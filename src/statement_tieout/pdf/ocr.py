"""Reading a scanned page into words with coordinates (SPEC §7.2).

The second ingest backend. A scan is rendered and read locally — ONNX, no
system dependency, no network, no per-page cost — into exactly the structure
the text-layer backend produces, so the profile derivation, the parser and
the reconciliation downstream are unchanged.

The interesting work is not the engine but the conversion below it. OCR
returns *line segments*, not words, and loses spaces:
`03/31ENDINGBALANCEFROMPRIORSTATEMENT` has to become a date in the date
column and a description beside it. Splitting on whitespace, on digit↔letter
boundaries and on lowercase→uppercase boundaries recovers enough structure to
place each token in a column, which is all the parser needs. Money tokens
survive untouched: they contain no letters and no case transitions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache

from ..layout.dates import DATE_FORMATS, YEARLESS_FORMATS, parse_date
from ..money import parse_money
from .model import Word

OCR_SCALE = 3.0
"""Render factor for a scanned page. Below ~2.0 small print starts to blur."""

MIN_OCR_CONFIDENCE = 0.5
"""Segments the engine is this unsure about are dropped rather than guessed at."""

#: Boundaries where a segment is split, in addition to whitespace. A colon is a
#: field separator that OCR glues to its label (`2024:LASTSTATEMENT`).
_BOUNDARY = re.compile(
    r"(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])|(?<=[a-z])(?=[A-Z])"
    r"|(?<=:)|(?=:)|(?<=\*)(?!\*)|(?<!\*)(?=\*)"
)

#: A thousands separator misread as a slash, a pipe or a period (SPEC §7.2).
#: The final group must be exactly two digits, which keeps European notation
#: (1.234,56) and dotted dates (04.01.2025) out of it.
_MISREAD_SEPARATOR = re.compile(r"^\d{1,3}(?:[/|.]\d{3})+\.\d{2}$")


@dataclass(frozen=True)
class Segment:
    """One line segment as an OCR engine reports it."""

    text: str
    x0: float
    x1: float
    top: float
    score: float
    bottom: float = 0.0
    rise: float = 0.0
    """How far the segment's line falls from its left end to its right. Zero on
    a square page; on a scan set off square the box is a quadrilateral and
    `top` is the height of its highest end alone (SPEC §7.2)."""

    @property
    def left_top(self) -> float:
        """The height of the line at `x0`, which `top` only equals going down."""
        return self.top if self.rise >= 0 else self.top - self.rise

    def top_at(self, x: float) -> float:
        """The height of the line under this segment at `x`."""
        span = self.x1 - self.x0
        if span <= 0:
            return self.left_top
        return self.left_top + (x - self.x0) / span * self.rise

    @property
    def text_height(self) -> float:
        """Type size, with the tilt taken back out of the box (SPEC §7.19)."""
        return max(self.bottom - self.top - abs(self.rise), 0.0)


def words_from_segments(segments: list[Segment], scale: float) -> list[Word]:
    """Split segments into tokens, share out their boxes, and undo the render scale."""
    words: list[Word] = []
    for segment in segments:
        if segment.score < MIN_OCR_CONFIDENCE:
            continue
        tokens = _tokenize(segment.text)
        if not tokens:
            continue
        characters = sum(len(token) for token in tokens)
        cursor = segment.x0
        for token in tokens:
            width = (segment.x1 - segment.x0) * len(token) / characters
            words.append(
                Word(
                    text=token,
                    x0=cursor / scale,
                    x1=(cursor + width) / scale,
                    # Along the segment's own line, not at its highest end: a
                    # token inherits where it sits, not where the box starts.
                    top=segment.top_at(cursor) / scale,
                    height=segment.text_height / scale,
                )
            )
            cursor += width
    return sorted(words, key=lambda word: (word.top, word.x0))


def _tokenize(text: str) -> list[str]:
    tokens = (token for part in text.split() for token in _BOUNDARY.split(part))
    return [_repair(token) for token in tokens if token and token != ":"]


#: Glyphs OCR returns for the digit 1 (SPEC §7.2).
_ONE_LOOKALIKES = str.maketrans("iIl|", "1111")


def _repair(token: str) -> str:
    if _MISREAD_SEPARATOR.match(token):
        head, _, cents = token.rpartition(".")
        return f"{head.replace('/', ',').replace('|', ',').replace('.', ',')}.{cents}"
    return _repair_ones(token)


def _repair_ones(token: str) -> str:
    """Substitute 1 for its look-alikes, but only where that makes the token parse.

    Self-verifying: a substitution that yields neither a date nor an amount is
    thrown away, which is what leaves `Ixonia` and `Life` alone.
    """
    # A token with no digit cannot become a date or an amount, so it is not
    # tried: that skips almost every word on the page, and trying them costs
    # a dozen strptime failures each.
    if not any(character.isdigit() for character in token):
        return token
    candidate = token.translate(_ONE_LOOKALIKES)
    if candidate == token:
        return token
    return candidate if _means_something(candidate) else token


def _means_something(token: str) -> bool:
    if parse_date(token, DATE_FORMATS + YEARLESS_FORMATS) is not None:
        return True
    try:
        parse_money(token)
    except ValueError:
        return False
    return True


def read_page(page, scale: float = OCR_SCALE) -> list[Word]:
    """OCR one `pypdfium2` page. The engine is built once and reused."""
    image = page.render(scale=scale).to_pil()
    raw, _ = _engine()(image)
    segments = [
        Segment(
            text=text,
            x0=min(point[0] for point in box),
            x1=max(point[0] for point in box),
            top=min(point[1] for point in box),
            bottom=max(point[1] for point in box),
            score=float(score),
            rise=_rise_of(box),
        )
        for box, text, score in (raw or [])
    ]
    return words_from_segments(segments, scale)


def _rise_of(box) -> float:
    """How far this box's top edge falls from its left corner to its right.

    The engine returns a quadrilateral. Its two highest corners are the top
    edge; on a square page they share a height and the rise is zero.
    """
    corners = sorted(box, key=lambda point: point[1])[:2]
    left, right = sorted(corners, key=lambda point: point[0])
    if right[0] == left[0]:
        return 0.0
    return float(right[1] - left[1])


@cache
def _engine():
    """Built lazily: importing the engine costs seconds and most files never need it."""
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()
