"""Regression test: pipeline output must reproduce independently checked values.

Needs the report PDFs in pdfs/ and confirmed pages in config/page_map.yaml
(run `python -m regextract run --procedure UKA` first)."""
import csv
from pathlib import Path

import pytest

from regextract.config import OUT_DIR, ROOT

GOLD = ROOT / "tests" / "gold" / "UKA_gold_values.csv"
LONG = OUT_DIR / "UKA" / "UKA_long.csv"


@pytest.mark.skipif(not LONG.exists(), reason="run the pipeline first")
def test_gold_values():
    with open(LONG, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    with open(GOLD, newline="", encoding="utf-8") as f:
        gold = list(csv.DictReader(f))
    missing, wrong = [], []
    for g in gold:
        hits = [r for r in rows if r["registry"] == g["registry"] and r["table_key"] == g["table_key"]
                and r["device_label"] == g["device_label"] and float(r["time_yr"]) == float(g["time_yr"])
                and (not g["n_total"] or r["n_total"] and int(float(r["n_total"])) == int(g["n_total"]))]
        if not hits:
            missing.append(g["device_label"])
            continue
        r = hits[0]
        for k in ("estimate", "lcl", "ucl"):
            if abs(float(r[k]) - float(g[k])) > 1e-9:
                wrong.append((g["device_label"], g["time_yr"], k, r[k], g[k]))
    assert not missing, f"gold rows not found: {missing}"
    assert not wrong, f"mismatches: {wrong}"


CM_GOLD = ROOT / "tests" / "gold" / "UKA_casemix_gold_values.csv"
CM_LONG = OUT_DIR / "UKA" / "UKA_casemix_long.csv"


@pytest.mark.skipif(not CM_LONG.exists(), reason="run the pipeline first")
def test_casemix_gold_values():
    with open(CM_LONG, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    with open(CM_GOLD, newline="", encoding="utf-8") as f:
        gold = list(csv.DictReader(f))
    missing, wrong = [], []
    for g in gold:
        hits = [r for r in rows if r["registry"] == g["registry"] and r["table_key"] == g["table_key"]
                and r["stratum_label"] == g["stratum_label"] and r["sex"] == g["sex"]
                and r["age_group"] == g["age_group"] and float(r["time_yr"]) == float(g["time_yr"])]
        if not hits:
            missing.append((g["stratum_label"], g["sex"], g["age_group"]))
            continue
        r = hits[0]
        if int(float(r["n_total"])) != int(g["n_total"]):
            wrong.append((g["stratum_label"], "n_total", r["n_total"], g["n_total"]))
        for k in ("estimate", "lcl", "ucl"):
            if abs(float(r[k]) - float(g[k])) > 1e-9:
                wrong.append((g["stratum_label"], g["sex"], g["age_group"], g["time_yr"], k, r[k], g[k]))
    assert not missing, f"gold rows not found: {missing}"
    assert not wrong, f"mismatches: {wrong}"


RARE_GOLD = ROOT / "tests" / "gold" / "RARE_gold_values.csv"


@pytest.mark.parametrize("procedure", ["ANKLE", "ELBOW"])
def test_rare_joint_gold_values(procedure):
    files = {"device": OUT_DIR / procedure / f"{procedure}_long.csv",
             "casemix": OUT_DIR / procedure / f"{procedure}_casemix_long.csv"}
    if not all(f.exists() for f in files.values()):
        pytest.skip("run the pipeline first")
    rows = {}
    for kind, f in files.items():
        with open(f, newline="", encoding="utf-8") as fh:
            rows[kind] = list(csv.DictReader(fh))
    with open(RARE_GOLD, newline="", encoding="utf-8") as f:
        gold = [g for g in csv.DictReader(f) if g["procedure"] == procedure]
    missing, wrong = [], []
    for g in gold:
        label_col = "device_label" if g["kind"] == "device" else "stratum_label"
        hits = [r for r in rows[g["kind"]] if r["registry"] == g["registry"] and r["table_key"] == g["table_key"]
                and r[label_col] == g["label"] and float(r["time_yr"]) == float(g["time_yr"])]
        if not hits:
            missing.append((g["registry"], g["label"], g["time_yr"]))
            continue
        r = hits[0]
        if int(float(r["n_total"])) != int(g["n_total"]):
            wrong.append((g["label"], "n_total", r["n_total"], g["n_total"]))
        for k in ("estimate", "lcl", "ucl"):
            if abs(float(r[k]) - float(g[k])) > 1e-9:
                wrong.append((g["label"], g["time_yr"], k, r[k], g[k]))
    assert not missing, f"gold rows not found: {missing}"
    assert not wrong, f"mismatches: {wrong}"
