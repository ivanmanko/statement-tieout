"""Reading the printed header: account identity and the summary block.

The summary is *read*, never computed (ADR-001) — that is what makes
reconciliation a comparison of two independent things rather than a
tautology. Anything the page does not print is left `None` here and filled in
downstream, where it is recorded as derived rather than printed (SPEC §7.8).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from ..layout.dates import find_dates, starts_with_date
from ..money import ZERO, find_money
from ..schema import Account, DateRange, Summary, Transaction
from .labels import (
    BEGINNING_LABELS,
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

_MASKED_ACCOUNT = re.compile(r"[*xX×]{1,}\s?(\d{4})\b")
_FOUR_DIGITS = re.compile(r"\b(\d{4})\b")
_LONG_DIGITS = re.compile(r"\d{4,}")
_BARE_INTEGER = re.compile(r"\b\d{1,3}\b")


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
    header_lines: Sequence[str],
    body_lines: Sequence[str] = (),
) -> HeaderReading:
    """Read identity and totals, preferring the lines above the first row (SPEC §7.5)."""
    reading = HeaderReading(account=_read_account(header_lines))
    for field, labels in LABELS.items():
        found = _find_labelled(header_lines, labels) or _find_labelled(body_lines, labels)
        if found is None:
            continue
        amount, count = found
        setattr(reading, field, amount)
        if count is not None and field in _COUNT_OF:
            setattr(reading, _COUNT_OF[field], count)
    return reading


def _find_labelled(
    lines: Sequence[str], labels: Sequence[str]
) -> tuple[Decimal, int | None] | None:
    """The amount and optional count on the first line carrying one of these labels."""
    for line in lines:
        if starts_with_date(line):
            continue  # a transaction row, not a summary line
        normalized = " ".join(line.split()).casefold()
        if not any(label in normalized for label in labels):
            continue
        tokens = find_money(line)
        if not tokens:
            continue
        return tokens[-1].value, _count_on(line, tokens)
    return None


def _count_on(line: str, money: Sequence[object]) -> int | None:
    """A count is read only from an unambiguous line: one amount, one bare integer."""
    if len(money) != 1:
        return None
    without_money = _MONEY_SHAPED.sub(" ", line)
    integers = _BARE_INTEGER.findall(without_money)
    return int(integers[0]) if len(integers) == 1 else None


_MONEY_SHAPED = re.compile(r"[-(]?\$?\d[\d,]*\.\d{2}\)?-?")


def _read_account(lines: Sequence[str]) -> Account:
    return Account(
        bank=_read_bank(lines),
        account_last4=read_last4(lines),
        period=read_period(lines),
    )


def _read_bank(lines: Sequence[str]) -> str | None:
    """The letterhead: the first line with no money and no long digit run (SPEC §7.15)."""
    for line in lines:
        stripped = line.strip()
        if not stripped or not any(char.isalpha() for char in stripped):
            continue
        if find_money(stripped) or _LONG_DIGITS.search(stripped):
            continue
        return stripped
    return None


def read_last4(lines: Sequence[str]) -> str | None:
    """The masked or labelled account tail (SPEC §7.15). Also a period anchor."""
    for line in lines:
        masked = _MASKED_ACCOUNT.search(line)
        if masked:
            return masked.group(1)
    for line in lines:
        if "account" in line.casefold():
            digits = _FOUR_DIGITS.findall(line)
            if digits:
                return digits[-1]
    return None


def read_period(lines: Sequence[str]) -> DateRange:
    """The stated statement period (SPEC §7.15). Also a period anchor."""
    for line in lines:
        if not any(label in line.casefold() for label in PERIOD_LABELS):
            continue
        dates = find_dates(line)
        if len(dates) >= 2:
            return DateRange(start=dates[0], end=dates[-1])
        if dates:
            return DateRange(end=dates[0])
    return DateRange()


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
