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


def deskew(words: list[Word]) -> list[Word]:
    """These words with the page's tilt taken out of them (SPEC §7.2).

    A rotation moves a word in both directions at once: down the page in
    proportion to how far right it sits, and across the page in proportion to
    how far down. Correcting only the first puts the lines back and leaves the
    columns drifting sideways as the page descends — and a column that drifts
    is not a column, which is the evidence the profile is looking for.

    Applied once, at ingest, so that nothing downstream has to know the page
    was ever crooked. Returns the words unchanged when it reads square.
    """
    slope = estimate_skew(words)
    if not slope:
        return words
    return [
        Word(
            text=word.text,
            x0=word.x0 + word.top * slope,
            x1=word.x1 + word.top * slope,
            top=word.top - word.x0 * slope,
            height=word.height,
        )
        for word in words
    ]


@dataclass(frozen=True)
class Page:
    number: int
    words: list[Word] = field(default_factory=list)
    text: str = ""
    source: str = "text"
    """Which ingest backend produced this page: `text`, `ocr` or `empty`."""

    def lines(self, tolerance: float = LINE_TOLERANCE) -> list[list[Word]]:
        """Words grouped into visual lines, each ordered left to right.

        A word joins the line whose words' **mean** height it is within the
        tolerance of (SPEC §7.2). Anchoring on the first word instead tears a
        row apart whenever a stray token — a cheque image's serial number in
        the margin, three points above the row — happens to come first.
        """
        grouped: list[list[Word]] = []
        heights: list[float] = []  # running sum of tops, one per line
        for word in sorted(self.words, key=lambda w: (w.top, w.x0)):
            if grouped and word.top - heights[-1] / len(grouped[-1]) <= tolerance:
                grouped[-1].append(word)
                heights[-1] += word.top
            else:
                grouped.append([word])
                heights.append(word.top)
        return [sorted(line, key=lambda w: w.x0) for line in grouped]
