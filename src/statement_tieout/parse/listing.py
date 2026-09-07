"""A listing set several to a line (SPEC §7.22).

Two of the four samples print a cheque section this way: a header such as
`NUMBER DATE AMOUNT NUMBER DATE AMOUNT`, or `Date Check No. Amount` repeated
three times across the page, and under it lines that each hold two or three
transactions. The row model reads one transaction per line, so without this
module those lines are either skipped or misread.

The header is the whole layout: it names the slots, and where in each slot the
date and the amount sit. Nothing here decides whether the listing *belongs* in
the table — on one statement it is the only record of its cheques, on another
it repeats rows the table already holds, and the page does not say which.
That is the verifier's call, made in `api`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from ..layout.dates import MAX_DATE_WORDS
from ..money import find_money
from ..pdf.model import Word

MIN_SLOTS = 2
"""A header naming one date-and-amount pair is an ordinary table header."""

DATE_WORDS = ("date",)
AMOUNT_WORDS = ("amount",)
CHECK_WORDS = ("check", "cheque", "chk")
"""What a listing header calls its columns. A header that names a check is a
listing of withdrawals whether or not a heading above it said so."""


@dataclass(frozen=True)
class Slot:
    """One repetition of the header: a horizontal band holding one record."""

    x0: float
    x1: float

    def holds(self, center: float) -> bool:
        return self.x0 <= center < self.x1


@dataclass(frozen=True)
class Listing:
    slots: tuple[Slot, ...]
    names_a_check: bool


@dataclass(frozen=True)
class Record:
    when: date
    amount: Decimal
    description: str


def read_header(line: Sequence[Word]) -> Listing | None:
    """The slots this line declares, or None when it is not a listing header.

    A slot ends at each word naming the amount and must carry a word naming
    the date; the line must declare at least `MIN_SLOTS` of them and carry no
    money of its own. Slot boundaries fall midway between one slot's last
    header word and the next slot's first.
    """
    words = sorted(line, key=lambda word: word.x0)
    if any(find_money(word.text) for word in words):
        return None

    groups: list[list[Word]] = []
    current: list[Word] = []
    for word in words:
        current.append(word)
        if _names(word, AMOUNT_WORDS):
            groups.append(current)
            current = []
    if len(groups) < MIN_SLOTS:
        return None
    if not all(any(_names(word, DATE_WORDS) for word in group) for group in groups):
        return None

    edges = [
        (groups[index][-1].x1 + groups[index + 1][0].x0) / 2
        for index in range(len(groups) - 1)
    ]
    bounds = [float("-inf"), *edges, float("inf")]
    return Listing(
        slots=tuple(Slot(x0=bounds[i], x1=bounds[i + 1]) for i in range(len(groups))),
        names_a_check=any(_names(word, CHECK_WORDS) for word in words),
    )


def read_records(
    listing: Listing,
    line: Sequence[Word],
    *,
    date_of: Callable[[list[Word]], date | None],
    amount_at: Callable[[list[Word], int], tuple[int, Decimal | None]],
) -> list[Record]:
    """One record per slot that holds both a date and an amount, left to right.

    `date_of` and `amount_at` are the row parser's own readers, so a listing
    is read under the same date formats and the same OCR fragment repairs as
    the table it sits in.
    """
    records = []
    for slot in listing.slots:
        words = sorted((w for w in line if slot.holds(w.center)), key=lambda w: w.x0)
        found = _record(words, date_of, amount_at)
        if found is not None:
            records.append(found)
    return records


def _record(
    words: list[Word],
    date_of: Callable[[list[Word]], date | None],
    amount_at: Callable[[list[Word], int], tuple[int, Decimal | None]],
) -> Record | None:
    when, date_span = _first_date(words, date_of)
    if when is None:
        return None

    amount, amount_span = None, (0, 0)
    index = 0
    while index < len(words):
        span, value = amount_at(words, index)
        if span:
            amount, amount_span = value, (index, index + span)
            index += span
        else:
            index += 1
    if amount is None:
        return None

    taken = set(range(*date_span)) | set(range(*amount_span))
    rest = " ".join(word.text for i, word in enumerate(words) if i not in taken)
    return Record(when=when, amount=abs(amount), description=rest)


def _first_date(
    words: list[Word], date_of: Callable[[list[Word]], date | None]
) -> tuple[date | None, tuple[int, int]]:
    """The leftmost date in these words, longest join first at each position."""
    for start in range(len(words)):
        for length in range(min(MAX_DATE_WORDS, len(words) - start), 0, -1):
            when = date_of(words[start : start + length])
            if when is not None:
                return when, (start, start + length)
    return None, (0, 0)


def _names(word: Word, vocabulary: tuple[str, ...]) -> bool:
    text = word.text.casefold().strip(".:#")
    return any(text.startswith(name) for name in vocabulary)
