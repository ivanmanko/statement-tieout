"""Words with coordinates, and the pages that hold them.

Kept free of pdfplumber so parsing can be tested on synthetic fixtures: the
row parser must be correct for any layout, and a test that needs a real PDF
can only ever check the layouts we happen to have.
"""

from __future__ import annotations

from dataclasses import dataclass, field

LINE_TOLERANCE = 3.0
"""Vertical distance within which two words belong to the same visual line."""

MAX_SKEW = 0.06
"""The steepest tilt looked for, about 3.4 degrees. Past that a page is not a
sheet fed crooked; it is a page we have no business guessing about."""

SKEW_COARSE_STEP = 0.004
SKEW_FINE_STEP = 0.0005
"""Two passes: a sweep of the whole range, then a fine sweep around its best.
A slope wrong by one fine step displaces a word by 0.3 pt across a page."""

SKEW_BIN = 1.0
"""Width, in points, of the projection-profile bins the angle is scored on."""

MIN_WORDS_FOR_SKEW = 30
"""Below this a page has no profile to speak of, and the sharpest angle is
whichever one happens to line two words up."""

SKEW_MARGIN = 1.05
"""How much sharper than square an angle must read to be believed."""


@dataclass(frozen=True)
class Word:
    text: str
    x0: float
    x1: float
    top: float
    height: float = 0.0
    """Text size. Both ingest backends report it; 0.0 means unknown."""

    @property
    def center(self) -> float:
        """Horizontal midpoint — how a word is assigned to a column."""
        return (self.x0 + self.x1) / 2


def _sharpness(words: list[Word], slope: float) -> float:
    """How cleanly these words fall into rows once this tilt is undone.

    The classic projection profile: bin the deskewed tops and sum the squares
    of the bin counts. Words that share a line land in one bin, so the sum is
    largest at the angle the page was actually set at.
    """
    bins: dict[int, int] = {}
    for word in words:
        key = int((word.top - word.x0 * slope) / SKEW_BIN)
        bins[key] = bins.get(key, 0) + 1
    return float(sum(count * count for count in bins.values()))


def estimate_skew(words: list[Word]) -> float:
    """The slope of this page's text lines, or 0.0 when it reads square.

    Rise over run, so a word `x` points to the right of another on the same
    line sits `x * slope` lower down the page.
    """
    if len(words) < MIN_WORDS_FOR_SKEW:
        return 0.0

    def sweep(around: float, step: float, reach: float) -> float:
        steps = int(reach / step)
        candidates = [around + index * step for index in range(-steps, steps + 1)]
        return max(candidates, key=lambda slope: _sharpness(words, slope))

    coarse = sweep(0.0, SKEW_COARSE_STEP, MAX_SKEW)
    best = sweep(coarse, SKEW_FINE_STEP, SKEW_COARSE_STEP)

    # A square page is the null hypothesis: an angle has to explain the layout
    # better than no angle at all, or noise would tilt every page a little.
    if _sharpness(words, best) <= _sharpness(words, 0.0) * SKEW_MARGIN:
        return 0.0
    return best


@dataclass(frozen=True)
class Page:
    number: int
    words: list[Word] = field(default_factory=list)
    text: str = ""
    source: str = "text"
    """Which ingest backend produced this page: `text`, `ocr` or `empty`."""
    skew: float = 0.0
    """The slope its lines are set at. A text layer reports exact coordinates
    and is therefore square by construction; only a scan can be crooked, so
    only a scan pays for the estimate (SPEC §7.2)."""

    def __post_init__(self) -> None:
        if self.source == "ocr" and not self.skew:
            object.__setattr__(self, "skew", estimate_skew(self.words))

    def _row(self, word: Word) -> float:
        """Where this word sits down the page, read along the page's own angle."""
        return word.top - word.x0 * self.skew

    def lines(self, tolerance: float = LINE_TOLERANCE) -> list[list[Word]]:
        """Words grouped into visual lines, each ordered left to right."""
        grouped: list[list[Word]] = []
        for word in sorted(self.words, key=lambda w: (self._row(w), w.x0)):
            if grouped and self._row(word) - self._row(grouped[-1][0]) <= tolerance:
                grouped[-1].append(word)
            else:
                grouped.append([word])
        return [sorted(line, key=lambda w: w.x0) for line in grouped]
