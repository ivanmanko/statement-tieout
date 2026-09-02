"""Residual diagnosis (SPEC §5.1).

Turning "period 3 did not reconcile" into "a deposit of 1,240.50 was not
parsed, and it appears on page 47". All synthetic — no PDF, no model.
"""

from datetime import date
from decimal import Decimal

from statement_tieout.reconcile import diagnose
from statement_tieout.schema import Summary, Transaction

ALL_PRINTED = {
    "beginning_balance", "ending_balance",
    "deposits_total", "deposits_count",
    "withdrawals_total", "withdrawals_count",
}
NO_COUNTS = ALL_PRINTED - {"deposits_count", "withdrawals_count"}


def summary(dep="300.00", dep_n=2, wd="50.00", wd_n=1, printed=ALL_PRINTED):
    """A period whose printed block closes: 1000 + 300 - 50 = 1250."""
    return Summary(
        beginning_balance=Decimal("1000.00"),
        ending_balance=Decimal("1250.00"),
        deposits_total=Decimal(dep),
        deposits_count=dep_n,
        withdrawals_total=Decimal(wd),
        withdrawals_count=wd_n,
        printed_fields=printed,
    )


def rows(*specs):
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


GOOD = rows(("d", "100.00"), ("d", "200.00"), ("w", "50.00"))


class TestNothingToDiagnose:
    def test_reconciled_period_has_no_diagnosis(self):
        assert diagnose(summary(), GOOD) is None


class TestCountsPrinted:
    """SPEC §5.1 signature 3 — two integers and one Decimal identify the row."""

    def test_dropped_deposit(self):
        result = diagnose(summary(), rows(("d", "100.00"), ("w", "50.00")))
        assert result.kind == "dropped_row"
        assert result.side == "deposit"
        assert result.amount == Decimal("200.00")

    def test_dropped_withdrawal(self):
        result = diagnose(summary(), rows(("d", "100.00"), ("d", "200.00")))
        assert result.kind == "dropped_row"
        assert result.side == "withdrawal"
        assert result.amount == Decimal("50.00")

    def test_duplicated_deposit(self):
        result = diagnose(
            summary(), rows(("d", "100.00"), ("d", "200.00"), ("d", "75.00"), ("w", "50.00"))
        )
        assert result.kind == "duplicated_row"
        assert result.side == "deposit"
        assert result.amount == Decimal("75.00")

    def test_duplicated_withdrawal(self):
        result = diagnose(
            summary(), rows(("d", "100.00"), ("d", "200.00"), ("w", "50.00"), ("w", "12.00"))
        )
        assert result.kind == "duplicated_row"
        assert result.side == "withdrawal"
        assert result.amount == Decimal("12.00")

    def test_deposit_parsed_as_withdrawal(self):
        result = diagnose(summary(), rows(("w", "100.00"), ("d", "200.00"), ("w", "50.00")))
        assert result.kind == "side_flip"
        assert result.side == "deposit"
        assert result.amount == Decimal("100.00")

    def test_withdrawal_parsed_as_deposit(self):
        result = diagnose(summary(), rows(("d", "100.00"), ("d", "200.00"), ("d", "50.00")))
        assert result.kind == "side_flip"
        assert result.side == "withdrawal"
        assert result.amount == Decimal("50.00")

    def test_dropped_row_names_the_page_when_the_amount_is_on_it(self):
        result = diagnose(
            summary(),
            rows(("d", "100.00"), ("w", "50.00")),
            page_text={1: "01/02 SOMETHING 100.00", 4: "01/05 CIGNA CLAIMS 200.00 1,234.00"},
        )
        assert result.kind == "dropped_row"
        assert result.page == 4

    def test_zero_amount_rows_when_residual_is_clean(self):
        """Counts short but the money is right: skipped rows carried no amount."""
        s = summary(dep="300.00", dep_n=3, wd="50.00", wd_n=1)
        result = diagnose(s, GOOD)
        assert result.kind == "zero_amount_rows"


class TestRunningBalanceOutranksCounts:
    """SPEC §5.1 signature 2 — a row index beats a period-level inference."""

    def test_chain_break_is_reported_with_the_row(self):
        result = diagnose(
            summary(),
            rows(("d", "100.00"), ("w", "50.00")),
            running_balances=[Decimal("1100.00"), Decimal("1234.00")],
            opening_balance=Decimal("1000.00"),
        )
        assert result.kind == "row_level_break"
        assert result.row_index == 1

    def test_unbroken_chain_falls_through_to_the_count_signature(self):
        result = diagnose(
            summary(),
            rows(("d", "100.00"), ("w", "50.00")),
            running_balances=[Decimal("1100.00"), Decimal("1050.00")],
            opening_balance=Decimal("1000.00"),
        )
        assert result.kind == "dropped_row"


class TestCountsUnavailable:
    """SPEC §5.1 signature 4 — the tuning file prints no counts."""

    def test_double_a_row_amount_suggests_a_side_flip(self):
        result = diagnose(
            summary(printed=NO_COUNTS),
            rows(("w", "100.00"), ("d", "200.00"), ("w", "50.00")),
        )
        assert result.kind == "side_flip"
        assert result.amount == Decimal("100.00")
        assert result.confident is False

    def test_matching_a_row_amount_is_reported_as_ambiguous(self):
        result = diagnose(
            summary(printed=NO_COUNTS),
            rows(("d", "100.00"), ("d", "200.00"), ("d", "200.00"), ("w", "50.00")),
        )
        assert result.kind == "amount_matches_row"
        assert result.amount == Decimal("200.00")
        assert result.confident is False

    def test_amount_only_on_the_page_suggests_a_dropped_row(self):
        result = diagnose(
            summary(printed=NO_COUNTS),
            rows(("d", "100.00"), ("w", "50.00")),
            page_text={7: "01/09 AETNA REMITTANCE ACH 200.00 1,876.00"},
        )
        assert result.kind == "dropped_row"
        assert result.amount == Decimal("200.00")
        assert result.page == 7
        assert result.confident is False

    def test_unknown_when_nothing_matches(self):
        result = diagnose(
            summary(printed=NO_COUNTS),
            rows(("d", "100.00"), ("d", "123.45"), ("w", "50.00")),
        )
        assert result.kind == "unknown"
        assert result.amount is None


class TestDetailIsHumanReadable:
    def test_detail_names_amount_and_side(self):
        result = diagnose(summary(), rows(("d", "100.00"), ("w", "50.00")))
        assert "200.00" in result.detail
        assert "deposit" in result.detail
