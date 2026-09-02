"""The output contract (SPEC §3).

`ExtractResult` is the single source of truth for the shape; these tests pin
the invariants that are real code rather than documentation.
"""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from statement_tieout.schema import (
    Account,
    CheckState,
    DateRange,
    Extraction,
    ExtractResult,
    PeriodResult,
    Reconciliation,
    Summary,
    Transaction,
)


def a_transaction(deposit=None, withdrawal=None, day=1, description="ACH - MEDTRONIC INC"):
    return Transaction(
        date=date(2025, 1, day),
        description=description,
        deposit=deposit,
        withdrawal=withdrawal,
    )


def a_summary(beginning="100.00", ending="150.00", dep="80.00", dep_n=2, wd="30.00", wd_n=1):
    return Summary(
        beginning_balance=Decimal(beginning),
        ending_balance=Decimal(ending),
        deposits_total=Decimal(dep),
        deposits_count=dep_n,
        withdrawals_total=Decimal(wd),
        withdrawals_count=wd_n,
    )


def a_period(summary=None, transactions=(), start=date(2025, 1, 1), end=date(2025, 1, 31)):
    return PeriodResult(
        account=Account(bank="Great Lakes", account_last4="4071",
                        period=DateRange(start=start, end=end)),
        summary=summary or a_summary(),
        transactions=list(transactions),
        reconciliation=Reconciliation.reconciled_on({"A", "B", "C"}),
    )


class TestTransactionSides:
    """SPEC §3 invariant 1: sign lives in the field choice, never in the value."""

    def test_deposit_only_is_valid(self):
        assert a_transaction(deposit=Decimal("10.00")).withdrawal is None

    def test_withdrawal_only_is_valid(self):
        assert a_transaction(withdrawal=Decimal("10.00")).deposit is None

    def test_both_sides_null_is_rejected(self):
        with pytest.raises(ValidationError):
            a_transaction()

    def test_both_sides_set_is_rejected(self):
        with pytest.raises(ValidationError):
            a_transaction(deposit=Decimal("10.00"), withdrawal=Decimal("5.00"))

    @pytest.mark.parametrize("amount", ["0.00", "-10.00"])
    def test_non_positive_amount_is_rejected(self, amount):
        with pytest.raises(ValidationError):
            a_transaction(deposit=Decimal(amount))

    def test_signed_amount_helper_picks_the_side(self):
        assert a_transaction(deposit=Decimal("10.00")).signed == Decimal("10.00")
        assert a_transaction(withdrawal=Decimal("10.00")).signed == Decimal("-10.00")


class TestSerialization:
    """SPEC §3 invariant 6."""

    def test_money_serializes_with_two_decimals(self):
        dumped = a_summary(beginning="100.5").model_dump(mode="json")
        assert dumped["beginning_balance"] == 100.50
        assert isinstance(dumped["beginning_balance"], float)

    def test_dates_serialize_iso(self):
        dumped = a_transaction(deposit=Decimal("1.00"), day=28).model_dump(mode="json")
        assert dumped["date"] == "2025-01-28"

    def test_absent_side_stays_null(self):
        assert a_transaction(deposit=Decimal("1.00")).model_dump(mode="json")["withdrawal"] is None


class TestReconciliationStates:
    """SPEC §5: a check whose input is missing is never `ok`."""

    def test_unavailable_is_not_ok(self):
        rec = Reconciliation(
            reconciled=False,
            checks={"A": CheckState.UNAVAILABLE, "B": CheckState.UNAVAILABLE},
            residual=Decimal("0.00"),
        )
        assert rec.checks["A"] is not CheckState.OK
        assert rec.carried_by == set()

    def test_reconciled_records_which_checks_carried_it(self):
        rec = Reconciliation.reconciled_on({"A", "B", "C"})
        assert rec.reconciled is True
        assert rec.carried_by == {"A", "B", "C"}

    def test_cannot_be_reconciled_with_no_ok_check(self):
        with pytest.raises(ValidationError):
            Reconciliation(
                reconciled=True,
                checks={"A": CheckState.UNAVAILABLE},
                residual=Decimal("0.00"),
            )


class TestSinglePeriodMirrors:
    """SPEC §3 invariant 2: the assignment's single-period example holds."""

    def test_top_level_is_the_only_period(self):
        txs = [a_transaction(deposit=Decimal("80.00")), a_transaction(withdrawal=Decimal("30.00"))]
        period = a_period(transactions=txs)
        result = ExtractResult.from_periods([period], Extraction())

        assert result.summary == period.summary
        assert result.account == period.account
        assert result.transactions == period.transactions
        assert result.reconciliation.reconciled is True


class TestAggregateAcrossPeriods:
    """SPEC §3 invariant 3."""

    def build(self):
        first = a_period(
            summary=a_summary("100.00", "150.00", "80.00", 2, "30.00", 1),
            transactions=[a_transaction(deposit=Decimal("80.00"), day=2)],
            start=date(2025, 1, 1), end=date(2025, 1, 31),
        )
        second = a_period(
            summary=a_summary("150.00", "175.00", "40.00", 1, "15.00", 3),
            transactions=[a_transaction(withdrawal=Decimal("15.00"), day=3)],
            start=date(2025, 2, 1), end=date(2025, 2, 28),
        )
        return first, second

    def test_balances_come_from_the_ends(self):
        result = ExtractResult.from_periods(list(self.build()), Extraction())
        assert result.summary.beginning_balance == Decimal("100.00")
        assert result.summary.ending_balance == Decimal("175.00")

    def test_totals_and_counts_are_summed(self):
        result = ExtractResult.from_periods(list(self.build()), Extraction())
        assert result.summary.deposits_total == Decimal("120.00")
        assert result.summary.deposits_count == 3
        assert result.summary.withdrawals_total == Decimal("45.00")
        assert result.summary.withdrawals_count == 4

    def test_transactions_concatenate_in_document_order(self):
        result = ExtractResult.from_periods(list(self.build()), Extraction())
        assert [t.signed for t in result.transactions] == [Decimal("80.00"), Decimal("-15.00")]

    def test_period_spans_first_start_to_last_end(self):
        result = ExtractResult.from_periods(list(self.build()), Extraction())
        assert result.account.period.start == date(2025, 1, 1)
        assert result.account.period.end == date(2025, 2, 28)

    def test_reconciled_is_the_and_over_periods(self):
        first, second = self.build()
        second.reconciliation = Reconciliation(
            reconciled=False,
            checks={"A": CheckState.FAIL, "B": CheckState.OK},
            residual=Decimal("-12.50"),
        )
        result = ExtractResult.from_periods([first, second], Extraction())
        assert result.reconciliation.reconciled is False
        assert result.reconciliation.checks["A"] is CheckState.FAIL
        assert result.reconciliation.residual == Decimal("-12.50")

    def test_aggregate_check_is_worst_state_across_periods(self):
        first, second = self.build()
        second.reconciliation = Reconciliation(
            reconciled=True,
            checks={"A": CheckState.OK, "B": CheckState.UNAVAILABLE, "C": CheckState.OK},
            residual=Decimal("0.00"),
        )
        result = ExtractResult.from_periods([first, second], Extraction())
        assert result.reconciliation.checks["B"] is CheckState.UNAVAILABLE
        assert result.reconciliation.checks["A"] is CheckState.OK

    def test_at_least_one_period_is_required(self):
        with pytest.raises(ValueError):
            ExtractResult.from_periods([], Extraction())
