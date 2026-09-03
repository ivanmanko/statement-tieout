"""Reading the printed header: account identity and the summary block.

The summary is *read*, never computed (ADR-001) — that is what makes
reconciliation a comparison of two independent things rather than a tautology.
Anything the page does not print is left `None` here and filled in downstream,
where it is recorded as derived rather than printed (SPEC §7.8).

Two block layouts exist in the wild and both are handled: *vertical*, with the
label and the amount on one line, and *horizontal*, with a row of labels above
a row of amounts matched by column. A reader that knows only the first finds
no summary at all on a bank that uses the second.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from ..layout.dates import find_dates, starts_with_date
from ..money import ZERO, MoneyToken, find_money
from ..pdf.model import Word
from ..schema import Account, DateRange, Summary, Transaction
from .labels import (
    BEGINNING_LABELS,
    CYCLE_END_LABEL,
    CYCLE_START_LABEL,
    DEPOSIT_LABELS,
    ENDING_LABELS,
    WITHDRAWAL_LABELS,
)

#: SPEC §7.5, from the one module that owns the vocabulary.
LABELS: dict[str, tuple[str, ...]] = {
    "beginning_balance": BEGINNING_LABELS,
    "ending_balance": ENDING_LABELS,
    "deposits_total": DEPOSIT_LABELS,
    "withdrawals_total": WITHDRAWAL_LABELS,
}

#: Which count field rides along with which total (SPEC §7.5).
_COUNT_OF = {"deposits_total": "deposits_count", "withdrawals_total": "withdrawals_count"}

PERIOD_LABELS = ("statement period", "statement date", "for the period", "period covered")

LETTERHEAD_LINES = 15
"""How far down the page a letterhead may be (SPEC §7.15)."""

LETTERHEAD_SIZE_RATIO = 0.85
"""Words this close to the largest on the page belong to the letterhead with it."""

MIN_LABELS_IN_A_ROW = 2
"""A horizontal label row carries several labels; one is a stray word."""

# Two guards, both from real pages: a mask never follows a letter (`P.O.Box 4887`
# ends in `x`), and a lone mask character must touch the digits — OCR of that same
# line elsewhere reads `P.O.B 0 x 4887`.
_MASKED_ACCOUNT = re.compile(
    r"(?<![A-Za-z0-9])(?:[*xX×]\s*){2,}(\d{4})(?!\d)"
    r"|(?<![A-Za-z0-9])[*xX×](\d{4})(?!\d)"
)
_FOUR_DIGITS = re.compile(r"\b(\d{4})\b")
_BARE_INTEGER = re.compile(r"\b\d{1,3}\b")
_LONG_DIGITS = re.compile(r"\d{4,}")
_DIGIT_RUN = re.compile(r"\d{5,}")
_MONEY_SHAPED = re.compile(r"[-(]?\$?\d[\d,]*\.\d{2}\)?-?")

RawLine = "str | Sequence[Word] | _Line"


@dataclass(frozen=True)
class _Line:
    """One line of the page, with word positions when the caller has them."""

    text: str
    words: tuple[Word, ...] = ()

    @property
    def money(self) -> list[MoneyToken]:
        return find_money(self.text)


@dataclass
class HeaderReading:
    """What the page printed. `None` means the page did not say."""

    account: Account
    beginning_balance: Decimal | None = None
    ending_balance: Decimal | None = None
    deposits_total: Decimal | None = None
    withdrawals_total: Decimal | None = None
    deposits_count: int | None = None
    withdrawals_count: int | None = None

    @property
    def printed_fields(self) -> set[str]:
        """The summary fields that actually came off the page (SPEC §7.8)."""
        return {
            name
            for name in (
                "beginning_balance", "ending_balance", "deposits_total",
                "withdrawals_total", "deposits_count", "withdrawals_count",
            )
            if getattr(self, name) is not None
        }


def read_header(
    header_lines: Sequence[RawLine],
    body_lines: Sequence[RawLine] = (),
) -> HeaderReading:
    """Read identity and totals, preferring the lines above the first row (SPEC §7.5)."""
    header, body = _as_lines(header_lines), _as_lines(body_lines)
    reading = HeaderReading(account=_read_account(header))

    horizontal = _horizontal_block(header) or _horizontal_block(body)
    for field, labels in LABELS.items():
        if field in horizontal:
            setattr(reading, field, horizontal[field])
            continue
        found = _find_labelled(header, labels) or _find_labelled(body, labels)
        if found is None:
            continue
        amount, count = found
        setattr(reading, field, amount)
        if count is not None and field in _COUNT_OF:
            setattr(reading, _COUNT_OF[field], count)
    return reading


def _as_lines(raw: Sequence[RawLine]) -> list[_Line]:
    lines = []
    for item in raw:
        if isinstance(item, _Line):
            lines.append(item)
        elif isinstance(item, str):
            lines.append(_Line(text=item))
        else:
            words = tuple(item)
            lines.append(_Line(text=" ".join(w.text for w in words), words=words))
    return lines


def _squash(text: str) -> str:
    """Whitespace-insensitive form: OCR loses spaces, and the label is the same."""
    return "".join(text.split()).casefold()


def _labels_on(line: _Line) -> dict[str, str]:
    """Which summary fields this line names, and with which label."""
    squashed = _squash(line.text)
    found = {}
    for field, labels in LABELS.items():
        for label in labels:
            if _squash(label) in squashed:
                found[field] = label
                break
    return found


# --------------------------------------------------------------------------- vertical


def _find_labelled(
    lines: Sequence[_Line], labels: Sequence[str]
) -> tuple[Decimal, int | None] | None:
    """The amount and optional count on the first line carrying one of these labels."""
    for line in lines:
        if starts_with_date(line.text):
            continue  # a transaction row, not a summary line
        squashed = _squash(line.text)
        if not any(_squash(label) in squashed for label in labels):
            continue
        tokens = line.money
        if not tokens:
            continue
        return tokens[-1].value, _count_on(line.text, tokens)
    return None


def _count_on(text: str, money: Sequence[MoneyToken]) -> int | None:
    """A count is read only from an unambiguous line: one amount, one bare integer."""
    if len(money) != 1:
        return None
    integers = _BARE_INTEGER.findall(_MONEY_SHAPED.sub(" ", text))
    return int(integers[0]) if len(integers) == 1 else None


# ------------------------------------------------------------------------- horizontal


def _horizontal_block(lines: Sequence[_Line]) -> dict[str, Decimal]:
    """A row of labels over a row of amounts, matched by horizontal midpoint."""
    for index, line in enumerate(lines):
        if line.money or not line.words or starts_with_date(line.text):
            continue
        named = _labels_on(line)
        if len(named) < MIN_LABELS_IN_A_ROW:
            continue
        values = _next_value_row(lines, index)
        if values is None:
            continue
        spans = {}
        for field in named:
            # Try every label for the field: the one that matched the whole line
            # as a substring may not be the one the words actually spell.
            for label in LABELS[field]:
                span = _span_of(label, line.words)
                if span is not None:
                    spans[field] = span
                    break
        if len(spans) < MIN_LABELS_IN_A_ROW:
            continue
        return _assign_by_column(spans, values)
    return {}


def _next_value_row(lines: Sequence[_Line], index: int) -> list[Word] | None:
    """The following line carrying several amounts is the label row's values."""
    for line in lines[index + 1 : index + 3]:
        amounts = [word for word in line.words if find_money(word.text)]
        if len(amounts) >= MIN_LABELS_IN_A_ROW:
            return amounts
    return None


def _span_of(label: str, words: Sequence[Word]) -> tuple[float, float] | None:
    """Where this label sits horizontally, given words that may be split anywhere."""
    target = _squash(label)
    for start in range(len(words)):
        accumulated = ""
        for end in range(start, min(start + 8, len(words))):
            accumulated += _squash(words[end].text)
            if accumulated == target:
                return words[start].x0, words[end].x1
            if not target.startswith(accumulated):
                break
    return None


def _assign_by_column(
    spans: dict[str, tuple[float, float]], values: list[Word]
) -> dict[str, Decimal]:
    """Give each label the nearest amount, nearest pair first, no amount reused."""
    centers = {field: (span[0] + span[1]) / 2 for field, span in spans.items()}
    pairs = sorted(
        (abs((word.x0 + word.x1) / 2 - center), field, index)
        for field, center in centers.items()
        for index, word in enumerate(values)
    )
    assigned: dict[str, Decimal] = {}
    used: set[int] = set()
    for _, field, index in pairs:
        if field in assigned or index in used:
            continue
        amount = find_money(values[index].text)
        if amount:
            assigned[field] = amount[-1].value
            used.add(index)
    return assigned


# --------------------------------------------------------------------------- identity


def _read_account(lines: Sequence[_Line]) -> Account:
    return Account(
        bank=_read_bank(lines),
        account_last4=read_last4(lines),
        period=read_period(lines),
    )


def _read_bank(lines: Sequence[_Line]) -> str | None:
    """The letterhead: the words set in the largest type (SPEC §7.15).

    Word, not line: a logo is set larger than the address printed beside it,
    so taking the whole line returns the address too.
    """
    candidates = [line for line in lines[:LETTERHEAD_LINES] if _could_be_a_letterhead(line)]
    if not candidates:
        return None

    tallest = max(
        (word for line in candidates for word in line.words),
        key=lambda word: word.height,
        default=None,
    )
    if tallest is not None and tallest.height > 0:
        home = next(line for line in candidates if tallest in line.words)
        floor = tallest.height * LETTERHEAD_SIZE_RATIO
        return " ".join(word.text for word in home.words if word.height >= floor)

    # No size information (a caller passing plain strings): fall back to the
    # older rule, which prefers a line carrying no long run of digits.
    plain = [line for line in candidates if not _LONG_DIGITS.search(line.text)]
    return (plain or candidates)[0].text.strip()


def _could_be_a_letterhead(line: _Line) -> bool:
    text = line.text.strip()
    if not text or not any(char.isalpha() for char in text):
        return False
    return not (":" in text or find_money(text) or find_dates(text))


def read_last4(lines: Sequence[RawLine]) -> str | None:
    """The masked or labelled account tail (SPEC §7.15). Also a period anchor."""
    resolved = _as_lines(lines)
    for line in resolved:
        masked = _MASKED_ACCOUNT.search(line.text)
        if masked:
            return masked.group(1) or masked.group(2)
    for line in resolved:
        if "account" not in _squash(line.text):
            continue
        exact = _FOUR_DIGITS.findall(line.text)
        if exact:
            return exact[-1]
        runs = _DIGIT_RUN.findall(line.text)
        if runs:
            return max(runs, key=len)[-4:]
    return None


def read_period(lines: Sequence[RawLine]) -> DateRange:
    """The stated statement period (SPEC §7.15). Also a period anchor."""
    resolved = _as_lines(lines)
    for line in resolved:
        if not any(label in line.text.casefold() for label in PERIOD_LABELS):
            continue
        dates = find_dates(line.text)
        if len(dates) >= 2:
            return DateRange(start=dates[0], end=dates[-1])
        if dates:
            return _completed(DateRange(end=dates[0]), resolved)
    return _completed(_cycle_pair(resolved), resolved)


def _completed(period: DateRange, lines: Sequence[_Line]) -> DateRange:
    """Fill a missing endpoint from `Beginning/Ending Balance as of <date>`.

    Statements routinely print only a statement date in the header while
    stating the real range beside the balances — and without a start, a
    transaction line reading `Apr 01` has no year (SPEC §7.11).
    """
    start, end = period.start, period.end
    if start is not None and end is not None:
        return period
    for line in lines:
        dates = find_dates(line.text)
        if not dates:
            continue
        squashed = _squash(line.text)
        if start is None and any(_squash(x) in squashed for x in BEGINNING_LABELS):
            start = dates[0]
        elif end is None and any(_squash(x) in squashed for x in ENDING_LABELS):
            end = dates[0]
    return DateRange(start=start, end=end)


def _cycle_pair(lines: Sequence[_Line]) -> DateRange:
    """`... : LAST STATEMENT` over `... : THIS STATEMENT` (SPEC §7.15)."""
    start = end = None
    for line in lines:
        squashed = _squash(line.text)
        dates = find_dates(line.text)
        if not dates:
            continue
        if start is None and _squash(CYCLE_START_LABEL) in squashed:
            start = dates[0]
        elif end is None and _squash(CYCLE_END_LABEL) in squashed:
            end = dates[0]
    return DateRange(start=start, end=end)


# ---------------------------------------------------------------------------- assembly


def build_summary(reading: HeaderReading, transactions: Sequence[Transaction]) -> Summary:
    """Fill what the page did not print from the parsed rows, and say which was which."""
    deposits = [t.deposit for t in transactions if t.deposit is not None]
    withdrawals = [t.withdrawal for t in transactions if t.withdrawal is not None]
    deposits_total = sum(deposits, ZERO)
    withdrawals_total = sum(withdrawals, ZERO)

    beginning = reading.beginning_balance if reading.beginning_balance is not None else ZERO
    ending = reading.ending_balance
    if ending is None:
        printed_deposits = reading.deposits_total
        printed_withdrawals = reading.withdrawals_total
        ending = (
            beginning
            + (printed_deposits if printed_deposits is not None else deposits_total)
            - (printed_withdrawals if printed_withdrawals is not None else withdrawals_total)
        )

    return Summary(
        beginning_balance=beginning,
        ending_balance=ending,
        deposits_total=_or(reading.deposits_total, deposits_total),
        deposits_count=_or(reading.deposits_count, len(deposits)),
        withdrawals_total=_or(reading.withdrawals_total, withdrawals_total),
        withdrawals_count=_or(reading.withdrawals_count, len(withdrawals)),
        printed_fields=reading.printed_fields,
    )


def _or(printed, derived):
    return derived if printed is None else printed
