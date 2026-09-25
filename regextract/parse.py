"""Cell-text parsers. Pure functions: text in, typed values out."""
from __future__ import annotations

import re

NUM = r"\d+(?:\.\d+)?"
NA_RE = re.compile(r"^(?:n\.?a\.?|na|no|-|–|—|\.)$", re.I)


def compact_numbers(s: str) -> str:
    """Repair numbers split by the PDF text layer: '19. 7' -> '19.7', '[0 .5;' -> '[0.5;', '(2, 139)' -> '(2,139)'."""
    s = s.replace("\xa0", " ").replace("–", "-").replace("—", "-")
    s = re.sub(r"(\d)\s+\.\s*(\d)", r"\1.\2", s)
    s = re.sub(r"(\d)\.\s+(\d)", r"\1.\2", s)
    s = re.sub(r"(\d),\s+(\d{3})\b", r"\1,\2", s)
    s = re.sub(r"([\(\[])\s+", r"\1", s)
    s = re.sub(r"\s+([\)\]])", r"\1", s)
    # digits split by kerning inside a CI bound, e.g. '[1 1.5; 15.0]' -> '[11.5; 15.0]'
    s = re.sub(r"(?<=[\[\(])[\d .]+(?=[;,])", lambda m: m.group(0).replace(" ", ""), s)
    s = re.sub(r"(?<=[;,] )[\d .]+(?=[\]\)])", lambda m: m.group(0).replace(" ", ""), s)
    # digits split by kerning inside at-risk counts, e.g. '(2 8 9)' -> '(289)'
    s = re.sub(r"\((\d[\d ,]*\d)\)", lambda m: "(" + m.group(1).replace(" ", "") + ")", s)
    return s.strip()


def parse_int(s: str) -> tuple[int | None, list[str]]:
    flags = []
    s = s.strip()
    if "*" in s or "†" in s:
        flags.append("marked_*")
    s = re.sub(r"[*†\s]", "", s)
    s = s.replace(",", "")
    if not s or NA_RE.match(s):
        return None, flags
    m = re.match(r"^\d+(?:\.\d+)?$", s)
    return (int(float(s)) if m else None), flags + ([] if m else [f"unparsed_int:{s}"])


EST_CI_RE = re.compile(
    rf"(?P<est>{NUM})\s*[\(\[]\s*(?P<lcl>{NUM})\s*(?:[,;]|-|to)\s*(?P<ucl>{NUM})\s*[\)\]]"
    rf"(?:\s*\((?P<risk>[\d,]+)\))?"
)
EST_ONLY_RE = re.compile(rf"^(?P<est>{NUM})(?:\s*\((?P<risk>[\d,]+)\))?$")


def parse_est_ci(s: str) -> dict:
    """'1.9 (1.3, 2.8)' | '0.92(0.88-0.96)' | '3.6 [2.2; 5.0] (583)' | 'n.a.' | ''."""
    out = {"estimate": None, "lcl": None, "ucl": None, "n_at_risk": None, "status": "blank"}
    s = compact_numbers(s)
    if not s:
        return out
    if NA_RE.match(s):
        out["status"] = "not_reported"
        return out
    m = EST_CI_RE.search(s)
    m0 = re.match(rf"^(?P<est>{NUM})\s*[\(\[]\s*\.?\s*[-;,]\s*\.?\s*[\)\]]$", s)
    if not m and m0:  # e.g. NJR '0.00 (.-.)': estimate printed, CI not estimable
        out.update(estimate=float(m0["est"]), status="ci_not_estimable")
        return out
    if not m:
        m2 = EST_ONLY_RE.match(s)
        if m2:
            out.update(estimate=float(m2["est"]), status="estimate_only")
            if m2["risk"]:
                out["n_at_risk"] = int(m2["risk"].replace(",", ""))
            return out
        out["status"] = f"unparsed:{s}"
        return out
    out.update(estimate=float(m["est"]), lcl=float(m["lcl"]), ucl=float(m["ucl"]), status="ok")
    if m["risk"]:
        out["n_at_risk"] = int(m["risk"].replace(",", ""))
    rest = (s[: m.start()] + s[m.end():]).strip()
    if rest:
        out["status"] = f"ok_extra:{rest}"
    return out


def parse_median_iqr(s: str) -> dict:
    """'64 (57 to 71)' | '62 (56 - 70)' | '64(58-70)' -> median, q1, q3."""
    s = compact_numbers(s)
    m = re.search(rf"(?P<med>{NUM})\s*\(\s*(?P<q1>{NUM})\s*(?:to|-)\s*(?P<q3>{NUM})\s*\)", s)
    if m:
        return {"median": float(m["med"]), "q1": float(m["q1"]), "q3": float(m["q3"])}
    m = re.match(rf"^({NUM})$", s)
    return {"median": float(m[1]) if m else None, "q1": None, "q3": None}


def parse_ratio(s: str) -> tuple[float | None, float | None]:
    """EPRD 'm/f' column '46/54' -> (46, 54)."""
    m = re.search(rf"({NUM})\s*/\s*({NUM})", s)
    return (float(m[1]), float(m[2])) if m else (None, None)


def parse_number(s: str) -> float | None:
    s = compact_numbers(s).replace(",", "")
    m = re.match(rf"^({NUM})$", s)
    return float(m[1]) if m else None
