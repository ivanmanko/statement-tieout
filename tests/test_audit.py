"""The audit view (SPEC §8).

An auditor's three questions, in order: is this the whole population, where
did each number come from, and is this exception the tool's or the statement's.
The view answers them or says it cannot — it never implies completeness it has
not established.

Pure: it reads an `ExtractResult` and computes nothing of its own.
"""

from datetime import date
from decimal import Decimal

import pytest

from statement_tieout.audit import Attribution, Verdict, audit
from statement_tieout.schema import (
    Account,
    DateRange,
    Extraction,
    ExtractResult,
    PeriodResult,
    Reconciliation,
    Summary,
    Transaction,
)

ALL_PRINTED = {
    "beginning_balance", "ending_balance", "deposits_total",
    "deposits_count", "withdrawals_total", "withdrawals_count",
}
NO_COUNTS = ALL_PRINTED - {"deposits_count", "withdrawals_count"}


def summary(dep="300.00", dep_n=2, wd="50.00", wd_n=1, printed=ALL_PRINTED):
    return Summary(
        beginning_balance=Decimal("1000.00"), ending_balance=Decimal("1250.00"),
        deposits_total=Decimal(dep), deposits_count=dep_n,
        withdrawals_total=Decimal(wd), withdrawals_count=wd_n,
        printed_fields=printed,
    )


def rows(*specs, recovered_at=()):
    out = []
    for i, (side, amount) in enumerate(specs):
        value = Decimal(amount)
        out.append(Transaction(
            date=date(2025, 1, i + 1), description=f"ROW {i}",
            deposit=value if side == "d" else None,
            withdrawal=value if side == "w" else None,
            page=1, line=i, recovered=i in recovered_at,
        ))
    return out


def result(period_summary, transactions, reconciliation, warnings=()):
    period = PeriodResult(
        account=Account(bank="ACME BANK", account_last4="4071",
                        period=DateRange(start=date(2025, 1, 1), end=date(2025, 1, 31))),
        summary=period_summary, transactions=transactions, reconciliation=reconciliation,
    )
    return ExtractResult.from_periods([period], Extraction(warnings=list(warnings)))


GOOD = rows(("d", "100.00"), ("d", "200.00"), ("w", "50.00"))


class TestCompleteness:
    """The first question: may this population be sampled at all?"""

    def test_printed_counts_bound_the_population(self):
        report = audit(result(summary(), GOOD, Reconciliation.reconciled_on({"balance_equation"})))
        assert report.periods[0].completeness.bounded is True

    def test_absent_counts_leave_it_unbounded(self):
        report = audit(result(summary(printed=NO_COUNTS), GOOD,
                              Reconciliation.reconciled_on({"balance_equation"})))
        completeness = report.periods[0].completeness
        assert completeness.bounded is False
        assert "cannot" in completeness.statement.casefold()

    def test_a_shortfall_is_quantified(self):
        short = rows(("d", "100.00"), ("w", "50.00"))
        short_result = Reconciliation(reconciled=False, checks={}, residual=Decimal("-200.00"))
        report = audit(result(summary(), short, short_result))
        completeness = report.periods[0].completeness
        assert completeness.missing_deposits == 1
        assert completeness.missing_withdrawals == 0

    def test_an_unbounded_population_says_so_in_the_next_steps(self):
        report = audit(result(summary(printed=NO_COUNTS), GOOD,
                              Reconciliation.reconciled_on({"balance_equation"})))
        assert any("sampl" in step.casefold() for step in report.periods[0].next_steps)


class TestAttribution:
    """The third question: whose exception is this?"""

    def test_a_named_extraction_cause_is_attributed_to_the_tool(self):
        report = audit(result(
            summary(), rows(("d", "100.00"), ("w", "50.00")),
            Reconciliation(reconciled=False, checks={}, residual=Decimal("-200.00"),
                           diagnosis="dropped_row",
                           detail="a deposit of 200.00 was not parsed, found on page 4"),
        ))
        period = report.periods[0]
        assert period.attribution is Attribution.EXTRACTION_UNCERTAINTY
        assert period.extraction_uncertainty == Decimal("-200.00")
        assert period.statement_inconsistency == Decimal("0.00")

    def test_a_clean_read_with_a_residual_blames_the_statement(self):
        """No outstanding doubt, so the document is the one that disagrees."""
        report = audit(result(
            summary(), GOOD,
            Reconciliation(reconciled=False, checks={}, residual=Decimal("-0.01"),
                           diagnosis=None, detail=None),
        ))
        period = report.periods[0]
        assert period.attribution is Attribution.STATEMENT_INCONSISTENCY
        assert period.statement_inconsistency == Decimal("-0.01")

    def test_doubt_outstanding_means_the_document_is_not_blamed(self):
        report = audit(result(
            summary(), GOOD,
            Reconciliation(reconciled=False, checks={}, residual=Decimal("-0.01")),
            warnings=["3 row(s) carried a zero amount and were skipped"],
        ))
        assert report.periods[0].attribution is Attribution.UNEXPLAINED

    def test_an_unknown_diagnosis_is_unexplained(self):
        report = audit(result(
            summary(), GOOD,
            Reconciliation(reconciled=False, checks={}, residual=Decimal("-99.00"),
                           diagnosis="unknown", detail="matches no single-row signature"),
        ))
        assert report.periods[0].attribution is Attribution.UNEXPLAINED


class TestDoubtItems:
    """Things that do not move the residual but do affect reliability."""

    def test_inferred_amounts_are_listed(self):
        report = audit(result(
            summary(dep="120.00", dep_n=2), rows(("d", "100.00"), ("d", "20.00"),
                                                 ("w", "50.00"), recovered_at=(1,)),
            Reconciliation.reconciled_on({"balance_equation"}),
        ))
        doubts = [e for e in report.periods[0].exceptions
                  if e.kind is Attribution.EXTRACTION_UNCERTAINTY]
        assert any("inferred" in e.description.casefold() for e in doubts)
        assert any(e.page == 1 for e in doubts)

    def test_a_skipped_line_is_listed_from_the_warnings(self):
        report = audit(result(
            summary(), GOOD, Reconciliation.reconciled_on({"balance_equation"}),
            warnings=["14 line(s) held several date-and-amount pairs and were read as a "
                      "multi-column summary table rather than as transactions"],
        ))
        assert any("summary table" in e.description for e in report.periods[0].exceptions)


class TestVerdicts:
    def test_a_clean_bounded_period_is_tied(self):
        report = audit(result(summary(), GOOD, Reconciliation.reconciled_on({"balance_equation"})))
        assert report.periods[0].verdict is Verdict.TIED

    def test_reconciled_but_unbounded_carries_notes(self):
        report = audit(result(summary(printed=NO_COUNTS), GOOD,
                              Reconciliation.reconciled_on({"balance_equation"})))
        assert report.periods[0].verdict is Verdict.TIED_WITH_NOTES

    def test_a_named_discrepancy_is_an_identified_exception(self):
        report = audit(result(
            summary(), rows(("d", "100.00"), ("w", "50.00")),
            Reconciliation(reconciled=False, checks={}, residual=Decimal("-200.00"),
                           diagnosis="dropped_row", detail="a deposit of 200.00 was not parsed"),
        ))
        assert report.periods[0].verdict is Verdict.EXCEPTIONS_IDENTIFIED

    def test_an_unexplained_residual_is_not_tied(self):
        report = audit(result(
            summary(), GOOD,
            Reconciliation(reconciled=False, checks={}, residual=Decimal("-99.00"),
                           diagnosis="unknown"),
        ))
        assert report.periods[0].verdict is Verdict.NOT_TIED

    @pytest.mark.parametrize("verdict", list(Verdict))
    def test_every_verdict_says_what_to_do_next(self, verdict):
        assert Verdict(verdict).next_step_hint


class TestWholeFile:
    def test_the_file_verdict_is_the_weakest_period(self):
        report = audit(result(
            summary(), GOOD,
            Reconciliation(reconciled=False, checks={}, residual=Decimal("-99.00"),
                           diagnosis="unknown"),
        ))
        assert report.verdict is Verdict.NOT_TIED

    def test_it_serializes(self):
        report = audit(result(summary(), GOOD, Reconciliation.reconciled_on({"balance_equation"})))
        dumped = report.model_dump(mode="json")
        assert dumped["verdict"] == "tied"
        assert dumped["periods"][0]["completeness"]["bounded"] is True
