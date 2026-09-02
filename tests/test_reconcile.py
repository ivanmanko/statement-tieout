"""Reconciliation (SPEC §5).

The free oracle: two independently obtained views of one period, compared.
Every fixture here is synthetic — no PDF, no model, no network.
"""

from datetime import date
from decimal import Decimal

from statement_tieout.reconcile import (
    BALANCE_EQUATION,
    DEPOSITS_COUNT,
    DEPOSITS_TOTAL,
    PRINTED_BLOCK_CLOSES,
    WITHDRAWALS_COUNT,
    WITHDRAWALS_TOTAL,
    reconcile,
)
from statement_tieout.schema import CheckState, Summary, Transaction

ALL_PRINTED = {
    "beginning_balance",
    "ending_balance",
    "deposits_total",
    "deposits_count",
    "withdrawals_total",
    "withdrawals_count",
}


def summary(
    beginning="1000.00",
    ending="1250.00",
    dep="300.00",
    dep_n=2,
    wd="50.00",
    wd_n=1,
    printed=None,
):
    return Summary(
        beginning_balance=Decimal(beginning),
        ending_balance=Decimal(ending),
        deposits_total=Decimal(dep),
        deposits_count=dep_n,
        withdrawals_total=Decimal(wd),
        withdrawals_count=wd_n,
        printed_fields=ALL_PRINTED if printed is None else printed,
    )


def rows(*specs):
    """(\"d\", \"100.00\") -> deposit; (\"w\", \"50.00\") -> withdrawal."""
    out = []
    for i, (side, amount) in enumerate(specs, start=1):
        value = Decimal(amount)
        out.append(
            Transaction(
                date=date(2025, 1, i),
                description=f"ROW {i}",
                deposit=value if side == "d" else None,
                withdrawal=value if side == "w" else None,
            )
        )
    return out


GOOD_ROWS = rows(("d", "100.00"), ("d", "200.00"), ("w", "50.00"))


class TestEverythingAgrees:
    def test_all_six_checks_pass(self):
        result = reconcile(summary(), GOOD_ROWS)
        assert set(result.checks) == {
            PRINTED_BLOCK_CLOSES, BALANCE_EQUATION,
            DEPOSITS_TOTAL, WITHDRAWALS_TOTAL, DEPOSITS_COUNT, WITHDRAWALS_COUNT,
        }
        assert all(state is CheckState.OK for state in result.checks.values())

    def test_reconciled_with_zero_residual(self):
        result = reconcile(summary(), GOOD_ROWS)
        assert result.reconciled is True
        assert result.residual == Decimal("0.00")

    def test_reports_which_checks_carried_it(self):
        assert BALANCE_EQUATION in reconcile(summary(), GOOD_ROWS).carried_by


class TestUnavailableIsNeverOk:
    """SPEC §5 and §7.8 — the tuning file prints totals but no counts."""

    def test_absent_counts_are_unavailable_not_ok(self):
        printed = ALL_PRINTED - {"deposits_count", "withdrawals_count"}
        result = reconcile(summary(printed=printed), GOOD_ROWS)
        assert result.checks[DEPOSITS_COUNT] is CheckState.UNAVAILABLE
        assert result.checks[WITHDRAWALS_COUNT] is CheckState.UNAVAILABLE
        assert result.reconciled is True

    def test_no_printed_summary_at_all(self):
        result = reconcile(summary(printed=set()), GOOD_ROWS)
        assert all(s is CheckState.UNAVAILABLE for s in result.checks.values())
        assert result.reconciled is False
        assert result.diagnosis == "no_printed_summary"

    def test_balances_only_still_evidences_the_rows(self):
        """The common case: a statement printing only opening and closing balances."""
        result = reconcile(
            summary(printed={"beginning_balance", "ending_balance"}), GOOD_ROWS
        )
        assert result.checks[BALANCE_EQUATION] is CheckState.OK
        assert result.checks[PRINTED_BLOCK_CLOSES] is CheckState.UNAVAILABLE
        assert result.checks[DEPOSITS_TOTAL] is CheckState.UNAVAILABLE
        assert result.reconciled is True

    def test_totals_only_reconciles_without_the_balance_equation(self):
        printed = {"deposits_total", "withdrawals_total"}
        result = reconcile(summary(printed=printed), GOOD_ROWS)
        assert result.checks[BALANCE_EQUATION] is CheckState.UNAVAILABLE
        assert result.reconciled is True

    def test_counts_alone_are_not_transaction_evidence(self):
        """Nothing failed, but nothing compared the amounts either."""
        printed = {"deposits_count", "withdrawals_count"}
        result = reconcile(summary(printed=printed), GOOD_ROWS)
        assert CheckState.FAIL not in result.checks.values()
        assert result.reconciled is False
        assert result.diagnosis == "no_transaction_evidence"


class TestFailures:
    def test_dropped_deposit_row(self):
        result = reconcile(summary(), rows(("d", "100.00"), ("w", "50.00")))
        assert result.checks[DEPOSITS_TOTAL] is CheckState.FAIL
        assert result.checks[DEPOSITS_COUNT] is CheckState.FAIL
        assert result.checks[BALANCE_EQUATION] is CheckState.FAIL
        assert result.checks[PRINTED_BLOCK_CLOSES] is CheckState.OK
        assert result.residual == Decimal("-200.00")
        assert result.reconciled is False

    def test_one_cent_is_a_failure(self):
        """SPEC §7.9: tolerance is exactly zero."""
        result = reconcile(summary(), rows(("d", "100.00"), ("d", "199.99"), ("w", "50.00")))
        assert result.checks[BALANCE_EQUATION] is CheckState.FAIL
        assert result.residual == Decimal("-0.01")
        assert result.reconciled is False

    def test_side_flip_moves_the_residual_by_twice_the_amount(self):
        result = reconcile(summary(), rows(("w", "100.00"), ("d", "200.00"), ("w", "50.00")))
        assert result.residual == Decimal("-200.00")
        assert result.checks[DEPOSITS_COUNT] is CheckState.FAIL
        assert result.checks[WITHDRAWALS_COUNT] is CheckState.FAIL

    def test_printed_block_that_does_not_close_is_reported(self):
        """The bank's own totals disagree with its own balances (ADR-001)."""
        result = reconcile(summary(ending="1300.00"), GOOD_ROWS)
        assert result.checks[PRINTED_BLOCK_CLOSES] is CheckState.FAIL
        assert result.checks[DEPOSITS_TOTAL] is CheckState.OK
        assert result.checks[WITHDRAWALS_TOTAL] is CheckState.OK
        assert result.reconciled is False


class TestEmptyPeriod:
    """SPEC §6 edge case 8."""

    def test_reconciles_when_balances_are_equal(self):
        s = summary(beginning="1000.00", ending="1000.00", dep="0.00", dep_n=0, wd="0.00", wd_n=0)
        assert reconcile(s, []).reconciled is True

    def test_fails_when_balances_differ(self):
        s = summary(beginning="1000.00", ending="1001.00", dep="0.00", dep_n=0, wd="0.00", wd_n=0)
        result = reconcile(s, [])
        assert result.reconciled is False
        assert result.residual == Decimal("-1.00")
