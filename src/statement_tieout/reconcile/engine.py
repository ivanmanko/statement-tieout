"""The six reconciliation checks (SPEC §5).

Nothing here reads a PDF or calls a model. It compares two views of one
period — what the statement printed, and what the row parser produced — and
says which of them can be evidenced against the other. That comparison costs
nothing and needs no ground truth, which is why it, rather than any model, is
the thing the extraction ladder climbs against.
"""

from __future__ import annotations

from ..money import ZERO
from ..schema import CheckState, Reconciliation, Summary, Transaction

PRINTED_BLOCK_CLOSES = "printed_block_closes"
BALANCE_EQUATION = "balance_equation"
DEPOSITS_TOTAL = "deposits_total"
WITHDRAWALS_TOTAL = "withdrawals_total"
DEPOSITS_COUNT = "deposits_count"
WITHDRAWALS_COUNT = "withdrawals_count"

#: Declaration order, which is also the order they appear in the output.
CHECKS = (
    PRINTED_BLOCK_CLOSES,
    BALANCE_EQUATION,
    DEPOSITS_TOTAL,
    WITHDRAWALS_TOTAL,
    DEPOSITS_COUNT,
    WITHDRAWALS_COUNT,
)

#: Which printed summary fields each check needs before it can run at all.
_REQUIRES: dict[str, frozenset[str]] = {
    PRINTED_BLOCK_CLOSES: frozenset(
        {"beginning_balance", "ending_balance", "deposits_total", "withdrawals_total"}
    ),
    BALANCE_EQUATION: frozenset({"beginning_balance", "ending_balance"}),
    DEPOSITS_TOTAL: frozenset({"deposits_total"}),
    WITHDRAWALS_TOTAL: frozenset({"withdrawals_total"}),
    DEPOSITS_COUNT: frozenset({"deposits_count"}),
    WITHDRAWALS_COUNT: frozenset({"withdrawals_count"}),
}


def _state(available: bool, passed: bool) -> CheckState:
    if not available:
        return CheckState.UNAVAILABLE
    return CheckState.OK if passed else CheckState.FAIL


def reconcile(summary: Summary, transactions: list[Transaction]) -> Reconciliation:
    """Compare the printed summary with the parsed transactions.

    `summary.printed_fields` says which summary values actually came off the
    page; anything derived from the transactions cannot evidence them, so the
    checks that depend on it report `unavailable` rather than `ok`.
    """
    printed = summary.printed_fields
    deposits = [t.deposit for t in transactions if t.deposit is not None]
    withdrawals = [t.withdrawal for t in transactions if t.withdrawal is not None]
    parsed_deposits = sum(deposits, ZERO)
    parsed_withdrawals = sum(withdrawals, ZERO)

    residual = (
        summary.beginning_balance
        + parsed_deposits
        - parsed_withdrawals
        - summary.ending_balance
    )

    outcomes: dict[str, bool] = {
        PRINTED_BLOCK_CLOSES: (
            summary.beginning_balance + summary.deposits_total - summary.withdrawals_total
            == summary.ending_balance
        ),
        BALANCE_EQUATION: residual == ZERO,
        DEPOSITS_TOTAL: parsed_deposits == summary.deposits_total,
        WITHDRAWALS_TOTAL: parsed_withdrawals == summary.withdrawals_total,
        DEPOSITS_COUNT: len(deposits) == summary.deposits_count,
        WITHDRAWALS_COUNT: len(withdrawals) == summary.withdrawals_count,
    }
    checks = {
        name: _state(_REQUIRES[name] <= printed, outcomes[name]) for name in CHECKS
    }

    reconciled = _is_reconciled(checks)
    return Reconciliation(
        reconciled=reconciled,
        checks=checks,
        residual=residual,
        diagnosis=None if reconciled else _structural_diagnosis(checks),
    )


def _is_reconciled(checks: dict[str, CheckState]) -> bool:
    """SPEC §5: no failure, plus at least one line of evidence about the rows."""
    if CheckState.FAIL in checks.values():
        return False
    if checks[BALANCE_EQUATION] is CheckState.OK:
        return True
    return (
        checks[DEPOSITS_TOTAL] is CheckState.OK
        and checks[WITHDRAWALS_TOTAL] is CheckState.OK
    )


def _structural_diagnosis(checks: dict[str, CheckState]) -> str | None:
    """Name the two ways a period fails without any check actually failing."""
    if CheckState.FAIL in checks.values():
        return None  # an arithmetic failure; residual diagnosis handles it
    if all(state is CheckState.UNAVAILABLE for state in checks.values()):
        return "no_printed_summary"
    return "no_transaction_evidence"
