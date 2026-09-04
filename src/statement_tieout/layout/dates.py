"""Recognising dates inside a line of statement text.

Shared by the header reader and the heuristic profile: both need to know
whether a token is a date, and neither can rely on a format being declared
yet — that is what they are trying to work out.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date, datetime

#: Candidate formats carrying a year, most specific first. Declared in SPEC §7.19.
DATE_FORMATS: tuple[str, ...] = (
    "%m/%d/%Y",
    "%m/%d/%y",
    "%m-%d-%Y",
    "%Y-%m-%d",
    "%B %d, %Y",
    "%b %d, %Y",
    "%B %d %Y",
    "%b %d %Y",
    "%d %B %Y",
    "%d %b %Y",
    # Two-digit years, day first: the notation outside the United States.
    # Tried last, so `01 Apr 2025` is never read as `%d %b %y` first.
    "%d %B %y",
    "%d %b %y",
)

#: Formats without a year, used only on transaction rows (SPEC §7.12).
YEARLESS_FORMATS: tuple[str, ...] = ("%m/%d", "%m-%d", "%b %d", "%d %b")

#: The year a yearless format parses to. A leap year, so "02/29" still parses,
#: and a sentinel the row parser replaces with the period's year (SPEC §7.12).
STAND_IN_YEAR = 1904

#: How many whitespace-separated words a date may span.
MAX_DATE_WORDS = 4


#: OCR sets the comma it sees and not the space beside it: a scanned
#: `February 01, 2026` arrives as `February 01,2026` (SPEC §7.19).
_RUN_TOGETHER_COMMA = re.compile(r",(?=\S)")

#: The hyphen of a range set without spaces — `February 1,2026-February 28,2026`
#: — which glues one date's year to the next date's month. No declared format
#: puts a hyphen between a digit and a letter, so no date is broken by this.
_RANGE_HYPHEN = re.compile(r"(?<=\d)-(?=[A-Za-z])|(?<=[A-Za-z])-(?=\d)")


def _spellings(text: str) -> list[str]:
    """The forms this string might be, once OCR's habits are undone.

    Every form is only ever *tried*: a rewrite that does not then parse as a
    date under a declared format is discarded, so restoring the space cannot
    turn `1,809.28` into anything.
    """
    forms: list[str] = []
    for candidate in (text, _RUN_TOGETHER_COMMA.sub(", ", text)):
        # Punctuation attaches to dates in the wild — `DECEMBER 31, 2024: LAST
        # STATEMENT` — so a trailing colon or full stop is tried away as well.
        for form in (candidate, candidate.rstrip(":;.")):
            if form and form not in forms:
                forms.append(form)
    return forms


def parse_date(text: str, formats: Sequence[str] = DATE_FORMATS) -> date | None:
    """The date this exact string spells, or None.

    A format carrying no year is parsed against `STAND_IN_YEAR` rather than
    strptime's default, which warns and cannot represent 29 February.
    """
    for attempt in _spellings(text):
        for fmt in formats:
            yearless = "%Y" not in fmt and "%y" not in fmt
            candidate = f"{attempt} {STAND_IN_YEAR}" if yearless else attempt
            pattern = f"{fmt} %Y" if yearless else fmt
            try:
                return datetime.strptime(candidate, pattern).date()
            except ValueError:
                continue
    return None


def _words(text: str) -> list[str]:
    """The line's words, with a range's hyphen treated as the space it stands for."""
    return _RANGE_HYPHEN.sub(" ", text).split()


def find_dates(text: str, formats: Sequence[str] = DATE_FORMATS) -> list[date]:
    """Every date in a line, left to right, longest match first at each position."""
    tokens = _words(text)
    found: list[date] = []
    index = 0
    while index < len(tokens):
        for length in range(min(MAX_DATE_WORDS, len(tokens) - index), 0, -1):
            when = parse_date(" ".join(tokens[index : index + length]), formats)
            if when is not None:
                found.append(when)
                index += length
                break
        else:
            index += 1
    return found


def starts_with_date(text: str, formats: Sequence[str] = DATE_FORMATS) -> bool:
    """True when the line opens with a date, i.e. it is a transaction row."""
    tokens = _words(text)
    all_formats = tuple(formats) + YEARLESS_FORMATS
    return any(
        parse_date(" ".join(tokens[:length]), all_formats) is not None
        for length in range(1, min(MAX_DATE_WORDS, len(tokens)) + 1)
    )
