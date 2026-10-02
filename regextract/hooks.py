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


# ------------------------------------------------------------------------ case-mix (sex / age) tables

def std_age(s: str | None) -> str | None:
    """'55 to 64' / '55-64' -> '55-64'; '≥75' -> '>=75'; '<55' -> '<55'."""
    if not s:
        return None
    s = re.sub(r"\s+", " ", s).strip()
    s = s.replace("≥", ">=").replace("≤", "<=")
    s = re.sub(r"(\d+)\s*(?:to|-|–)\s*(\d+)", r"\1-\2", s)
    return s.replace(" ", "")


@hook("casemix")
def casemix(rec: dict) -> None:
    """Rows of case-mix tables: sex x age group, optionally within an implant design group."""
    sex = _clean(rec.get("sex"))
    if sex and sex.lower() in ("male", "female"):
        rec["sex"] = sex.capitalize()
    elif sex and sex.upper() == "TOTAL":
        rec["sex"] = None
        rec["device_label"] = "TOTAL"
    rec["age_group"] = std_age(rec.get("age"))
    rec["age_group_raw"] = _clean(rec.get("age"))
    grp, sub = _clean(rec.get("design_group")), _clean(rec.get("design_subgroup"))
    rec["design_group"], rec["design_subgroup"] = grp, sub
    parts = [p for p in (grp, sub) if p] or [p for p in (rec.get("sex"),) if p]
    if rec.get("device_label") != "TOTAL":
        rec["device_label"] = " | ".join(parts)
    if grp is not None or sub is not None:
        rec["row_type"] = "group" if rec.get("is_group_row") else "stratum"
    elif rec.get("device_label") == "TOTAL":
        rec["row_type"] = "total"
    elif rec.get("sex") and not rec.get("age_group"):
        rec["row_type"] = "subtotal"
    else:
        rec["row_type"] = "stratum"

    # NJR-style design descriptors
    g = (grp or "").lower()
    if "unicondylar" in g:
        rec["implant_class"] = "unicondylar"
    fx = re.search(r"\b(uncemented/hybrid|uncemented|cemented|hybrid)\b", g)
    rec["fixation"] = fx.group(1) if fx else None
    if sub:
        m = re.match(r"^(medial|lateral)\s*,\s*(.+)$", sub, re.I)
        if m:
            rec["compartment"], rec["bearing"] = m.group(1).lower(), m.group(2).strip()
        else:
            m = re.match(r"^(unconstrained|posterior-stabilised|constrained condylar)\s*,?\s*(.*)$", sub, re.I)
            if m:
                rec["constraint"], rec["bearing"] = m.group(1).lower(), (m.group(2).strip() or None)


@hook("lroi_period")
def lroi_period(rec: dict) -> None:
    """LROI 'by procedure year' rows: '2009-2010' (printed with a hyphen or en dash) -> a period stratum."""
    raw = _clean(rec.get("period")) or ""
    raw = re.sub(r"\s*[-\u2013\u2010\u00b7.]\s*", "-", raw)      # OCR reads the dash as '-', '.', or a middle dot
    m = re.match(r"^(\d{4})-(\d{4})$", raw)
    rec["procedure_period"] = raw or None
    rec["period_start"], rec["period_end"] = (int(m.group(1)), int(m.group(2))) if m else (None, None)
    rec["device_label"] = raw
    rec["row_type"] = "stratum"


# ------------------------------------------------------------------------ rare joints (ankle, elbow)

def _strip_marks(s: str | None) -> str | None:
    """Drop trailing '*' (not used in the report year) and the AOANJRR HTARR footnote '1' glued to
    'Hintermann Series H3' ('H31')."""
    s = _clean(s)
    if not s:
        return s
    s = re.sub(r"\s*\*+$", "", s)
    s = re.sub(r"(Series H3)1$", r"\1", s)
    s = re.sub(r"(?<=\w) 1(?= |$)", "", s)          # superscript footnote '1' read as a separate word
    s = re.sub(r"-\s+", "-", s)                      # 'Buechel- Pappas' (wrapped) -> 'Buechel-Pappas'
    return s


def _summary_row(rec: dict, label: str) -> None:
    if re.match(r"^TOTAL$", label or "", re.I):
        rec["row_type"] = "total"
    elif re.match(r"^Other \(\d+\)", label or "", re.I):
        rec["row_type"] = "other"


@hook("aoanjrr_elbow_stem")
def aoanjrr_elbow_stem(rec: dict) -> None:
    """AOANJRR ET6-ET8: one humeral stem per row (total elbow without radial replacement)."""
    rec["humeral"] = _strip_marks(rec.get("humeral"))
    rec["device_label"] = rec["humeral"] or ""
    _summary_row(rec, rec["device_label"])


@hook("aoanjrr_ankle_combo")
def aoanjrr_ankle_combo(rec: dict) -> None:
    """AOANJRR A15: tibial / talar prosthesis combination."""
    if any(re.search(r"\*\s*$", rec.get(f) or "") for f in ("tibial", "talar")):
        rec["flags"].append("not_used_in_report_year_*")
    rec["tibial"], rec["talar"] = _strip_marks(rec.get("tibial")), _strip_marks(rec.get("talar"))
    t, a = rec["tibial"], rec["talar"]
    rec["device_label"] = t if (t and a and t.lower() == a.lower()) else " / ".join(p for p in (t, a) if p)
    _summary_row(rec, rec["device_label"])


def _period(raw: str | None) -> tuple[str | None, int | None, int | None]:
    raw = _clean(raw) or ""
    raw = re.sub(r"\s*[-\u2013\u2010\u00b7.]\s*", "-", raw)
    m = re.match(r"^(\d{4})-(\d{4})$", raw)
    if m:
        return raw, int(m.group(1)), int(m.group(2))
    m = re.match(r"^Pre (\d{4})$", raw, re.I)
    if m:
        return raw, None, int(m.group(1)) - 1
    return raw or None, None, None


@hook("stratum")
def stratum(rec: dict) -> None:
    """Generic case-mix stratum row: label columns named `implant_class`, `diagnosis`, `period`, `sex`, `age`
    are copied to the matching case-mix fields; TOTAL / 'Other (n)' rows become summary rows."""
    for f in ("implant_class", "diagnosis"):
        if rec.get(f) is not None:
            rec[f] = _clean(rec.get(f))
    if rec.get("period") is not None:
        rec["procedure_period"], rec["period_start"], rec["period_end"] = _period(rec.get("period"))
    if rec.get("age") is not None:
        rec["age_group"], rec["age_group_raw"] = std_age(rec.get("age")), _clean(rec.get("age"))
    parts = [rec.get(f) for f in ("implant_class", "diagnosis", "procedure_period") if rec.get(f)]
    rec["device_label"] = " | ".join(parts)
    rec["row_type"] = "stratum"
    first = _clean(rec.get("implant_class") or rec.get("diagnosis") or rec.get("period")) or ""
    _summary_row(rec, first)
    if rec["row_type"] == "total":
        rec["device_label"] = "TOTAL"
        rec["implant_class"] = rec["diagnosis"] = None


@hook("njr_sex_age")
def njr_sex_age(rec: dict) -> None:
    """NJR 3.A3: 'All cases', then 'Female' / 'Male' sub-headings (carried by group_pattern) with age rows."""
    lab = _clean(rec.get("age")) or ""
    sex = rec.pop("design_group", None)
    is_group = rec.pop("is_group_row", False)
    rec.pop("design_subgroup", None)
    if re.match(r"^All cases$", lab, re.I):
        rec["row_type"], rec["device_label"] = "total", "All cases"
    elif is_group:
        rec["row_type"], rec["sex"], rec["device_label"] = "subtotal", lab.capitalize(), lab.capitalize()
    else:
        rec["row_type"], rec["sex"] = "stratum", (sex or "").capitalize() or None
        rec["age_group"], rec["age_group_raw"] = std_age(lab), lab
        rec["device_label"] = f"{rec['sex']} | {rec['age_group']}"


ELBOW_CLASSES = [
    (r"inc\.? radial head", "Total elbow inc. radial head"),
    (r"total elbow", "Total elbow"),
    (r"radial head", "Radial head"),
    (r"distal humeral", "Distal humeral hemiarthroplasty"),
    (r"lateral resurfacing", "Lateral resurfacing"),
]


def elbow_class(text: str | None) -> str | None:
    t = (text or "").lower()
    for pat, name in ELBOW_CLASSES:
        if re.search(pat, t):
            return name
    return None


@hook("njr_elbow_indication")
def njr_elbow_indication(rec: dict) -> None:
    """NJR 3.E6: rows by procedure type within 'All acute trauma cases' / 'All elective cases'.
    The indication (acute trauma / elective) is stored in `diagnosis`; the procedure type in `implant_class`.
    'Unconfirmed ...' rows (component labels do not match the reported procedure) are kept but flagged."""
    lab = _clean(rec.get("label")) or ""
    grp = rec.pop("design_group", None) or ""
    is_group = rec.pop("is_group_row", False)
    rec.pop("design_subgroup", None)
    ind = "Acute trauma" if re.search(r"trauma", grp, re.I) else "Elective" if re.search(r"elective", grp, re.I) else None
    if re.match(r"^All acute trauma and elective", lab, re.I):
        rec["row_type"], rec["device_label"] = "total", "All acute trauma and elective cases"
        return
    rec["diagnosis"] = ind
    if is_group:
        rec["row_type"], rec["device_label"] = "subtotal", f"All {ind.lower()} cases" if ind else lab
        return
    rec["row_type"] = "stratum"
    rec["implant_class"] = elbow_class(lab)
    if re.match(r"^Unconfirmed", lab, re.I):
        rec["implant_class"] = f"Unconfirmed {rec['implant_class'] or ''}".strip()
        rec["flags"].append("unconfirmed_procedure_type")
    rec["device_label"] = f"{ind} | {rec['implant_class']}"


def _njr_tagged(raw: str, tags: dict[str, str]) -> tuple[str, dict[str, str | None]]:
    """'Infinity[Tibial] Inbone[Talar]' -> ('Infinity / Inbone', {'tibial': 'Infinity', 'talar': 'Inbone'})."""
    raw = re.sub(r"\s*\[\s*", "[", raw)
    raw = re.sub(r"\s*\]\s*", "] ", raw).strip()
    parts = re.findall(r"([^\[\]]+?)\[([^\]]+)\]", raw)
    out = {v: None for v in tags.values()}
    if not parts:
        return raw, out
    names = []
    for name, tag in parts:
        name = name.strip()
        names.append(name)
        for key, field in tags.items():
            if key.lower() in tag.lower():
                out[field] = name
    return " / ".join(names), out


@hook("njr_ankle_brand")
def njr_ankle_brand(rec: dict) -> None:
    raw = _clean(rec.get("brand")) or ""
    label, comp = _njr_tagged(raw, {"Tibial": "tibial", "Talar": "talar"})
    rec["device_label"] = label
    rec["tibial"] = comp["tibial"] or label
    rec["talar"] = comp["talar"] or label


@hook("njr_elbow_brand")
def njr_elbow_brand(rec: dict) -> None:
    """NJR 3.E8: brand within a drawn group cell (total elbow / radial head / distal humeral hemi) and,
    for total elbows, a linked / unlinked sub-cell."""
    raw = _clean(rec.get("brand")) or ""
    label, comp = _njr_tagged(raw, {"Hum": "humeral", "Ulna": "ulnar"})
    rec["device_label"] = label
    rec["humeral"] = comp["humeral"] or label
    rec["ulnar"] = comp["ulnar"]
    rec["implant_class"] = elbow_class(rec.get("group"))
    sub = _clean(rec.get("subgroup")) or ""
    rec["section"] = rec["implant_class"]
    if re.search(r"unlinked", sub, re.I):
        rec["constraint"] = "unlinked"
    elif re.search(r"linked", sub, re.I):
        rec["constraint"] = "linked"
