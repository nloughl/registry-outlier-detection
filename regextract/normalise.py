"""Wide records -> the tidy long table handed to R (one row per device x time point)."""
from __future__ import annotations

import re

LONG_COLUMNS = [
    "registry", "country", "report_year", "data_period", "table_key", "table_id", "procedure",
    "pdf_file", "pdf_page", "source", "verification",
    "section", "row_type", "device_label", "femoral", "tibial", "manufacturer_femoral",
    "manufacturer_tibial", "compartment", "patella", "talar", "humeral", "ulnar", "diagnosis",
    "implant_class", "constraint",
    "n_total", "n_revised", "hospitals", "age_median", "age_q1", "age_q3", "mean_age", "male_pct", "ccs",
    "years_implanted",
    "time_yr", "estimate", "lcl", "ucl", "n_at_risk", "value_status", "low_at_risk",
    "metric_type", "metric_method", "ci_level", "population", "row_flags",
]

DEVICE_COLUMNS = [c for c in LONG_COLUMNS if c not in
                  ("time_yr", "estimate", "lcl", "ucl", "n_at_risk", "value_status", "low_at_risk")]

# case-mix tables (sex / age / design strata rather than devices)
CASEMIX_COLUMNS = [
    "registry", "country", "report_year", "data_period", "table_key", "table_id", "procedure",
    "pdf_file", "pdf_page", "source", "verification",
    "row_type", "stratum_label", "design_group", "design_subgroup", "implant_class", "fixation",
    "compartment", "constraint", "bearing", "sex", "age_group", "age_group_raw",
    "procedure_period", "period_start", "period_end", "diagnosis",
    "n_total", "n_revised",
    "time_yr", "estimate", "lcl", "ucl", "n_at_risk", "value_status", "low_at_risk",
    "metric_type", "metric_method", "ci_level", "population", "row_flags",
]

COLUMNS_BY_OUTPUT = {"device": LONG_COLUMNS, "casemix": CASEMIX_COLUMNS}


def expand_strata(rows: list[dict], spec: dict) -> list[tuple[dict, list[dict]]]:
    """Split rows whose strata sit side by side (NJR 3.K6: male | female) into one view per stratum.

    Returns [(row_view, time_columns)] where time_columns are column specs (with 'time') whose parsed
    values live in row_view[col['name']]. Tables without `strata` give one view per row."""
    tcols = [c for c in spec["columns"] if c.get("kind") == "est_ci"]
    strata = spec.get("strata")
    if not strata:
        return [(r, tcols) for r in rows]
    out = []
    for r in rows:
        for st in strata:
            sfx = st["suffix"]
            v = dict(r)
            v["sex"] = st.get("sex", v.get("sex"))
            for base in ("n_total", "n_revised"):
                v[base] = r.get(base + sfx)
            v["flags"] = [f for f in r.get("flags", []) if not re.match(r"^\w+_[mf]:", f) or f.split(":")[0].endswith(sfx)]
            out.append((v, [c for c in tcols if c["name"].endswith(sfx)]))
    return out


def keep_for_procedure(row: dict, spec: dict, procedure: str) -> bool:
    """Row filter for tables that mix procedures (e.g. EPRD lists TKA and UKA in one table)."""
    rule = (spec.get("procedures") or {}).get(procedure) or {}
    if row["row_type"] in rule.get("keep_row_types", []):
        return True
    secs, groups = rule.get("sections"), rule.get("groups")
    if secs and not any(re.search(p, row.get("section") or "", re.I) for p in secs):
        return False
    if groups and not any(re.search(p, row.get("design_group") or "", re.I) for p in groups):
        return False
    return True


def to_long(rows: list[dict], cfg: dict, spec: dict, procedure: str) -> list[dict]:
    metric = spec.get("metric", {})
    out = []
    for r, tcols in expand_strata(rows, spec):
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
            "talar": r.get("talar"), "humeral": r.get("humeral"), "ulnar": r.get("ulnar"),
            "diagnosis": r.get("diagnosis"),
            "n_total": r.get("n_total"), "n_revised": r.get("n_revised"), "hospitals": r.get("hospitals"),
            "age_median": r.get("age_median"), "age_q1": r.get("age_q1"), "age_q3": r.get("age_q3"),
            "mean_age": r.get("mean_age"),
            "male_pct": r.get("male_pct") if r.get("male_pct") is not None else r.get("sex_mf_a"),
            "ccs": r.get("ccs"), "years_implanted": r.get("years_implanted"),
            "metric_type": metric.get("type"), "metric_method": metric.get("method"),
            "ci_level": metric.get("ci_level"), "population": metric.get("population"),
            "row_flags": ";".join(r.get("flags", [])),
            # case-mix fields
            "stratum_label": r.get("device_label"), "design_group": r.get("design_group"),
            "design_subgroup": r.get("design_subgroup"), "implant_class": r.get("implant_class"),
            "fixation": r.get("fixation"), "constraint": r.get("constraint"), "bearing": r.get("bearing"),
            "sex": r.get("sex"), "age_group": r.get("age_group"), "age_group_raw": r.get("age_group_raw"),
            "procedure_period": r.get("procedure_period"), "period_start": r.get("period_start"),
            "period_end": r.get("period_end"),
        }
        # constant fields a table applies to every row (e.g. AOANJRR ET6 = diagnosis Fracture/Dislocation)
        for k, v in (spec.get("set_fields") or {}).items():
            if base.get(k) in (None, ""):
                base[k] = v
        for c in tcols:
            p = r.get(c["name"]) or {}
            out.append({**base, "time_yr": c["time"], "estimate": p.get("estimate"), "lcl": p.get("lcl"),
                        "ucl": p.get("ucl"), "n_at_risk": p.get("n_at_risk"), "value_status": p.get("status"),
                        "low_at_risk": bool(p.get("italic"))})
    return out
