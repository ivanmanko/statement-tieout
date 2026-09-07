"""Putting scanned pages back on one margin (SPEC §7.2).

A sheet fed through a scanner lands a few points to the left or right of the
sheet before it. Every rule downstream is stated in terms of columns, and a
layout profile is derived from one page of a period and applied to the rest —
so a page offset by a dozen points has its dates fall outside the date column
and reads as no rows at all. Measured on a 99-page binder: whole pages of a
statement were lost that way, with the profile otherwise correct.

The offset is measured from the rows' own left edge and taken out here, at
ingest, like the tilt. A text-layer page reports exact coordinates and is
never moved.
"""

from __future__ import annotations

from dataclasses import replace
from statistics import median

from ..layout.dates import starts_with_date
from ..money import find_money
from .model import Page, Word

MIN_ROWS_FOR_REGISTRATION = 3
"""Fewer rows than this and the margin is whichever two lines happen to
agree, not a property of the page."""

MAX_REGISTRATION = 30.0
"""The largest sideways shift a scanner is believed to have introduced. Past
it the page is laid out differently, not fed crooked, and is left alone."""


def row_margin(page: Page) -> float | None:
    """Where this page's transaction rows begin: the median left edge of the
    lines that open with a date and carry money. None when there are too few
    such lines to speak of a margin."""
    edges = [line[0].x0 for line in page.lines() if _is_row(line)]
    if len(edges) < MIN_ROWS_FOR_REGISTRATION:
        return None
    return float(median(edges))


def register(pages: list[Page]) -> list[Page]:
    """The scanned pages moved sideways so that their rows share one margin.

    The reference is the median margin over the scanned pages that have one,
    so a single page fed off to the side moves and the rest stay put. Pages
    with no margin of their own, text-layer pages, and pages whose offset
    exceeds `MAX_REGISTRATION` are returned as they are.
    """
    margins = {
        page.number: row_margin(page) for page in pages if page.source == "ocr"
    }
    known = [margin for margin in margins.values() if margin is not None]
    if len(known) < 2:
        return pages
    reference = float(median(known))

    registered = []
    for page in pages:
        margin = margins.get(page.number)
        shift = None if margin is None else reference - margin
        if not shift or abs(shift) > MAX_REGISTRATION:
            registered.append(page)
            continue
        registered.append(replace(page, words=[_moved(word, shift) for word in page.words]))
    return registered


def _moved(word: Word, shift: float) -> Word:
    return replace(word, x0=word.x0 + shift, x1=word.x1 + shift)


def _is_row(line: list[Word]) -> bool:
    text = " ".join(word.text for word in line)
    return starts_with_date(text) and bool(find_money(text))
