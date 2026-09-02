"""Residual diagnosis (SPEC §5.1).

The residual is an address, not just a verdict. When the statement prints
transaction counts, the arithmetic of a single-row error is forced: two
integers and one Decimal say which side the row belonged on, what it was
worth, and what happened to it. When counts are absent the same numbers
still narrow the field, but the answer becomes a candidate rather than an
identification, and says so.

Nothing here edits anything. A diagnosis that silently rewrote a transaction
would produce a corrupted result that reconciles, which is strictly worse
than a failure that is reported.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..money import ZERO, find_money, format_money
from ..schema import Summary, Transaction
from .engine import reconcile

DEPOSIT = "deposit"
WITHDRAWAL = "withdrawal"


@dataclass(frozen=True)
class Diagnosis:
    """What the residual points at. `confident` is False for candidates."""

    kind: str
    detail: str
    side: str | None = None
    amount: Decimal | None = None
    row_index: int | None = None
    page: int | None = None
    confident: bool = True


def diagnose(
    summary: Summary,
    transactions: list[Transaction],
    *,
    running_balances: list[Decimal] | None = None,
    opening_balance: Decimal | None = None,
    page_text: dict[int, str] | None = None,
) -> Diagnosis | None:
    """Explain why a period did not reconcile, or None when it did.

    `running_balances` (aligned with `transactions`) and `opening_balance`
    enable the row-level check; `page_text` maps page number to raw text and
    lets a dropped amount be located on the page.
    """
    result = reconcile(summary, transactions)
    if result.reconciled:
        return None
    if result.diagnosis in ("no_printed_summary", "no_transaction_evidence"):
        return Diagnosis(kind=result.diagnosis, detail=_structural_detail(result.diagnosis))

    residual = result.residual
    counts_known = {"deposits_count", "withdrawals_count"} <= summary.printed_fields
    deposits = [t.deposit for t in transactions if t.deposit is not None]
    withdrawals = [t.withdrawal for t in transactions if t.withdrawal is not None]
    delta_dep = len(deposits) - summary.deposits_count
    delta_wd = len(withdrawals) - summary.withdrawals_count

    if counts_known and residual == ZERO and (delta_dep < 0 or delta_wd < 0):
        return _zero_amount_rows(delta_dep, delta_wd)

    if running_balances is not None and opening_balance is not None:
        broken = _first_chain_break(transactions, running_balances, opening_balance)
        if broken is not None:
            return broken

    if counts_known:
        identified = _from_counts(delta_dep, delta_wd, residual, page_text)
        if identified is not None:
            return identified

    return _from_residual_alone(residual, deposits + withdrawals, page_text)


def _structural_detail(kind: str) -> str:
    if kind == "no_printed_summary":
        return "the statement printed no summary block, so nothing can evidence the rows"
    return "only counts were printed; no amount on the page was compared against the rows"


def _zero_amount_rows(delta_dep: int, delta_wd: int) -> Diagnosis:
    missing = abs(min(delta_dep, 0)) + abs(min(delta_wd, 0))
    return Diagnosis(
        kind="zero_amount_rows",
        detail=f"{missing} row(s) fewer than printed, but the money is exact — "
        "they carried no amount and were skipped",
    )


def _first_chain_break(
    transactions: list[Transaction],
    running_balances: list[Decimal],
    opening_balance: Decimal,
) -> Diagnosis | None:
    """SPEC §7.10: balance[i-1] ± amount[i] == balance[i] localizes to a row."""
    previous = opening_balance
    # strict=False: a short balance list checks the rows it covers rather than
    # raising while we are already diagnosing a failure.
    pairs = zip(transactions, running_balances, strict=False)
    for index, (transaction, balance) in enumerate(pairs):
        expected = previous + transaction.signed
        if expected != balance:
            return Diagnosis(
                kind="row_level_break",
                detail=f"running balance breaks at row {index}: expected "
                f"{format_money(expected)}, statement shows {format_money(balance)}",
                row_index=index,
                amount=balance - expected,
            )
        previous = balance
    return None


#: (Δdeposits, Δwithdrawals) -> (kind, the side the row belonged on, residual per unit).
#: The last element is how many times the row's amount fits into the residual,
#: signed: a dropped deposit moves the residual by −X, a side flip by −2X.
_SIGNATURES: dict[tuple[int, int], tuple[str, str, int]] = {
    (-1, 0): ("dropped_row", DEPOSIT, -1),
    (0, -1): ("dropped_row", WITHDRAWAL, +1),
    (+1, 0): ("duplicated_row", DEPOSIT, +1),
    (0, +1): ("duplicated_row", WITHDRAWAL, -1),
    (-1, +1): ("side_flip", DEPOSIT, -2),
    (+1, -1): ("side_flip", WITHDRAWAL, +2),
}


def _from_counts(
    delta_dep: int,
    delta_wd: int,
    residual: Decimal,
    page_text: dict[int, str] | None,
) -> Diagnosis | None:
    signature = _SIGNATURES.get((delta_dep, delta_wd))
    if signature is None:
        return None
    kind, side, multiple = signature
    amount = residual / multiple
    if amount <= ZERO:
        return None  # the residual's sign contradicts the counts: not a single-row error

    page = _page_carrying(amount, page_text) if kind == "dropped_row" else None
    return Diagnosis(
        kind=kind,
        detail=_detail(kind, side, amount, page),
        side=side,
        amount=amount,
        page=page,
    )


def _from_residual_alone(
    residual: Decimal,
    amounts: list[Decimal],
    page_text: dict[int, str] | None,
) -> Diagnosis:
    """Counts were not printed: narrow the field, but never claim certainty."""
    magnitude = abs(residual)
    half = magnitude / 2

    if half in amounts:
        return Diagnosis(
            kind="side_flip",
            detail=f"residual is twice a parsed row of {format_money(half)} — "
            "that row may have landed on the wrong side",
            amount=half,
            confident=False,
        )
    if magnitude in amounts:
        return Diagnosis(
            kind="amount_matches_row",
            detail=f"residual equals a parsed row of {format_money(magnitude)} — "
            "that row may be doubled, or a different row of equal value may be missing",
            amount=magnitude,
            confident=False,
        )
    page = _page_carrying(magnitude, page_text)
    if page is not None:
        return Diagnosis(
            kind="dropped_row",
            detail=f"{format_money(magnitude)} appears on page {page} but among no parsed "
            "row — it was probably not parsed",
            amount=magnitude,
            page=page,
            confident=False,
        )
    return Diagnosis(
        kind="unknown",
        detail=f"residual {format_money(residual)} matches no single-row signature — "
        "more than one row is wrong",
        confident=False,
    )


def _page_carrying(amount: Decimal, page_text: dict[int, str] | None) -> int | None:
    """The first page whose raw text shows this amount as a money token."""
    for page in sorted(page_text or {}):
        if any(abs(token.value) == amount for token in find_money(page_text[page])):
            return page
    return None


def _detail(kind: str, side: str, amount: Decimal, page: int | None) -> str:
    money = format_money(amount)
    where = f", found on page {page}" if page is not None else ""
    if kind == "dropped_row":
        return f"a {side} of {money} was not parsed{where}"
    if kind == "duplicated_row":
        return f"a {side} of {money} was parsed twice"
    return f"a {side} of {money} landed on the other side"
