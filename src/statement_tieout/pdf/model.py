"""Words with coordinates, and the pages that hold them.

Kept free of pdfplumber so parsing can be tested on synthetic fixtures: the
row parser must be correct for any layout, and a test that needs a real PDF
can only ever check the layouts we happen to have.
"""

from __future__ import annotations

from dataclasses import dataclass, field

LINE_TOLERANCE = 3.0
"""Vertical distance within which two words belong to the same visual line."""


@dataclass(frozen=True)
class Word:
    text: str
    x0: float
    x1: float
    top: float

    @property
    def center(self) -> float:
        """Horizontal midpoint — how a word is assigned to a column."""
        return (self.x0 + self.x1) / 2


@dataclass(frozen=True)
class Page:
    number: int
    words: list[Word] = field(default_factory=list)
    text: str = ""
    source: str = "text"
    """Which ingest backend produced this page: `text`, `ocr` or `empty`."""

    def lines(self, tolerance: float = LINE_TOLERANCE) -> list[list[Word]]:
        """Words grouped into visual lines, each ordered left to right."""
        grouped: list[list[Word]] = []
        for word in sorted(self.words, key=lambda w: (w.top, w.x0)):
            if grouped and word.top - grouped[-1][0].top <= tolerance:
                grouped[-1].append(word)
            else:
                grouped.append([word])
        return [sorted(line, key=lambda w: w.x0) for line in grouped]
