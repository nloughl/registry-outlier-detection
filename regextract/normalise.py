"""Wide records -> the tidy long table handed to R (one row per device x time point)."""
from __future__ import annotations

import re

LONG_COLUMNS = [
    "registry", "country", "report_year", "data_period", "table_key", "table_id", "procedure",
    "pdf_file", "pdf_page", "source", "verification",
    "section", "row_type", "device_label", "femoral", "tibial", "manufacturer_femoral",
    "manufacturer_tibial", "compartment", "patella",
    "n_total", "n_revised", "hospitals", "age_median", "age_q1", "age_q3", "mean_age", "male_pct", "ccs",
    "years_implanted",
    "time_yr", "estimate", "lcl", "ucl", "n_at_risk", "value_status", "low_at_risk",
    "metric_type", "metric_method", "ci_level", "population", "row_flags",
]

DEVICE_COLUMNS = [c for c in LONG_COLUMNS if c not in
                  ("time_yr", "estimate", "lcl", "ucl", "n_at_risk", "value_status", "low_at_risk")]


def keep_for_procedure(row: dict, spec: dict, procedure: str) -> bool:
    """Row filter for tables that mix procedures (e.g. EPRD lists TKA and UKA in one table)."""
    rule = (spec.get("procedures") or {}).get(procedure) or {}
    if row["row_type"] in rule.get("keep_row_types", []):
        return True
    secs = rule.get("sections")
    if not secs:
        return True
    return any(re.search(p, row.get("section") or "", re.I) for p in secs)


def to_long(rows: list[dict], cfg: dict, spec: dict, procedure: str) -> list[dict]:
    metric = spec.get("metric", {})
    out = []
    for r in rows:
        base = {
            "registry": r["registry"], "country": cfg.get("country"), "report_year": r["report_year"],
            "data_period": cfg.get("data_period"), "table_key": r["table_key"], "table_id": r["table_id"],
            "procedure": procedure, "pdf_file": r["pdf_file"], "pdf_page": r["pdf_page"],
            "source": r.get("source"), "verification": r.get("verification"),
            "section": r.get("section"), "row_type": r["row_type"], "device_label": r.get("device_label"),
            "femoral": r.get("femoral"), "tibial": r.get("tibial"),
            "manufacturer_femoral": r.get("manufacturer_femoral"),
            "manufacturer_tibial": r.get("manufacturer_tibial"),
            "compartment": r.get("compartment"), "patella": r.get("patella"),
            "n_total": r.get("n_total"), "n_revised": r.get("n_revised"), "hospitals": r.get("hospitals"),
            "age_median": r.get("age_median"), "age_q1": r.get("age_q1"), "age_q3": r.get("age_q3"),
            "mean_age": r.get("mean_age"),
            "male_pct": r.get("male_pct") if r.get("male_pct") is not None else r.get("sex_mf_a"),
            "ccs": r.get("ccs"), "years_implanted": r.get("years_implanted"),
            "metric_type": metric.get("type"), "metric_method": metric.get("method"),
            "ci_level": metric.get("ci_level"), "population": metric.get("population"),
            "row_flags": ";".join(r.get("flags", [])),
        }
        for c in spec["columns"]:
            if c.get("kind") != "est_ci":
                continue
            p = r.get(c["name"]) or {}
            out.append({**base, "time_yr": c["time"], "estimate": p.get("estimate"), "lcl": p.get("lcl"),
                        "ucl": p.get("ucl"), "n_at_risk": p.get("n_at_risk"), "value_status": p.get("status"),
                        "low_at_risk": bool(p.get("italic"))})
    return out
