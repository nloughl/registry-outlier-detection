"""Load registry/report-year YAML configs."""
from __future__ import annotations

import copy
import glob
from pathlib import Path

import yaml

from .grid import Column

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
PDF_DIR = ROOT / "pdfs"
OUT_DIR = ROOT / "outputs"
REVIEW_DIR = ROOT / "review"
MANUAL_DIR = ROOT / "manual"
PAGE_MAP = CONFIG_DIR / "page_map.yaml"


def load_registry_configs(registries: list[str] | None = None, year: int | None = None) -> list[dict]:
    """All config/registries/*.yaml, optionally filtered. If no year is given, the latest per registry."""
    cfgs = []
    for f in sorted((CONFIG_DIR / "registries").glob("*.yaml")):
        cfg = yaml.safe_load(f.read_text(encoding="utf-8"))
        cfg["_file"] = f.name
        cfgs.append(cfg)
    if registries:
        wanted = {r.upper() for r in registries}
        cfgs = [c for c in cfgs if c["registry"].upper() in wanted]
    if year:
        cfgs = [c for c in cfgs if c["report_year"] == year]
    else:
        # latest report year per registry AND document: a registry can have several PDFs per year
        # (main report + supplements, e.g. AOANJRR ankle / elbow), told apart by `document` in the config
        latest = {}
        for c in cfgs:
            k = (c["registry"], c.get("document", "main"))
            if k not in latest or c["report_year"] > latest[k]["report_year"]:
                latest[k] = c
        cfgs = list(latest.values())
    return cfgs


def tables_for(cfg: dict, procedure: str) -> list[dict]:
    return [t for t in cfg["tables"] if t.get("enabled", True) and procedure in (t.get("procedures") or {})]


def resolve_pdf(cfg: dict, pdf_dir: Path = PDF_DIR) -> Path:
    hits = sorted(glob.glob(str(pdf_dir / cfg["pdf_glob"])))
    if not hits:
        raise FileNotFoundError(f"{cfg['registry']}: no PDF matching '{cfg['pdf_glob']}' in {pdf_dir}")
    if len(hits) > 1:
        raise ValueError(f"{cfg['registry']}: several PDFs match '{cfg['pdf_glob']}': {hits}")
    return Path(hits[0])


def make_columns(spec: dict) -> list[Column]:
    cols = []
    for c in copy.deepcopy(spec["columns"]):
        cols.append(
            Column(
                name=c["name"],
                header=c["header"] if isinstance(c["header"], list) else [c["header"]],
                kind=c.get("kind", "text"),
                align=c.get("align", "left" if c.get("kind") == "label" else "right"),
                time=c.get("time"),
            )
        )
    return cols


def table_ref(cfg: dict, spec: dict) -> str:
    return f"{cfg['registry']}_{cfg['report_year']}/{spec['key']}"
