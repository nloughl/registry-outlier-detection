"""Registry-specific label handling (the only per-registry *code*; everything else is config).

Each hook receives the wide record dict (label columns already joined to text)
and fills the harmonised label fields:
    device_label, femoral, tibial, manufacturer_femoral, manufacturer_tibial,
    compartment (medial / lateral / patellofemoral / multicompartmental / None)
"""
from __future__ import annotations

import re

HOOKS = {}


def hook(name):
    def deco(fn):
        HOOKS[name] = fn
        return fn
    return deco


def _clean(s: str | None) -> str | None:
    if s is None:
        return None
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def _star(rec: dict, *fields) -> None:
    for f in fields:
        v = rec.get(f)
        if v and re.match(r"^\s*\*", v):
            rec[f] = v.lstrip("* ").strip()
            rec["flags"].append("label_marked_*")
        if v and re.search(r"\*\s*$", v):
            rec[f] = v.rstrip("* ").strip()
            rec["flags"].append("label_marked_*")


@hook("aoanjrr_combination")
def aoanjrr_combination(rec: dict) -> None:
    rec["femoral"] = _clean(rec.get("femoral"))
    rec["tibial"] = _clean(rec.get("tibial"))
    parts = [p for p in (rec["femoral"], rec["tibial"]) if p]
    rec["device_label"] = " / ".join(parts)


@hook("njr_brand")
def njr_brand(rec: dict) -> None:
    """'Oxford Cementless Partial Knee[M.Fem]Oxford Partial Knee[M.Tib]' or 'Physica ZUK[M.Fem:M.Tib]'."""
    raw = _clean(rec.get("brand")) or ""
    raw = re.sub(r"\[\s*", "[", raw)
    raw = re.sub(r"\s*\]", "]", raw)
    raw = re.sub(r"([\[:])([ML])\.\s+", r"\1\2.", raw)      # '[M. Fem' -> '[M.Fem'
    raw = re.sub(r"(\w)\s+(Fem|Tib)\]", r"\1\2]", raw)     # 'M. Tib]' split variants
    rec["brand"] = raw
    _star(rec, "brand")
    raw = rec["brand"]
    rec["device_label"] = raw
    tags = re.findall(r"\[([^\]]+)\]", raw)
    names = [n.strip() for n in re.split(r"\[[^\]]+\]", raw) if n.strip()]
    side = None
    joined = " ".join(tags)
    if "PFJ" in joined:
        side = "patellofemoral"
    elif re.search(r"\bM\.", joined):
        side = "medial"
    elif re.search(r"\bL\.", joined):
        side = "lateral"
    if re.search(r"multi-?compartmental", rec.get("section") or "", re.I):
        side = "multicompartmental"
    rec["compartment"] = side
    fem = tib = None
    if len(tags) == 1 and names:
        if ":" in tags[0] or "Fem" not in tags[0] and "Tib" not in tags[0]:
            fem = tib = names[0]
        elif "Fem" in tags[0]:
            fem = names[0]
        else:
            tib = names[0]
    elif len(tags) >= 2 and len(names) >= 2:
        for n, t in zip(names, tags):
            if "Fem" in t:
                fem = n
            if "Tib" in t:
                tib = n
    rec["femoral"], rec["tibial"] = fem, tib


def _split_manufacturer(s: str | None) -> tuple[str | None, str | None]:
    s = _clean(s)
    if not s:
        return None, None
    m = re.match(r"^(.*?)\s*\(([^()]+)\)\s*$", s)
    if m and m.group(1):
        return m.group(1).strip(), m.group(2).strip()
    return s, None


@hook("eprd_combination")
def eprd_combination(rec: dict) -> None:
    rec["femoral"], rec["manufacturer_femoral"] = _split_manufacturer(rec.get("femoral"))
    rec["tibial"], rec["manufacturer_tibial"] = _split_manufacturer(rec.get("tibial"))
    parts = [p for p in (rec["femoral"], rec["tibial"]) if p]
    rec["device_label"] = " / ".join(parts) if parts else (rec.get("section") or "")
    if not parts:
        rec["row_type"] = "group"
    sec = (rec.get("section") or "").lower()
    if "patellofemoral" in sec:
        rec["compartment"] = "patellofemoral"


@hook("siris_system")
def siris_system(rec: dict) -> None:
    rec["device_label"] = _clean(rec.get("system")) or ""


@hook("lroi_combination")
def lroi_combination(rec: dict) -> None:
    rec["femoral"] = _clean(rec.get("femoral"))
    rec["tibial"] = _clean(rec.get("tibial"))
    _star(rec, "femoral", "tibial")
    parts = [p for p in (rec["femoral"], rec["tibial"]) if p]
    rec["device_label"] = " / ".join(parts)
