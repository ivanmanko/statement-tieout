"""Recognising dates inside a line of statement text.

Shared by the header reader and the heuristic profile: both need to know
whether a token is a date, and neither can rely on a format being declared
yet — that is what they are trying to work out.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

#: Candidate formats carrying a year, most specific first. Declared in SPEC §7.15.
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
)

#: Formats without a year, used only on transaction rows (SPEC §7.11).
YEARLESS_FORMATS: tuple[str, ...] = ("%m/%d", "%m-%d", "%b %d", "%d %b")

#: The year a yearless format parses to. A leap year, so "02/29" still parses,
#: and a sentinel the row parser replaces with the period's year (SPEC §7.11).
STAND_IN_YEAR = 1904

#: How many whitespace-separated words a date may span.
MAX_DATE_WORDS = 4


def parse_date(text: str, formats: Sequence[str] = DATE_FORMATS) -> date | None:
    """The date this exact string spells, or None.

    A format carrying no year is parsed against `STAND_IN_YEAR` rather than
    strptime's default, which warns and cannot represent 29 February.
    """
    # Punctuation attaches to dates in the wild — `DECEMBER 31, 2024: LAST
    # STATEMENT` — so a trailing colon or full stop is tried away as well.
    for attempt in (text, text.rstrip(":;.")):
        for fmt in formats:
            yearless = "%Y" not in fmt and "%y" not in fmt
            candidate = f"{attempt} {STAND_IN_YEAR}" if yearless else attempt
            pattern = f"{fmt} %Y" if yearless else fmt
            try:
                return datetime.strptime(candidate, pattern).date()
            except ValueError:
                continue
        if attempt == text.rstrip(":;."):
            break
    return None


def find_dates(text: str, formats: Sequence[str] = DATE_FORMATS) -> list[date]:
    """Every date in a line, left to right, longest match first at each position."""
    tokens = text.split()
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
    tokens = text.split()
    all_formats = tuple(formats) + YEARLESS_FORMATS
    return any(
        parse_date(" ".join(tokens[:length]), all_formats) is not None
        for length in range(1, min(MAX_DATE_WORDS, len(tokens)) + 1)
    )
