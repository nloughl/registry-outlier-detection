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
