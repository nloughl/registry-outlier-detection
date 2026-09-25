"""locate -> (confirm) -> extract -> normalise -> validate -> write outputs."""
from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

from . import config as C
from .extract import extract_ocr_table, extract_text_table
from .locate import load_page_map, locate_table, page_texts, propose, save_page_map, sha256
from .normalise import DEVICE_COLUMNS, LONG_COLUMNS, keep_for_procedure, to_long
from .validate import validate_table


def _write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields = []
        for r in rows:
            for k in r:
                if k not in fields:
                    fields.append(k)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in fields})


def flatten_wide(rows: list[dict], spec: dict) -> list[dict]:
    out = []
    for r in rows:
        flat = {}
        for k, v in r.items():
            if k.startswith("_"):
                continue
            if isinstance(v, dict) and "estimate" in v:
                for sub, key in (("est", "estimate"), ("lcl", "lcl"), ("ucl", "ucl"), ("at_risk", "n_at_risk"),
                                 ("status", "status"), ("low_at_risk", "italic"), ("raw", "raw")):
                    flat[f"{k}_{sub}"] = v.get(key)
            elif isinstance(v, list):
                flat[k] = ";".join(map(str, v))
            else:
                flat[k] = v
        out.append(flat)
    return out


# ---------------------------------------------------------------------------------------- locate

def cmd_locate(procedure: str, registries: list[str] | None, year: int | None, log=print) -> list[dict]:
    pm = load_page_map()
    report = []
    for cfg in C.load_registry_configs(registries, year):
        specs = C.tables_for(cfg, procedure)
        if not specs:
            continue
        try:
            pdf = C.resolve_pdf(cfg)
        except (FileNotFoundError, ValueError) as e:
            log(f"[{cfg['registry']}] {e}")
            continue
        log(f"[{cfg['registry']} {cfg['report_year']}] scanning {pdf.name} ...")
        texts, digest = page_texts(pdf), sha256(pdf)
        for spec in specs:
            found = locate_table(pdf, cfg, spec, texts)
            status = propose(pm, cfg, spec, pdf, found, digest)
            ref = C.table_ref(cfg, spec)
            log(f"    {ref}: pages {found['pages']} -> {status}"
                + (f" (TOC mentions on pages {found['toc_hints']})" if found["toc_hints"] else ""))
            for cand in found["candidates"] or [{"page": None, "role": "none", "header_score": None,
                                                  "headers_missing": "", "selected": False}]:
                report.append({"table": ref, "table_id": spec.get("table_id"), "pdf": pdf.name, **cand,
                               "toc_hint_pages": ";".join(map(str, found["toc_hints"]))})
    save_page_map(pm)
    _write_csv(C.OUT_DIR / "locate_report.csv", report)
    return report


def cmd_confirm(refs: list[str] | None, log=print) -> None:
    pm = load_page_map()
    for ref, entry in pm.items():
        if (not refs or ref in refs) and entry.get("status") == "proposed":
            entry["status"] = "confirmed"
            entry["confirmed_on"] = dt.date.today().isoformat()
            log(f"confirmed {ref}: pages {entry['pages']}")
    save_page_map(pm)


# --------------------------------------------------------------------------------------- extract

def cmd_extract(procedure: str, registries: list[str] | None, year: int | None, auto_confirm: bool = False,
                xlsx: bool = False, run_ocr: bool = True, log=print) -> dict:
    pm = load_page_map()
    out_dir = C.OUT_DIR / procedure
    all_long, all_dev, issues, extraction_log = [], [], [], []
    for cfg in C.load_registry_configs(registries, year):
        for spec in C.tables_for(cfg, procedure):
            ref = C.table_ref(cfg, spec)
            entry = pm.get(ref)
            try:
                pdf = C.resolve_pdf(cfg)
            except (FileNotFoundError, ValueError) as e:
                log(f"[skip] {ref}: {e}")
                continue
            if not entry or not entry.get("pages"):
                log(f"[skip] {ref}: not located yet - run `locate` first")
                continue
            if entry.get("sha256") and entry["sha256"] != sha256(pdf):
                log(f"[skip] {ref}: PDF changed since pages were confirmed - run `locate` again")
                continue
            if entry.get("status") != "confirmed" and not auto_confirm:
                log(f"[skip] {ref}: pages {entry['pages']} are '{entry.get('status')}' - check them, then `confirm`")
                continue
            if spec.get("source") == "ocr_image":
                rows, diags = extract_ocr_table(pdf, cfg, spec, entry["pages"], C.REVIEW_DIR, C.MANUAL_DIR, run_ocr)
            else:
                rows, diags = extract_text_table(pdf, cfg, spec, entry["pages"])
            rows = [r for r in rows if keep_for_procedure(r, spec, procedure)]
            for d in diags:
                extraction_log.append({"table": ref, **{k: v for k, v in d.items() if not k.startswith("_")}})
            iss = validate_table(rows, diags, cfg, spec)
            issues += iss
            long = to_long(rows, cfg, spec, procedure)
            all_long += long
            seen = set()
            for lr in long:
                key = (lr["table_key"], lr["device_label"], lr["section"], lr["n_total"])
                if key not in seen:
                    seen.add(key)
                    all_dev.append({k: lr.get(k) for k in DEVICE_COLUMNS})
            wide = flatten_wide(rows, spec)
            _write_csv(out_dir / "tables" / f"{cfg['registry']}_{cfg['report_year']}_{spec['key']}_wide.csv", wide)
            n_err = sum(i["severity"] == "error" for i in iss)
            n_warn = sum(i["severity"] == "warning" for i in iss)
            src = {r.get("source") for r in rows}
            log(f"[ok] {ref}: {len(rows)} rows from pages {entry['pages']} ({', '.join(sorted(map(str, src)))}); "
                f"{n_err} errors, {n_warn} warnings")
    _write_csv(out_dir / f"{procedure}_long.csv", all_long, LONG_COLUMNS)
    _write_csv(out_dir / f"{procedure}_devices.csv", all_dev, DEVICE_COLUMNS)
    _write_csv(out_dir / "validation_report.csv", issues,
               ["registry", "report_year", "table_key", "severity", "check", "device_label", "time_yr", "detail"])
    _write_csv(out_dir / "extraction_log.csv", extraction_log)
    if xlsx:
        write_xlsx(out_dir, procedure)
    return {"long": all_long, "devices": all_dev, "issues": issues}


def write_xlsx(out_dir: Path, procedure: str) -> Path:
    import pandas as pd
    path = out_dir / f"{procedure}_extraction.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        for name in (f"{procedure}_long", f"{procedure}_devices", "validation_report", "extraction_log"):
            f = out_dir / f"{name}.csv"
            if f.exists() and f.stat().st_size > 0:
                pd.read_csv(f).to_excel(xw, sheet_name=name[:31], index=False)
        for f in sorted((out_dir / "tables").glob("*_wide.csv")):
            pd.read_csv(f).to_excel(xw, sheet_name=f.stem.replace("_wide", "")[:31], index=False)
    return path
