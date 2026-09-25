"""Tables printed as images (LROI online report): deterministic OCR with Tesseract.

Pipeline for one image table:
  1. pull the embedded image out of the PDF at native resolution (pypdfium2) - the first image
     below a heading found in the text layer (`locate_text`)
  2. whole-image OCR -> words (for row labels and row anchors)
  3. column edges come from the config (pixel x-positions in the native image, checked once per
     report year with the overlay image written to review/)
  4. each numeric cell is re-OCR'd on its own crop with a digit whitelist
  5. format-aware repair: LROI prints estimates/CI bounds with exactly 2 decimals, so a dropped
     decimal point ('(534-603)') can be restored; the lcl <= est <= ucl constraint picks the split

OCR output is never trusted blindly: every OCR'd table is written to review/ as a CSV next to a
crop of the image. A verified copy placed in manual/ takes precedence over OCR (see extract_ocr_table).
"""
from __future__ import annotations

import itertools
import re
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from PIL import Image, ImageDraw, ImageFilter

from .grid import Column, Record, cluster_lines
from .words import Word, normalise_space, page_words

try:
    import pytesseract
except ImportError:  # pragma: no cover
    pytesseract = None

CONFUSABLE = str.maketrans({"O": "0", "o": "0", "°": "0", "D": "0", "S": "5", "s": "5", "$": "5",
                            "l": "1", "I": "1", "|": "1", "T": "1", "B": "8", "&": "8", "€": "6",
                            "G": "6", "Z": "2", "z": "2", "q": "9", "g": "9"})


def _configure_tesseract() -> None:
    """Honour TESSERACT_CMD, else try the default Windows install location."""
    import os
    cmd = os.environ.get("TESSERACT_CMD")
    win = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd
    elif os.name == "nt" and win.exists():
        pytesseract.pytesseract.tesseract_cmd = str(win)


def tesseract_available() -> bool:
    if pytesseract is None:
        return False
    _configure_tesseract()
    try:
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def page_images(pdf_path: Path, page_no: int) -> list[tuple[tuple[float, float, float, float], Image.Image]]:
    """[(bbox top-down (x0, top, x1, bottom), PIL image)] for embedded images on a page."""
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        page = pdf[page_no - 1]
        h = page.get_height()
        out = []
        for obj in page.get_objects():
            if obj.type == pdfium_c.FPDF_PAGEOBJ_IMAGE:
                l, b, r, t = obj.get_bounds()
                out.append(((l, h - t, r, h - b), obj.get_bitmap(render=False).to_pil().convert("RGB")))
        return sorted(out, key=lambda x: x[0][1])
    finally:
        pdf.close()


def find_table_image(pdf_path: Path, page_no: int, heading: str) -> Image.Image:
    with pdfplumber.open(pdf_path) as pdf:
        words = page_words(pdf.pages[page_no - 1])
    pat = re.compile(heading.replace(" ", r"\s+"), re.I)
    y = None
    for line in cluster_lines(words):
        if pat.search(normalise_space(" ".join(w.text for w in line))):
            y = max(w.bottom for w in line)
            break
    if y is None:
        raise ValueError(f"heading '{heading}' not found on page {page_no}")
    for bbox, im in page_images(pdf_path, page_no):
        if bbox[1] >= y - 5:
            return im
    raise ValueError(f"no image below heading '{heading}' on page {page_no}")


def _prep(im: Image.Image, scale: int) -> Image.Image:
    g = im.convert("L")
    g = g.resize((g.width * scale, g.height * scale), Image.BICUBIC)
    return g.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=2))


def ocr_words(im: Image.Image, scale: int = 3, psm: int = 6, whitelist: str | None = None) -> list[Word]:
    cfg = f"--psm {psm}"
    if whitelist:
        cfg += f" -c tessedit_char_whitelist={whitelist}"
    d = pytesseract.image_to_data(_prep(im, scale), config=cfg, output_type=pytesseract.Output.DICT)
    out = []
    for i, t in enumerate(d["text"]):
        if t.strip():
            x0, y0 = d["left"][i] / scale, d["top"][i] / scale
            out.append(Word(t, x0, x0 + d["width"][i] / scale, y0, y0 + d["height"][i] / scale,
                            conf=float(d["conf"][i])))
    return out


# ------------------------------------------------------------------ format-aware repair

def _digits(s: str) -> str:
    return re.sub(r"\D", "", s.translate(CONFUSABLE))


def repair_est_ci(text: str, decimals: int = 2, max_value: float = 100.0) -> dict:
    """Recover 'est (lcl-ucl)' from OCR text where decimal points/brackets may be lost."""
    out = {"estimate": None, "lcl": None, "ucl": None, "n_at_risk": None, "status": "blank", "raw": text}
    t = text.strip()
    if not t:
        return out
    if re.search(r"n\.?\s*a", t, re.I) and not re.search(r"\d", t):
        out["status"] = "not_reported"
        return out
    nums = re.findall(r"\d+\.\d+", t)
    if len(nums) == 3 and all(len(n.split(".")[1]) == decimals for n in nums):
        e, l, u = map(float, nums)
        if l <= e <= u:
            out.update(estimate=e, lcl=l, ucl=u, status="ocr_ok")
            return out
    d = _digits(t)
    cands = []
    for lens in itertools.product(range(decimals + 1, decimals + 3), repeat=3):
        if sum(lens) != len(d):
            continue
        parts, i = [], 0
        for n in lens:
            parts.append(d[i:i + n])
            i += n
        if any(len(p) > decimals + 1 and p[0] == "0" for p in parts):
            continue
        e, l, u = (int(p) / 10 ** decimals for p in parts)
        if l <= e <= u < max_value:
            cands.append((e, l, u))
    if len(cands) == 1:
        e, l, u = cands[0]
        out.update(estimate=e, lcl=l, ucl=u, status="ocr_repaired")
    else:
        out["status"] = "ocr_ambiguous" if cands else "ocr_unparsed"
    return out


def repair_int(text: str) -> int | None:
    d = _digits(text)
    return int(d) if d else None


def repair_median_iqr(text: str) -> dict:
    d = re.findall(r"\d+", text.translate(CONFUSABLE))
    if len(d) >= 3:
        return {"median": float(d[0]), "q1": float(d[1]), "q3": float(d[2])}
    return {"median": None, "q1": None, "q3": None}


# ------------------------------------------------------------------ table assembly

def ocr_table(im: Image.Image, spec: dict) -> tuple[list[dict], dict]:
    """Returns wide-ish row dicts (labels + parsed cells + raw text) and diagnostics."""
    o = spec["ocr"]
    edges = o["column_edges"]  # len(columns) - 1 boundaries, native-pixel x
    cols = spec["columns"]
    bounds = list(zip([-1e9] + edges, edges + [1e9]))
    words = ocr_words(im, scale=o.get("scale", 3))
    top, bottom = o["data_top"], o.get("data_bottom", im.height)
    words = [w for w in words if top <= w.yc <= bottom]
    for pat in spec.get("end_patterns", []):
        for line in cluster_lines(words):
            if re.search(pat, " ".join(w.text for w in line), re.I):
                bottom = min(bottom, min(w.top for w in line))
                break
    words = [w for w in words if w.yc < bottom]

    def col_idx(w: Word) -> int:
        for i, (lo, hi) in enumerate(bounds):
            if lo <= w.xc < hi:
                return i
        return -1

    a_idx = [c["name"] for c in cols].index(spec["anchor"])
    anchors = sorted(w.yc for w in words if col_idx(w) == a_idx and re.search(r"\d", w.text))
    ys = []
    for y in anchors:
        if not ys or y - ys[-1] > 6:
            ys.append(y)
    # record bands: halfway between consecutive anchors
    mids = [top] + [(a + b) / 2 for a, b in zip(ys, ys[1:])] + [bottom]
    rows, diag = [], {"records": len(ys), "anchors_y": [round(y) for y in ys]}
    for k, y in enumerate(ys):
        y0, y1 = mids[k], mids[k + 1]
        row = {"_band": (y0, y1), "flags": []}
        confs = []
        for i, c in enumerate(cols):
            lo, hi = bounds[i]
            if c.get("kind") == "label":
                ws = [w for w in words if y0 <= w.yc < y1 and col_idx(w) == i]
                row[c["name"]] = normalise_space(" ".join(w.text for w in sorted(ws, key=lambda w: (round(w.top / 4), w.x0)))) or None
                confs += [w.conf for w in ws]
                continue
            crop = im.crop((max(0, lo + 1), max(0, y0), min(im.width, hi - 1), min(im.height, y1)))
            cw = ocr_words(crop, scale=o.get("cell_scale", 4), psm=6, whitelist="0123456789.,-()na*")
            txt = normalise_space(" ".join(w.text for w in sorted(cw, key=lambda w: (round(w.top / 4), w.x0))))
            confs += [w.conf for w in cw]
            if c.get("kind") == "est_ci":
                row[c["name"]] = repair_est_ci(txt)
            elif c.get("kind") == "int":
                if "*" in txt:
                    row["flags"].append(f"{c['name']}:marked_*")
                row[c["name"]] = repair_int(txt)
                row[f"_raw_{c['name']}"] = txt
            elif c.get("kind") == "median_iqr":
                p = repair_median_iqr(txt)
                row[f"{c['name']}_median"], row[f"{c['name']}_q1"], row[f"{c['name']}_q3"] = p["median"], p["q1"], p["q3"]
                row[f"_raw_{c['name']}"] = txt
            else:
                row[c["name"]] = txt or None
        row["min_ocr_conf"] = min(confs) if confs else None
        rows.append(row)
    return rows, diag


def overlay(im: Image.Image, spec: dict, rows: list[dict], path: Path) -> None:
    """Draw configured column edges and detected row bands for visual checking."""
    o = spec["ocr"]
    big = im.convert("RGB").resize((im.width * 2, im.height * 2), Image.BICUBIC)
    dr = ImageDraw.Draw(big)
    for x in o["column_edges"]:
        dr.line([(x * 2, 0), (x * 2, big.height)], fill=(220, 0, 0), width=1)
    for r in rows:
        y0, y1 = r["_band"]
        dr.rectangle([(2, y0 * 2), (big.width - 2, y1 * 2)], outline=(0, 90, 220), width=1)
    dr.line([(0, o["data_top"] * 2), (big.width, o["data_top"] * 2)], fill=(0, 160, 0), width=2)
    big.save(path)
