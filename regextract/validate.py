"""Automated checks on extracted tables. Every issue is written to outputs/validation_report.csv."""
from __future__ import annotations

TOL = 1e-9


def _issue(rows, cfg, spec, sev, check, detail, device=None, time=None):
    rows.append({"registry": cfg["registry"], "report_year": cfg["report_year"], "table_key": spec["key"],
                 "severity": sev, "check": check, "device_label": device, "time_yr": time, "detail": detail})


def validate_table(rows: list[dict], diags: list[dict], cfg: dict, spec: dict) -> list[dict]:
    out: list[dict] = []
    tcols = [c for c in spec["columns"] if c.get("kind") == "est_ci"]

    for d in diags:
        if d.get("error"):
            _issue(out, cfg, spec, "error", "extraction", f"page {d.get('page')}: {d['error']}")
        if d.get("headers_missing"):
            _issue(out, cfg, spec, "warning", "headers", f"page {d.get('page')}: optional headers not found {d['headers_missing']}")
        if d.get("warning"):
            _issue(out, cfg, spec, "warning", "extraction", d["warning"])
        if d.get("ocr_vs_manual_differences"):
            _issue(out, cfg, spec, "info", "ocr_vs_manual",
                   f"{d['ocr_vs_manual_differences']} cells differ between OCR and manual copy (see review/)")

    exp = spec.get("expect", {})
    n_dev = sum(r["row_type"] == "device" for r in rows)
    if n_dev < exp.get("min_rows", 1):
        _issue(out, cfg, spec, "error", "row_count", f"{n_dev} device rows, expected >= {exp.get('min_rows', 1)}")

    for r in rows:
        dev = r.get("device_label")
        if r.get("verification") == "unverified":
            _issue(out, cfg, spec, "warning", "unverified_source", f"values from {r.get('source')} not yet verified", dev)
        if not dev:
            _issue(out, cfg, spec, "error", "label", "empty device label", dev)
        if r.get("n_total") is None and r["row_type"] in ("device", "other", "total"):
            _issue(out, cfg, spec, "warning", "n_total", "N missing", dev)
        if r.get("n_revised") is not None and r.get("n_total") is not None and r["n_revised"] > r["n_total"]:
            _issue(out, cfg, spec, "error", "n_revised<=n_total", f"{r['n_revised']} > {r['n_total']}", dev)
        for f in r.get("flags", []):
            if "unparsed" in f:
                _issue(out, cfg, spec, "error", "parse", f, dev)
        prev_est, prev_risk = None, None
        for c in tcols:
            p = r.get(c["name"]) or {}
            st = p.get("status", "blank")
            e, l, u = p.get("estimate"), p.get("lcl"), p.get("ucl")
            if st.startswith("unparsed") or st in ("ocr_unparsed", "ocr_ambiguous"):
                _issue(out, cfg, spec, "error", "parse", f"{st} raw='{p.get('raw')}'", dev, c["time"])
            elif st.startswith("ok_extra"):
                _issue(out, cfg, spec, "warning", "parse", st, dev, c["time"])
            if e is not None and l is not None and u is not None and not (l - TOL <= e <= u + TOL):
                _issue(out, cfg, spec, "error", "lcl<=est<=ucl", f"{l} / {e} / {u}", dev, c["time"])
            if e is not None and not (0 <= e <= 100):
                _issue(out, cfg, spec, "error", "range", f"estimate {e} outside 0-100", dev, c["time"])
            if e is not None:
                if prev_est is not None and e < prev_est - TOL:
                    _issue(out, cfg, spec, "error", "monotonic", f"{prev_est} -> {e}", dev, c["time"])
                prev_est = e
            rk = p.get("n_at_risk")
            if rk is not None:
                if prev_risk is not None and rk > prev_risk:
                    _issue(out, cfg, spec, "error", "at_risk_non_increasing", f"{prev_risk} -> {rk}", dev, c["time"])
                if r.get("n_total") is not None and rk > r["n_total"]:
                    _issue(out, cfg, spec, "error", "at_risk<=n_total", f"{rk} > {r['n_total']}", dev, c["time"])
                prev_risk = rk

        parts = (spec.get("checks") or {}).get("revision_type_sum")
        if parts and r.get("n_revised") is not None:
            vals = [r.get(p) for p in parts]
            if None not in vals and sum(vals) != r["n_revised"]:
                _issue(out, cfg, spec, "error", "revision_type_sum", f"{sum(vals)} != revisions {r['n_revised']}", dev)

    if exp.get("totals_check"):
        tot = [r for r in rows if r["row_type"] == "total"]
        parts = [r for r in rows if r["row_type"] in ("device", "other")]
        if len(tot) != 1:
            _issue(out, cfg, spec, "error", "totals", f"expected one total row, found {len(tot)}")
        else:
            for f in ("n_total", "n_revised"):
                s = sum(r.get(f) or 0 for r in parts)
                if tot[0].get(f) is not None and s != tot[0][f]:
                    _issue(out, cfg, spec, "error", "totals", f"sum of {f} = {s}, TOTAL row = {tot[0][f]}")
    return out
