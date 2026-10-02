"""Word-level access to PDF pages (text layer) and embedded images (OCR).

Everything downstream works on a flat list of `Word` objects with page
coordinates, so text-layer PDFs and OCR'd images go through the same
table-parsing code.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import pdfplumber


@dataclass
class Word:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    italic: bool = False
    bold: bool = False
    conf: float = 100.0  # OCR confidence (100 for text-layer words)
    meta: dict = field(default_factory=dict)

    @property
    def xc(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def yc(self) -> float:
        return (self.top + self.bottom) / 2


def normalise_space(s: str) -> str:
    """Collapse all unicode whitespace (incl. non-breaking spaces) to single spaces."""
    return re.sub(r"\s+", " ", s.replace("\xa0", " ").replace("￾", "")).strip()


def page_words(page: "pdfplumber.page.Page") -> list[Word]:
    """Upright words from a pdfplumber page, with italic/bold flags from the font name."""
    raw = page.extract_words(
        x_tolerance=3,
        y_tolerance=2,
        keep_blank_chars=False,
        use_text_flow=False,
        extra_attrs=["fontname", "upright"],
    )
    out = []
    for w in raw:
        if not w.get("upright", True):
            continue  # rotated side-labels / watermarks
        font = (w.get("fontname") or "").lower()
        out.append(
            Word(
                text=w["text"],
                x0=w["x0"],
                x1=w["x1"],
                top=w["top"],
                bottom=w["bottom"],
                italic=("italic" in font) or bool(re.search(r"(?:-|\b)(?:\w*it|oblique)$", font)),
                bold=("bold" in font) or font.endswith("-md") or "medium" in font,
            )
        )
    return out


def page_text(page: "pdfplumber.page.Page") -> str:
    return normalise_space(page.extract_text() or "")


def rotated_words(page: "pdfplumber.page.Page", gap: float = 1.5) -> list[Word]:
    """Words from text rotated 90 degrees (reading bottom-to-top, e.g. landscape tables on portrait
    pages), rotated back into upright coordinates: x = distance from the page bottom, y = page x."""
    H = page.height
    chars = []
    for c in page.chars:
        a, b, cc, d = c["matrix"][:4]
        if abs(a) < 1e-3 and b > 0:  # 90 deg counter-clockwise text
            chars.append({"text": c["text"], "x0": H - c["bottom"], "x1": H - c["top"],
                          "top": c["x0"], "bottom": c["x1"], "font": (c.get("fontname") or "").lower()})
    chars.sort(key=lambda c: ((c["top"] + c["bottom"]) / 2, c["x0"]))
    lines: list[list[dict]] = []
    for c in chars:
        yc = (c["top"] + c["bottom"]) / 2
        if lines and abs((lines[-1][0]["top"] + lines[-1][0]["bottom"]) / 2 - yc) <= 2:
            lines[-1].append(c)
        else:
            lines.append([c])
    out = []
    for line in lines:
        line.sort(key=lambda c: c["x0"])
        cur: list[dict] = []

        def flush():
            if cur:
                font = cur[0]["font"]
                out.append(Word(text="".join(ch["text"] for ch in cur), x0=cur[0]["x0"], x1=cur[-1]["x1"],
                                top=min(ch["top"] for ch in cur), bottom=max(ch["bottom"] for ch in cur),
                                italic=("italic" in font) or bool(re.search(r"(?:-|\b)(?:\w*it|oblique)$", font)),
                                bold=("bold" in font) or font.endswith("-md")))
        for ch in line:
            if ch["text"].isspace():
                flush()
                cur = []
                continue
            if cur and ch["x0"] - cur[-1]["x1"] > gap:
                flush()
                cur = []
            cur.append(ch)
        flush()
    return out


def words_for(page: "pdfplumber.page.Page", spec: dict | None = None) -> list[Word]:
    if spec and spec.get("text_rotation") == 90:
        return rotated_words(page)
    return page_words(page)
