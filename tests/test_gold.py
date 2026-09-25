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
