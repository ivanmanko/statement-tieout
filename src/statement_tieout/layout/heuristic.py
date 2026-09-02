"""Deriving a layout profile from coordinates alone — rung 0 (SPEC §7.17).

This rung costs nothing and is what generalization rests on: no bank name, no
template, no model. Its other job is to **decline**. A profile guessed where
the page gives no evidence would produce transactions that look fine and
reconcile wrongly; returning None instead is what makes the ladder above it
mean something.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..money import parse_money
from ..parse.labels import DEPOSIT_LABELS, WITHDRAWAL_LABELS
from ..pdf.model import Page, Word
from .dates import DATE_FORMATS, MAX_DATE_WORDS, YEARLESS_FORMATS, parse_date
from .profile import Column, LayoutProfile, SideStrategy

MIN_CANDIDATE_ROWS = 3
COLUMN_GAP = 20.0
MIN_COLUMN_SHARE = 0.30
COLUMN_PAD = 2.0
MIN_BALANCE_PAIRS = 2

_CANDIDATE_FORMATS = DATE_FORMATS + YEARLESS_FORMATS


@dataclass
class _Row:
    date_words: list[Word]
    date_format: str
    money: list[tuple[Word, Decimal]]


def derive_profile(pages: list[Page]) -> LayoutProfile | None:
    """A profile for these pages, or None when the page gives no evidence."""
    rows, headings = _scan(pages)
    if len(rows) < MIN_CANDIDATE_ROWS:
        return None

    clusters = _money_clusters(rows)
    if not clusters:
        return None

    balance, amounts = _split_off_balance(clusters, rows)
    if not amounts:
        return None
    amounts = amounts[-2:]  # at most two, rightmost first (SPEC §7.17.5)

    deposits, withdrawals = _classify_headings(headings)
    strategy = _side_strategy(amounts, rows, deposits, withdrawals, balance)
    if strategy is None:
        return None

    return LayoutProfile(
        date_column=_span(word for row in rows for word in row.date_words),
        amount_columns=[cluster.column for cluster in amounts],
        balance_column=balance.column if balance else None,
        side_strategy=strategy,
        date_formats=[_dominant_format(rows)],
        deposit_sections=deposits,
        withdrawal_sections=withdrawals,
    )


def _scan(pages: list[Page]) -> tuple[list[_Row], list[str]]:
    """Split every line into a candidate transaction row, a heading, or noise."""
    rows: list[_Row] = []
    headings: list[str] = []
    for page in pages:
        for line in page.lines():
            money = [(word, value) for word in line if (value := _as_money(word.text))]
            leading = _leading_date(line)
            if leading is not None and money:
                date_words, date_format = leading
                rows.append(_Row(date_words=date_words, date_format=date_format, money=money))
            elif leading is None and not money:
                headings.append(" ".join(word.text for word in line))
    return rows, headings


def _leading_date(line: list[Word]) -> tuple[list[Word], str] | None:
    """The date this line opens with, longest join first, and the format that read it."""
    for length in range(min(MAX_DATE_WORDS, len(line)), 0, -1):
        text = " ".join(word.text for word in line[:length])
        for fmt in _CANDIDATE_FORMATS:
            if parse_date(text, [fmt]) is not None:
                return list(line[:length]), fmt
    return None


@dataclass
class _Cluster:
    column: Column
    rows: set[int]

    def share(self, total: int) -> float:
        return len(self.rows) / total


def _money_clusters(rows: list[_Row]) -> list[_Cluster]:
    """Money midpoints split on horizontal gaps, sparse clusters discarded."""
    placed = sorted(
        ((word, index) for index, row in enumerate(rows) for word, _ in row.money),
        key=lambda pair: pair[0].center,
    )
    groups: list[list[tuple[Word, int]]] = []
    for word, index in placed:
        if groups and word.center - groups[-1][-1][0].center <= COLUMN_GAP:
            groups[-1].append((word, index))
        else:
            groups.append([(word, index)])

    clusters = [
        _Cluster(
            column=_span(word for word, _ in group),
            rows={index for _, index in group},
        )
        for group in groups
    ]
    return [c for c in clusters if c.share(len(rows)) >= MIN_COLUMN_SHARE]


def _split_off_balance(
    clusters: list[_Cluster], rows: list[_Row]
) -> tuple[_Cluster | None, list[_Cluster]]:
    """The rightmost cluster is a balance column only if its chain actually holds."""
    if len(clusters) < 2:
        return None, clusters
    rightmost = clusters[-1]
    others = clusters[:-1]
    if _behaves_like_a_running_balance(rightmost, others, rows):
        return rightmost, others
    return None, clusters


def _behaves_like_a_running_balance(
    candidate: _Cluster, others: list[_Cluster], rows: list[_Row]
) -> bool:
    """SPEC §7.17.4: b[i] − b[i−1] == ± the row's amount, on a majority of pairs."""
    balances = [_value_in(row, candidate) for row in rows]
    amounts = [_value_in(row, others[-1]) for row in rows]
    pairs = [
        (balances[i] - balances[i - 1], amounts[i])
        for i in range(1, len(rows))
        if balances[i] is not None and balances[i - 1] is not None and amounts[i] is not None
    ]
    if len(pairs) < MIN_BALANCE_PAIRS:
        return False
    holds = sum(1 for delta, amount in pairs if delta in (amount, -amount))
    return holds * 2 > len(pairs)


def _value_in(row: _Row, cluster: _Cluster) -> Decimal | None:
    for word, value in row.money:
        if cluster.column.holds(word.center):
            return value
    return None


def _classify_headings(headings: list[str]) -> tuple[list[str], list[str]]:
    deposits, withdrawals = [], []
    for heading in headings:
        normalized = " ".join(heading.split()).casefold()
        if any(label in normalized for label in DEPOSIT_LABELS):
            deposits.append(heading)
        elif any(label in normalized for label in WITHDRAWAL_LABELS):
            withdrawals.append(heading)
    return deposits, withdrawals


def _side_strategy(
    amounts: list[_Cluster],
    rows: list[_Row],
    deposits: list[str],
    withdrawals: list[str],
    balance: _Cluster | None,
) -> SideStrategy | None:
    """SPEC §7.6 priority order; None when the page says nothing about sides."""
    if len(amounts) == 2:
        return SideStrategy.TWO_COLUMNS
    if any(value < 0 for row in rows for _, value in row.money):
        return SideStrategy.SIGNED
    if len(deposits) + len(withdrawals) >= 2:
        return SideStrategy.SECTIONS
    if balance is not None:
        return SideStrategy.BALANCE_DELTA
    return None


def _dominant_format(rows: list[_Row]) -> str:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.date_format] = counts.get(row.date_format, 0) + 1
    return max(counts, key=lambda fmt: (counts[fmt], -_CANDIDATE_FORMATS.index(fmt)))


def _span(words) -> Column:
    materialized = list(words)
    return Column(
        x0=min(word.x0 for word in materialized) - COLUMN_PAD,
        x1=max(word.x1 for word in materialized) + COLUMN_PAD,
    )


def _as_money(text: str) -> Decimal | None:
    try:
        return parse_money(text)
    except ValueError:
        return None
