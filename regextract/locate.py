"""Find the pages holding each configured table and record them for human confirmation.

Stage 1 (fast): scan every page's text layer with pypdfium2 for the caption / continuation
patterns, skipping table-of-contents pages (dot leaders) but recording them as hints.
Stage 2 (precise): on candidate pages, check the configured column headers are present
(pdfplumber word positions). A table's pages = caption page + following pages that
repeat the header, until another caption or the header disappears.

Results go to outputs/locate_report.csv and config/page_map.yaml. Pages in page_map.yaml
must be confirmed (status: confirmed) before `extract` uses them - that is the checkpoint.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium
import yaml

from .config import PAGE_MAP, make_columns, table_ref
from .grid import locate_columns
from .words import normalise_space, words_for

TOC_RE = re.compile(r"(?:\.\s?){6,}\s*\d+")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def page_texts(pdf_path: Path) -> list[str]:
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        return [normalise_space(pdf[i].get_textpage().get_text_range()) for i in range(len(pdf))]
    finally:
        pdf.close()


def _pat(p: str | None):
    return re.compile(p.lstrip("^").replace(" ", r"\s+"), re.I) if p else None


def header_score(page, spec: dict) -> tuple[float, list[str]]:
    cols = make_columns(spec)
    cols, _ = locate_columns(words_for(page, spec), cols)
    missing = [c.name for c in cols if not c.found and c.name not in (spec.get("fixed_columns") or {})]
    return 1 - len(missing) / len(cols), missing


def locate_table(pdf_path: Path, cfg: dict, spec: dict, texts: list[str] | None = None) -> dict:
    texts = texts or page_texts(pdf_path)
    cap, cont = _pat(spec.get("caption_text") or spec.get("caption")), _pat(spec.get("continuation"))
    stop = _pat(spec.get("stop_caption"))
    locate_by = _pat(spec.get("locate_text"))  # OCR tables: a heading in the text layer
    # optional chapter scope, e.g. LROI repeats 'By cemented component name' in the TKA and UKA chapters
    lo, hi = 0, len(texts)
    if spec.get("section_start"):
        s_pat, e_pat = _pat(spec["section_start"]), _pat(spec.get("section_end"))
        for i, t in enumerate(texts):
            if s_pat.search(t) and len(TOC_RE.findall(t)) < 3:
                lo = i
                break
        if e_pat:
            for i in range(lo + 1, len(texts)):
                if e_pat.search(texts[i]):
                    hi = i
                    break
    toc_hints, starts = [], []
    for i, t in enumerate(texts):
        if not (lo <= i <= hi):
            continue
        hit = (cap and cap.search(t)) or (locate_by and locate_by.search(t))
        if not hit:
            continue
        if len(TOC_RE.findall(t)) >= 3:
            toc_hints.append(i + 1)
        else:
            starts.append(i + 1)

    rows, pages = [], []
    if spec.get("source") == "ocr_image":
        # headers live inside the image, so the text-layer heading is all we can check here
        for p in starts:
            rows.append({"page": p, "role": "start", "header_score": None, "headers_missing": ""})
        pages = starts[:1]
    else:
        with pdfplumber.open(pdf_path) as pdf:
            best = None
            for p in starts:
                score, missing = header_score(pdf.pages[p - 1], spec)
                rows.append({"page": p, "role": "caption", "header_score": round(score, 2),
                             "headers_missing": ";".join(missing)})
                if score >= spec.get("min_header_score", 0.7) and (best is None or score > best[1]):
                    best = (p, score)
            if best:
                pages = [best[0]]
                p = best[0] + 1
                while p <= len(pdf.pages) and p - best[0] <= spec.get("max_pages", 30):
                    t = texts[p - 1]
                    has_cont = bool(cont and cont.search(t))
                    if stop and stop.search(t) and not has_cont:
                        break
                    score, missing = header_score(pdf.pages[p - 1], spec)
                    is_cont = has_cont or score >= 0.8
                    if not is_cont or score < spec.get("min_header_score", 0.7):
                        break
                    pages.append(p)
                    rows.append({"page": p, "role": "continuation", "header_score": round(score, 2),
                                 "headers_missing": ";".join(missing)})
                    p += 1
    for r in rows:
        r["selected"] = r["page"] in pages
    return {"pages": pages, "candidates": rows, "toc_hints": toc_hints}


# ---------------------------------------------------------------- page map (the checkpoint)

def load_page_map() -> dict:
    if PAGE_MAP.exists():
        return yaml.safe_load(PAGE_MAP.read_text(encoding="utf-8")) or {}
    return {}


def save_page_map(pm: dict) -> None:
    header = ("# Pages for each registry table. Written by `locate`; set status to 'confirmed'\n"
              "# (or run `python -m regextract confirm`) after checking the pages in the PDF.\n"
              "# A changed PDF (different sha256) is re-located and needs re-confirming.\n")
    PAGE_MAP.write_text(header + yaml.safe_dump(pm, sort_keys=True, allow_unicode=True), encoding="utf-8")


def propose(pm: dict, cfg: dict, spec: dict, pdf_path: Path, found: dict, digest: str) -> str:
    ref = table_ref(cfg, spec)
    old = pm.get(ref)
    if old and old.get("sha256") == digest and old.get("status") == "confirmed":
        if old.get("pages") != found["pages"]:
            return f"kept confirmed pages {old['pages']} (locator now suggests {found['pages']})"
        return "confirmed (unchanged)"
    pm[ref] = {
        "pdf": pdf_path.name,
        "sha256": digest,
        "table_id": spec.get("table_id"),
        "pages": found["pages"],
        "status": "proposed" if found["pages"] else "not_found",
        "located_on": dt.date.today().isoformat(),
    }
    return pm[ref]["status"]
