"""Reading the printed header: account identity and the summary block.

SPEC §7.5 (labels, scope, counts) and §7.15 (bank, account, period). ADR-001
is why this exists at all: the summary is *read*, not computed, so that
reconciliation compares two independent things.
"""

from datetime import date
from decimal import Decimal

from statement_tieout.parse.header import build_summary, read_header
from statement_tieout.schema import Transaction

GREAT_LAKES = [
    "GREAT LAKES COMMERCE BANK",
    "Business Checking Statement",
    "NORTHGATE SURGICAL INSTITUTE LLC - Account ****4071",
    "Statement period: January 01, 2025 - January 28, 2025",
    "Beginning balance $2,014,882.47",
    "Deposits and credits $2,180,764.90",
    "Withdrawals and debits $2,154,309.25",
    "Ending balance $2,041,338.12",
    "Date Description Amount Balance",
]


class TestSummaryBlock:
    def test_reads_the_four_printed_totals(self):
        reading = read_header(GREAT_LAKES)
        assert reading.beginning_balance == Decimal("2014882.47")
        assert reading.ending_balance == Decimal("2041338.12")
        assert reading.deposits_total == Decimal("2180764.90")
        assert reading.withdrawals_total == Decimal("2154309.25")

    def test_absent_counts_stay_none(self):
        reading = read_header(GREAT_LAKES)
        assert reading.deposits_count is None
        assert reading.withdrawals_count is None

    def test_printed_fields_name_exactly_what_was_found(self):
        assert read_header(GREAT_LAKES).printed_fields == {
            "beginning_balance", "ending_balance", "deposits_total", "withdrawals_total",
        }

    def test_a_count_is_read_when_the_line_is_unambiguous(self):
        reading = read_header(["Deposits and credits 81 $1,214,254.05"])
        assert reading.deposits_count == 81
        assert reading.deposits_total == Decimal("1214254.05")

    def test_two_bare_integers_leave_the_count_unread(self):
        """SPEC §7.5: a guessed count would make reconciliation lie."""
        reading = read_header(["Deposits and credits 81 items over 3 days $1,214,254.05"])
        assert reading.deposits_count is None
        assert reading.deposits_total == Decimal("1214254.05")

    def test_synonym_labels(self):
        reading = read_header(
            ["Previous balance 1,000.00", "New balance 1,200.00", "Total deposits 200.00"]
        )
        assert reading.beginning_balance == Decimal("1000.00")
        assert reading.ending_balance == Decimal("1200.00")
        assert reading.deposits_total == Decimal("200.00")

    def test_longer_label_is_not_consumed_by_a_shorter_one(self):
        reading = read_header(["Withdrawals and debits $2,154,309.25"])
        assert reading.withdrawals_total == Decimal("2154309.25")
        assert reading.deposits_total is None

    def test_the_last_money_token_on_the_line_is_the_amount(self):
        reading = read_header(["Beginning balance as of 01/01 was 1.00 ... 2,014,882.47"])
        assert reading.beginning_balance == Decimal("2014882.47")

    def test_no_summary_block_at_all(self):
        reading = read_header(["GREAT LAKES COMMERCE BANK", "Date Description Amount"])
        assert reading.printed_fields == set()
        assert reading.beginning_balance is None

    def test_body_is_searched_only_when_the_header_lacks_the_label(self):
        """SPEC §7.5 scope rule."""
        reading = read_header(
            ["GREAT LAKES COMMERCE BANK"],
            body_lines=["Ending balance 2,041,338.12"],
        )
        assert reading.ending_balance == Decimal("2041338.12")

    def test_a_transaction_description_in_the_body_does_not_win(self):
        reading = read_header(
            ["Ending balance 2,041,338.12"],
            body_lines=["01/03/2025 LOCKBOX DEPOSITS AND CREDITS 8,193.03 1,910,532.35"],
        )
        assert reading.ending_balance == Decimal("2041338.12")
        assert reading.deposits_total is None


class TestAccountIdentity:
    def test_bank_is_the_letterhead(self):
        assert read_header(GREAT_LAKES).account.bank == "GREAT LAKES COMMERCE BANK"

    def test_account_last4_from_a_masked_token(self):
        assert read_header(GREAT_LAKES).account.account_last4 == "4071"

    def test_account_last4_from_a_labelled_line(self):
        reading = read_header(["Account ending in 6426"])
        assert reading.account.account_last4 == "6426"

    def test_account_last4_from_an_x_mask(self):
        assert read_header(["Checking xxxx4664"]).account.account_last4 == "4664"

    def test_period_start_and_end(self):
        period = read_header(GREAT_LAKES).account.period
        assert period.start == date(2025, 1, 1)
        assert period.end == date(2025, 1, 28)

    def test_slash_dates_in_the_period_line(self):
        period = read_header(["Statement period 04/01/2025 - 04/30/2025"]).account.period
        assert (period.start, period.end) == (date(2025, 4, 1), date(2025, 4, 30))

    def test_a_single_date_fills_only_the_end(self):
        period = read_header(["Statement date: April 30, 2025"]).account.period
        assert period.start is None
        assert period.end == date(2025, 4, 30)

    def test_a_line_with_digits_is_not_the_bank_name(self):
        reading = read_header(["Account 1234567890", "IXONIA BANK", "Beginning balance 1.00"])
        assert reading.account.bank == "IXONIA BANK"

    def test_nothing_recognisable_yields_nulls_not_guesses(self):
        reading = read_header(["1234567890", "$5.00"])
        assert reading.account.bank is None
        assert reading.account.account_last4 is None


class TestBuildSummary:
    """SPEC §7.8: derived numbers are never presented as printed."""

    def rows(self):
        return [
            Transaction(date=date(2025, 1, 1), description="A", deposit=Decimal("80.00")),
            Transaction(date=date(2025, 1, 2), description="B", deposit=Decimal("20.00")),
            Transaction(date=date(2025, 1, 3), description="C", withdrawal=Decimal("30.00")),
        ]

    def test_printed_values_are_kept(self):
        summary = build_summary(read_header(GREAT_LAKES), self.rows())
        assert summary.beginning_balance == Decimal("2014882.47")
        assert "beginning_balance" in summary.printed_fields

    def test_absent_counts_are_derived_and_not_marked_printed(self):
        summary = build_summary(read_header(GREAT_LAKES), self.rows())
        assert summary.deposits_count == 2
        assert summary.withdrawals_count == 1
        assert "deposits_count" not in summary.printed_fields

    def test_absent_totals_are_derived_from_the_rows(self):
        summary = build_summary(read_header(["ACME BANK"]), self.rows())
        assert summary.deposits_total == Decimal("100.00")
        assert summary.withdrawals_total == Decimal("30.00")
        assert summary.printed_fields == set()

    def test_derived_balances_fall_back_to_zero_and_the_row_total(self):
        summary = build_summary(read_header(["ACME BANK"]), self.rows())
        assert summary.beginning_balance == Decimal("0.00")
        assert summary.ending_balance == Decimal("70.00")
