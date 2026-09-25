"""Extract one configured table from its confirmed pages into wide records."""
from __future__ import annotations

import re
from pathlib import Path

import pdfplumber

from . import hooks
from .config import make_columns
from .grid import (build_records, cluster_lines, join_words, locate_columns, refine_label_bounds,
                   refine_label_numeric_bounds, set_bounds)
from .parse import parse_est_ci, parse_int, parse_median_iqr, parse_number, parse_ratio
from .words import Word, normalise_space, page_words


def _caption_box(words: list[Word], pattern: str | None, min_top: float = -1) -> tuple[float, float] | None:
    """(top, bottom) of the first line matching the caption pattern."""
    if not pattern:
        return None
    pat = re.compile(pattern.replace(" ", r"\s+"), re.I)
    lines = cluster_lines(words)
    for i, line in enumerate(lines):
        if line[0].top < min_top:
            continue
        # captions can wrap: test this line plus the next one
        this = normalise_space(join_words(line))
        both = normalise_space(this + " " + (join_words(lines[i + 1]) if i + 1 < len(lines) else ""))
        m = pat.search(both)
        if pat.search(this) or (m and m.start() < len(this)):   # caption must start on this line
            return min(w.top for w in line), max(w.bottom for w in line)
    return None


def _data_bottom(words: list[Word], top: float, end_patterns: list[str], default: float) -> float:
    for line in cluster_lines([w for w in words if w.top > top]):
        txt = join_words(line)
        if any(re.search(p, txt, re.I) for p in end_patterns):
            return min(w.top for w in line)
    return default


def records_from_words(words: list[Word], spec: dict, page_no: int, page_height: float,
                       page_width: float, section: str | None, is_first: bool) -> tuple[list, dict, str | None]:
    """Shared by text-layer pages and OCR'd images."""
    diag = {"page": page_no}
    x_min, x_max = spec.get("x_range", [-1e9, 1e9])
    words = [w for w in words if x_min <= w.xc <= x_max]
    cap = _caption_box(words, spec.get("caption") if is_first else spec.get("continuation") or spec.get("caption"))
    diag["caption_found"] = cap is not None
    below = spec.get("caption_position", "above") == "below"
    if cap is not None and not below:
        min_top = cap[1]
    else:
        min_top = spec.get("page_top_margin", 0)
    cols = make_columns(spec)
    cols, header_bottom = locate_columns(words, cols, min_top)
    missing = [c.name for c in cols if not c.found]
    diag["headers_missing"] = missing
    diag["headers_found"] = len(cols) - len(missing)
    required = spec.get("required_columns", [c.name for c in cols])
    if any(m in required for m in missing):
        diag["error"] = f"required headers not found: {[m for m in missing if m in required]}"
        return [], diag, section
    set_bounds(cols, right_edge=min(x_max, page_width), overrides=spec.get("column_bounds"))
    data_top = header_bottom + 0.5
    # absorb header continuation lines (e.g. '(95% CI)', 'N', 'age'): no pure numbers, nothing in label columns
    from .grid import column_of
    label_names = {c.name for c in cols if c.kind == "label"}
    for line in cluster_lines([w for w in words if w.top > data_top - 0.5]):
        if line[0].top - data_top > 18:
            break
        inside = [w for w in line if column_of(w, cols)]
        if not inside:
            continue
        has_num = any(re.match(r"^[\d,.]+[*†]?$", w.text) for w in inside)
        in_label = any(column_of(w, cols).name in label_names for w in inside)
        if has_num or in_label:
            break
        data_top = max(w.bottom for w in line) + 0.5
    data_bottom = _data_bottom(words, data_top, spec.get("end_patterns", []),
                               page_height - spec.get("footer_margin", 40))
    if below:
        cap = _caption_box(words, spec.get("caption") if is_first else spec.get("continuation") or spec.get("caption"),
                           min_top=data_top)
        diag["caption_found"] = cap is not None
        if cap is not None:
            data_bottom = min(data_bottom, cap[0])
    if not spec.get("column_bounds"):
        refine_label_bounds(cols, words, data_top, data_bottom)
        refine_label_numeric_bounds(cols, words, data_top, data_bottom)
    recs, section = build_records(
        words, cols, spec["anchor"], data_top, data_bottom,
        section_patterns=spec.get("section_patterns", []),
        ignore_patterns=spec.get("ignore_patterns", []),
        page_no=page_no, current_section=section,
        anchor_pattern=spec.get("anchor_pattern", r"^[\d,.]+[*†]?$"),
    )
    diag["records"] = len(recs)
    diag["_columns"] = cols
    return recs, diag, section


def to_wide(recs, spec: dict, cfg: dict, pdf_name: str) -> list[dict]:
    cols = make_columns(spec)
    out = []
    for r in recs:
        row = {
            "registry": cfg["registry"], "report_year": cfg["report_year"],
            "table_key": spec["key"], "table_id": spec.get("table_id"),
            "pdf_file": pdf_name, "pdf_page": r.page, "section": r.section,
            "row_type": "device", "flags": [], "min_ocr_conf": None,
            "source": "pdf_text", "verification": "not_needed",
        }
        confs = []
        for c in cols:
            txt = r.text(c.name)
            confs.append(r.min_conf(c.name))
            if c.kind == "label":
                row[c.name] = txt or None
            elif c.kind == "int":
                v, fl = parse_int(txt)
                row[c.name] = v
                row["flags"] += [f"{c.name}:{f}" for f in fl]
            elif c.kind == "est_ci":
                p = parse_est_ci(txt)
                row[c.name] = p
                p["italic"] = r.italic(c.name)
                p["raw"] = txt
            elif c.kind == "median_iqr":
                p = parse_median_iqr(txt)
                row[f"{c.name}_median"], row[f"{c.name}_q1"], row[f"{c.name}_q3"] = p["median"], p["q1"], p["q3"]
            elif c.kind == "ratio":
                row[f"{c.name}_a"], row[f"{c.name}_b"] = parse_ratio(txt)
            elif c.kind == "number":
                row[c.name] = parse_number(txt)
            else:
                row[c.name] = txt or None
        if min(confs, default=100) < 100:
            row["min_ocr_conf"] = min(confs)
        out.append(row)

    # fill-down of blank label cells (e.g. AOANJRR prints the femoral component once per group)
    for f in spec.get("fill_down", []):
        last = None
        for row in out:
            if row.get(f):
                last = row[f]
            elif last and any(row.get(c["name"]) for c in spec["columns"] if c.get("kind") == "label"):
                row[f] = last
                row["flags"].append(f"{f}_filled_down")

    hook = hooks.HOOKS[spec["label_hook"]]
    for row in out:
        hook(row)
        for rt, pat in (spec.get("row_types") or {}).items():
            if re.search(pat, row.get("device_label") or "", re.I):
                row["row_type"] = rt
    return out


def extract_text_table(pdf_path: Path, cfg: dict, spec: dict, pages: list[int]) -> tuple[list[dict], list[dict]]:
    all_recs, diags = [], []
    section = None
    with pdfplumber.open(pdf_path) as pdf:
        for i, pno in enumerate(pages):
            page = pdf.pages[pno - 1]
            recs, diag, section = records_from_words(
                page_words(page), spec, pno, page.height, page.width, section, is_first=(i == 0))
            diag.pop("_columns", None)
            diags.append(diag)
            all_recs.extend(recs)
    return to_wide(all_recs, spec, cfg, pdf_path.name), diags


# ---------------------------------------------------------------------------- image tables (OCR)

MANUAL_FIELDS_INT = ["n_total", "hospitals", "n_revised"]


def _time_cols(spec: dict) -> list[dict]:
    return [c for c in spec["columns"] if c.get("kind") == "est_ci"]


def rows_from_manual(path: Path, cfg: dict, spec: dict, pdf_name: str, page: int | None) -> list[dict]:
    import csv

    def num(v):
        v = (v or "").strip()
        return None if v in ("", "NA", "n.a.", "na") else float(v)

    out = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for m in csv.DictReader(f):
            row = {"registry": cfg["registry"], "report_year": cfg["report_year"], "table_key": spec["key"],
                   "table_id": spec.get("table_id"), "pdf_file": pdf_name, "pdf_page": page, "section": None,
                   "row_type": "device", "flags": [], "min_ocr_conf": None,
                   "source": "manual",
                   "verification": "verified" if (m.get("verified_by") or "").strip() else "unverified",
                   "femoral": (m.get("femoral") or "").strip() or None,
                   "tibial": (m.get("tibial") or "").strip() or None}
            for c in spec["columns"]:
                k = c.get("kind")
                if k == "int":
                    v = num(m.get(c["name"]))
                    row[c["name"]] = int(v) if v is not None else None
                elif k == "median_iqr":
                    for sfx in ("median", "q1", "q3"):
                        row[f"{c['name']}_{sfx}"] = num(m.get(f"{c['name']}_{sfx}"))
                elif k == "est_ci":
                    e, l, u = (num(m.get(f"{c['name']}_{s}")) for s in ("est", "lcl", "ucl"))
                    row[c["name"]] = {"estimate": e, "lcl": l, "ucl": u, "n_at_risk": None, "italic": False,
                                      "status": "ok" if e is not None else "not_reported", "raw": ""}
            if (m.get("n_total_marked") or "0").strip() in ("1", "TRUE", "true", "yes"):
                row["flags"].append("n_total:marked_*")
            if (m.get("note") or "").strip():
                row["flags"].append("note:" + m["note"].strip())
            out.append(row)
    hook = hooks.HOOKS[spec["label_hook"]]
    for row in out:
        hook(row)
        for rt, pat in (spec.get("row_types") or {}).items():
            if re.search(pat, row.get("device_label") or "", re.I):
                row["row_type"] = rt
    return out


def ocr_rows_to_manual_csv(rows: list[dict], spec: dict, path: Path) -> None:
    """Write OCR output in the manual/ CSV layout, so it can be corrected and saved as the verified copy."""
    import csv
    fields = ["femoral", "tibial", "n_total", "n_total_marked"]
    for c in spec["columns"]:
        if c["name"] in ("femoral", "tibial", "n_total"):
            continue
        if c.get("kind") == "median_iqr":
            fields += [f"{c['name']}_median", f"{c['name']}_q1", f"{c['name']}_q3"]
        elif c.get("kind") == "est_ci":
            fields += [f"{c['name']}_{s}" for s in ("est", "lcl", "ucl")]
        else:
            fields.append(c["name"])
    fields += ["source", "verified_by", "verified_on", "note", "ocr_min_conf", "ocr_problems"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            out = {k: r.get(k) for k in fields if k in r}
            out["n_total_marked"] = int(any(fl == "n_total:marked_*" for fl in r["flags"]))
            probs = []
            for c in _time_cols(spec):
                p = r[c["name"]]
                for s, key in (("est", "estimate"), ("lcl", "lcl"), ("ucl", "ucl")):
                    out[f"{c['name']}_{s}"] = "NA" if p["status"] == "not_reported" else p[key]
                if p["status"] not in ("ocr_ok", "not_reported"):
                    probs.append(f"{c['name']}:{p['status']}:{p['raw']}")
            out["source"] = "ocr (unverified)"
            out["ocr_min_conf"] = r.get("min_ocr_conf")
            out["ocr_problems"] = " | ".join(probs)
            w.writerow(out)


def compare_ocr_manual(ocr_rows: list[dict], man_rows: list[dict], spec: dict) -> list[dict]:
    """Cell-by-cell comparison of OCR against the manual copy (matched by row order)."""
    diffs = []
    for i, (o, m) in enumerate(zip(ocr_rows, man_rows)):
        for c in spec["columns"]:
            n, k = c["name"], c.get("kind")
            if k == "int":
                pairs = [(n, o.get(n), m.get(n))]
            elif k == "median_iqr":
                pairs = [(f"{n}_{s}", o.get(f"{n}_{s}"), m.get(f"{n}_{s}")) for s in ("median", "q1", "q3")]
            elif k == "est_ci":
                pairs = [(f"{n}_{s}", o[n][key], m[n][key]) for s, key in (("est", "estimate"), ("lcl", "lcl"), ("ucl", "ucl"))]
            else:
                continue
            for name, ov, mv in pairs:
                if (ov is None) != (mv is None) or (ov is not None and abs(float(ov) - float(mv)) > 1e-9):
                    diffs.append({"row": i + 1, "device": m.get("device_label"), "field": name, "ocr": ov, "manual": mv})
    if len(ocr_rows) != len(man_rows):
        diffs.append({"row": None, "device": None, "field": "row_count", "ocr": len(ocr_rows), "manual": len(man_rows)})
    return diffs


def extract_ocr_table(pdf_path: Path, cfg: dict, spec: dict, pages: list[int],
                      review_dir: Path, manual_dir: Path, run_ocr: bool = True) -> tuple[list[dict], list[dict]]:
    import csv
    from . import ocr

    stem = f"{cfg['registry']}_{cfg['report_year']}_{spec['key']}"
    page = pages[0] if pages else None
    manual_path = manual_dir / f"{stem}.csv"
    diag = {"page": page, "source": None}
    ocr_rows = None
    if run_ocr and page and ocr.tesseract_available():
        im = ocr.find_table_image(pdf_path, page, spec["ocr"]["heading"])
        review_dir.mkdir(parents=True, exist_ok=True)
        im.save(review_dir / f"{stem}_image.png")
        raw, d = ocr.ocr_table(im, spec)
        ocr.overlay(im, spec, raw, review_dir / f"{stem}_grid_check.png")
        diag.update({"ocr_records": d["records"]})
        hook = hooks.HOOKS[spec["label_hook"]]
        ocr_rows = []
        for r in raw:
            r.update({"registry": cfg["registry"], "report_year": cfg["report_year"], "table_key": spec["key"],
                      "table_id": spec.get("table_id"), "pdf_file": pdf_path.name, "pdf_page": page,
                      "section": None, "row_type": "device", "source": "ocr", "verification": "unverified"})
            for c in _time_cols(spec):
                r[c["name"]].setdefault("n_at_risk", None)
                r[c["name"]].setdefault("italic", False)
            hook(r)
            for rt, pat in (spec.get("row_types") or {}).items():
                if re.search(pat, r.get("device_label") or "", re.I):
                    r["row_type"] = rt
            ocr_rows.append(r)
        ocr_rows_to_manual_csv(ocr_rows, spec, review_dir / f"{stem}_ocr.csv")
    elif run_ocr and not ocr.tesseract_available():
        diag["warning"] = "Tesseract not installed: OCR skipped"

    if manual_path.exists():
        rows = rows_from_manual(manual_path, cfg, spec, pdf_path.name, page)
        diag["source"] = f"manual ({rows[0]['verification'] if rows else 'empty'})"
        if ocr_rows is not None:
            diffs = compare_ocr_manual(ocr_rows, rows, spec)
            diag["ocr_vs_manual_differences"] = len(diffs)
            with open(review_dir / f"{stem}_ocr_vs_manual.csv", "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=["row", "device", "field", "ocr", "manual"])
                w.writeheader()
                w.writerows(diffs)
        return rows, [diag]
    if ocr_rows is not None:
        diag["source"] = "ocr (unverified) - correct review CSV and save it to manual/ to verify"
        for r in ocr_rows:
            r["flags"].append("ocr_unverified")
        return ocr_rows, [diag]
    diag["error"] = f"no manual file {manual_path.name} and OCR unavailable"
    return [], [diag]
