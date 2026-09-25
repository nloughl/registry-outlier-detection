"""Deterministic, coordinate-based table reconstruction.

Approach (same for every registry; only the YAML config differs):

1. Locate each configured column header phrase on the page -> x-position.
   (This doubles as validation that we are looking at the expected table.)
2. Derive column boundaries from the header positions.
3. Find "anchor" words (e.g. the N column) in the data region: each anchor
   marks one table record. Registries print multi-line cells vertically
   centred, so every other word is assigned to its nearest anchor.
4. Label-only lines that match a configured section pattern become section
   headers (e.g. EPRD "Unicondylar knee arthroplasties, fixed bearing, cemented").
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .words import Word, normalise_space

NUMERIC_KINDS = {"int", "est_ci", "number", "median_iqr", "ratio"}
NUMERIC_RE = re.compile(r"^[\(\[]?[\d.,;\-–]+[\)\]]?[*†]?$")


@dataclass
class Column:
    name: str
    header: list[str]  # alternative header phrases; first one found wins
    kind: str = "text"  # label | int | est_ci | median_iqr | ratio | text
    align: str = "right"  # left | right | center
    time: float | None = None
    x0: float = 0.0
    x1: float = 0.0
    found: bool = False
    lo: float = 0.0
    hi: float = 0.0


@dataclass
class Record:
    section: str | None
    cells: dict[str, list[Word]] = field(default_factory=dict)
    anchor_y: float = 0.0
    page: int = 0

    def text(self, col: str) -> str:
        return join_words(self.cells.get(col, []))

    def italic(self, col: str) -> bool:
        ws = [w for w in self.cells.get(col, []) if re.search(r"\d", w.text)]
        return bool(ws) and any(w.italic for w in ws)

    def min_conf(self, col: str) -> float:
        ws = self.cells.get(col, [])
        return min((w.conf for w in ws), default=100.0)


def cluster_lines(words: list[Word], tol: float = 2.5) -> list[list[Word]]:
    """Group words into visual lines by vertical centre."""
    lines: list[list[Word]] = []
    for w in sorted(words, key=lambda w: w.yc):
        if lines and abs(lines[-1][-1].yc - w.yc) <= tol:
            lines[-1].append(w)
        else:
            lines.append([w])
    return [sorted(l, key=lambda w: w.x0) for l in lines]


def join_words(ws: list[Word]) -> str:
    if not ws:
        return ""
    parts = [" ".join(w.text for w in line) for line in cluster_lines(ws)]
    return normalise_space(" ".join(parts))


def _tok(s: str) -> str:
    return re.sub(r"[^\w%]", "", s.lower())


def _tok_eq(tok: str, target: str) -> bool:
    """Exact token match, tolerating footnote digits ('Brand1') and yr/yrs, year/years plurals."""
    if tok == target:
        return True
    if not target.isdigit() and re.sub(r"\d+$", "", tok) == target:
        return True
    return target in ("yr", "year") and tok == target + "s"


def find_phrase(lines: list[list[Word]], phrase: str, min_top: float = -1) -> tuple[float, float, float] | None:
    """Find a phrase as consecutive words on one line. Returns (x0, x1, bottom)."""
    target = [_tok(t) for t in phrase.split() if _tok(t)]
    if not target:
        return None
    for line in lines:
        if line[0].top < min_top:
            continue
        toks = [_tok(w.text) for w in line]
        for i in range(len(toks) - len(target) + 1):
            if all(_tok_eq(toks[i + j], target[j]) for j in range(len(target))):
                ws = line[i: i + len(target)]
                return ws[0].x0, ws[-1].x1, max(w.bottom for w in ws)
    return None


def _find_all(lines: list[list[Word]], phrase: str, min_top: float) -> list[tuple[float, float, float]]:
    hits = []
    for line in lines:
        if line[0].top < min_top:
            continue
        h = find_phrase([line], phrase)
        if h:
            hits.append(h)
    return hits


def locate_columns(words: list[Word], columns: list[Column], min_top: float = -1,
                   band: tuple[float, float] = (25, 45)) -> tuple[list[Column], float]:
    """Set x-positions of each column from its header phrase. Returns (columns, header_bottom).

    Every occurrence of the first column's header is tried; the other headers must lie within a
    vertical band around it, and the occurrence that finds the most headers wins. This stops prose
    above/below a table (e.g. '... after 12 years ...') being mistaken for the header row."""
    lines = cluster_lines(words)
    best = None
    first = columns[0]
    starts = [h for alt in first.header for h in _find_all(lines, alt, min_top)]
    for x0, x1, bottom in starts:
        top = bottom - 8
        cand = [l for l in lines if top - band[0] <= l[0].top <= top + band[1]]
        found = {first.name: (x0, x1, bottom)}
        for col in columns[1:]:
            for alt in col.header:
                hit = find_phrase(cand, alt, min_top)
                if hit:
                    found[col.name] = hit
                    break
        if best is None or len(found) > len(best):
            best = found
    best = best or {}
    header_bottom = min_top
    for col in columns:
        col.found = col.name in best
        if col.found:
            col.x0, col.x1, bottom = best[col.name]
            header_bottom = max(header_bottom, bottom)
    return columns, header_bottom


def set_bounds(columns: list[Column], right_edge: float, overrides: dict | None = None) -> None:
    """Boundaries between adjacent columns (columns must be in left-to-right order)."""
    cols = [c for c in columns if c.found]
    for i, c in enumerate(cols):
        if i == 0:
            c.lo = -1e9
        if i + 1 < len(cols):
            n = cols[i + 1]
            if n.align == "left" or c.align == "left":
                b = n.x0 - 2  # label columns start where their header starts
            else:
                b = ((c.x0 + c.x1) / 2 + (n.x0 + n.x1) / 2) / 2
            c.hi = b
            n.lo = b
        else:
            c.hi = right_edge
    for name, (lo, hi) in (overrides or {}).items():
        for c in cols:
            if c.name == name:
                c.lo, c.hi = lo, hi


def refine_label_bounds(columns: list[Column], words: list[Word], top: float, bottom: float) -> None:
    """Adjacent left-aligned label columns: headers are often offset from the data, so put the
    boundary in the empty vertical gutter between the two columns' data words instead."""
    cols = [c for c in columns if c.found]
    for a, b in zip(cols, cols[1:]):
        if a.kind != "label" or b.kind != "label":
            continue
        span_lo, span_hi = a.x0, b.x1
        ivs = sorted((w.x0, w.x1) for w in words
                     if top < w.yc < bottom and span_lo - 5 <= w.x0 and w.x1 <= span_hi + 60)
        gaps, cur_hi = [], None
        for lo, hi in ivs:
            if cur_hi is not None and lo - cur_hi >= 2:
                gaps.append((cur_hi, lo))
            cur_hi = hi if cur_hi is None else max(cur_hi, hi)
        gaps = [g for g in gaps if a.x0 + 5 < g[1] <= b.x0 + 2]
        if gaps:
            g = min(gaps, key=lambda g: abs(g[1] - b.x0))
            mid = (g[0] + g[1]) / 2
            a.hi = b.lo = mid


def refine_label_numeric_bounds(columns: list[Column], words: list[Word], top: float, bottom: float) -> None:
    """Label column followed by a right-aligned numeric column: numbers often extend left of their
    header, so move the boundary to just left of the leftmost pure number printed under that header."""
    cols = [c for c in columns if c.found]
    for a, b in zip(cols, cols[1:]):
        if a.kind != "label" or b.kind == "label":
            continue
        xs = [w.x0 for w in words
              if top < w.yc < bottom and re.match(r"^[\d,]+[*†]?$", w.text)
              and b.x0 - 45 <= w.x1 <= b.x1 + 6]
        if xs:
            bnd = max(min(xs) - 1, a.x0 + 10)
            if bnd < a.hi:
                a.hi = b.lo = bnd


def column_of(w: Word, columns: list[Column]) -> Column | None:
    found = [c for c in columns if c.found]
    for i, c in enumerate(found):
        if c.lo <= w.xc < c.hi:
            # words with letters that straddle into a numeric column belong to the label on the left
            if (c.kind in NUMERIC_KINDS and i > 0 and found[i - 1].kind == "label"
                    and re.search(r"[A-Za-z]{2,}", w.text) and w.x0 < c.x0):
                return found[i - 1]
            return c
    return None


def build_records(
    words: list[Word],
    columns: list[Column],
    anchor: str,
    data_top: float,
    data_bottom: float,
    section_patterns: list[str],
    ignore_patterns: list[str],
    page_no: int,
    current_section: str | None = None,
    anchor_pattern: str = r"^[\d,.]+[*†]?$",
) -> tuple[list[Record], str | None]:
    """Segment data-region words into records around anchor words."""
    label_cols = {c.name for c in columns if c.kind == "label"}
    region = [w for w in words if data_top < w.yc < data_bottom and column_of(w, columns)]
    lines = cluster_lines(region)

    # 1. section header lines and ignorable lines
    sec_lines: list[tuple[float, str]] = []
    keep: list[Word] = []
    for line in lines:
        txt = join_words(line)
        if any(re.search(p, txt, re.I) for p in ignore_patterns):
            continue
        only_labels = all(column_of(w, columns).name in label_cols for w in line)
        if only_labels and any(re.search(p, txt, re.I) for p in section_patterns):
            sec_lines.append((line[0].yc, txt))
            continue
        keep.extend(line)

    # 2. anchors
    anchor_col = next(c for c in columns if c.name == anchor)
    anchors = sorted(
        (w for w in keep if column_of(w, columns) is anchor_col and re.match(anchor_pattern, w.text)),
        key=lambda w: w.yc,
    )
    merged: list[float] = []
    for a in anchors:
        if not merged or a.yc - merged[-1] > 3:
            merged.append(a.yc)

    # records, with section context
    events = sorted([(y, "sec", t) for y, t in sec_lines] + [(y, "anc", None) for y in merged])
    records: list[Record] = []
    section = current_section
    for y, kind, txt in events:
        if kind == "sec":
            section = txt
        else:
            records.append(Record(section=section, anchor_y=y, page=page_no))

    # 3. assign words to nearest anchor within the same section segment
    sec_ys = [y for y, _ in sec_lines]

    def segment(y: float) -> int:
        return sum(1 for s in sec_ys if s < y)

    unassigned = []
    for w in keep:
        cands = [r for r in records if segment(r.anchor_y) == segment(w.yc)]
        if not cands:
            unassigned.append(w)
            continue
        r = min(cands, key=lambda r: abs(r.anchor_y - w.yc))
        col = column_of(w, columns)
        r.cells.setdefault(col.name, []).append(w)
    return records, section
