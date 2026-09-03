"""Reading the printed header: account identity and the summary block.

SPEC §7.5 (labels, scope, counts) and §7.15 (bank, account, period). ADR-001
is why this exists at all: the summary is *read*, not computed, so that
reconciliation compares two independent things.
"""

from datetime import date
from decimal import Decimal

from statement_tieout.parse.header import build_summary, read_header
from statement_tieout.schema import Transaction

from .helpers import big, line, mixed

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


class TestHorizontalSummaryBlock:
    """SPEC §7.5 — labels on one row, amounts on the next, matched by column.

    Fulton Bank prints its summary this way, and so do Chase and BofA. A
    reader that only knows the vertical layout finds no summary at all.
    """

    def block(self):
        return [
            line(100.0, (50.0, "Prior Statement Balance"),
                 (200.0, "Total Deposits/Credits"),
                 (330.0, "Total Checks/Debits"),
                 (450.0, "Ending Statement Balance")),
            line(112.0, (55.0, "$1,908,989.60"), (205.0, "$4,351,230.63"),
                 (335.0, "$4,384,606.59"), (455.0, "$1,875,613.64")),
        ]

    def test_each_amount_is_matched_to_the_label_above_it(self):
        reading = read_header(self.block())
        assert reading.beginning_balance == Decimal("1908989.60")
        assert reading.deposits_total == Decimal("4351230.63")
        assert reading.withdrawals_total == Decimal("4384606.59")
        assert reading.ending_balance == Decimal("1875613.64")

    def test_all_four_are_marked_printed(self):
        assert read_header(self.block()).printed_fields == {
            "beginning_balance", "ending_balance", "deposits_total", "withdrawals_total",
        }

    def test_a_label_row_needs_at_least_two_labels(self):
        """One label and no amount is a stray word, not a summary block."""
        rows = [line(100.0, (50.0, "Total Deposits/Credits")), line(112.0, (55.0, "$1.00"))]
        assert read_header(rows).deposits_total is None

    def test_the_vertical_layout_still_works_when_words_are_given(self):
        rows = [line(100.0, (50.0, "Beginning balance"), (300.0, "$2,014,882.47"))]
        assert read_header(rows).beginning_balance == Decimal("2014882.47")


class TestWhitespaceInsensitiveLabels:
    """SPEC §7.5 — OCR loses spaces, and a label split differently is the same label."""

    def test_a_label_run_together_still_matches(self):
        assert read_header(["PriorStatementBalance 1,908,989.60"]).beginning_balance == Decimal(
            "1908989.60"
        )

    def test_new_labels_from_the_fulton_statement(self):
        reading = read_header([
            "Prior Statement Balance 1,908,989.60",
            "Ending Statement Balance 1,875,613.64",
        ])
        assert reading.beginning_balance == Decimal("1908989.60")
        assert reading.ending_balance == Decimal("1875613.64")


class TestLetterheadBySize:
    """SPEC §7.15 — the bank name is the biggest text, not the first tidy line."""

    def test_the_largest_line_wins_even_with_a_postcode(self):
        rows = [
            line(60.0, (300.0, "532 85")),
            line(72.0, (50.0, "P.O.Box 4887 Page 1 of 15")),
            big(84.0, 50.0, "Fulton Bank"),
            line(96.0, (200.0, "Lancaster, PA 17604")),
            line(108.0, (300.0, "Statement Date: 04/01/21 through 04/30/21")),
        ]
        assert read_header(rows).account.bank == "Fulton Bank"

    def test_a_labelled_field_is_never_the_letterhead(self):
        rows = [big(60.0, 50.0, "Statement Date: 04/01/21 through 04/30/21"),
                line(72.0, (50.0, "IXONIA BANK"))]
        assert read_header(rows).account.bank == "IXONIA BANK"

    def test_without_sizes_the_first_plain_line_is_used(self):
        assert read_header(["GREAT LAKES COMMERCE BANK", "Beginning balance 1.00"]).account.bank \
            == "GREAT LAKES COMMERCE BANK"


class TestAccountMaskGuard:
    """SPEC §7.15 — `Box` ends in `x`, and `P.O.Box 4887` is not an account number."""

    def test_a_po_box_is_not_an_account(self):
        reading = read_header(["P.O.Box 4887 Page 1 of 15", "Primary Account: XXXX 1858"])
        assert reading.account.account_last4 == "1858"

    def test_a_spaced_ocr_mask_still_reads(self):
        assert read_header(["COMMERCIAL CHECKING Account Xxx X 1858"]).account.account_last4 \
            == "1858"


class TestLetterheadIsWordsNotLines:
    """SPEC §7.15 — a logo is set larger than the address printed beside it."""

    def test_only_the_large_words_of_the_line_are_the_bank(self):
        rows = [
            mixed(60.0, [(50.0, "Fulton", 18.4), (140.0, "Bank", 18.4),
                         (205.0, "Lancaster,", 10.4), (250.0, "PA", 10.4),
                         (270.0, "17604", 10.4)]),
            line(72.0, (50.0, "fultonbank.com")),
        ]
        assert read_header(rows).account.bank == "Fulton Bank"

    def test_a_uniformly_set_letterhead_is_kept_whole(self):
        rows = [big(60.0, 50.0, "GREAT LAKES COMMERCE BANK"),
                line(72.0, (50.0, "Business Checking Statement"))]
        assert read_header(rows).account.bank == "GREAT LAKES COMMERCE BANK"


class TestAccountMaskNeedsTwoCharactersOrNoGap:
    """SPEC §7.15 — OCR of `P.O.Box 4887` yields `P.O.B 0 x 4887` on some pages."""

    def test_a_lone_masked_character_with_a_gap_is_not_a_mask(self):
        reading = read_header(["P.O.B 0 x 4887 Page 3 of 15", "Primary Account: XXXX 1858"])
        assert reading.account.account_last4 == "1858"

    def test_a_single_mask_touching_the_digits_still_reads(self):
        assert read_header(["Checking x4071"]).account.account_last4 == "4071"


class TestStatementCyclePair:
    """SPEC §7.15 — core-banking statements state the cycle as two lines."""

    def test_last_and_this_statement_bracket_the_period(self):
        period = read_header([
            "DECEMBER 31, 2024: LAST STATEMENT",
            "JANUARY 31, 2025: THIS STATEMENT",
        ]).account.period
        assert period.start == date(2024, 12, 31)
        assert period.end == date(2025, 1, 31)

    def test_this_statement_alone_fills_only_the_end(self):
        period = read_header(["JANUARY 31, 2025: THIS STATEMENT"]).account.period
        assert period.start is None
        assert period.end == date(2025, 1, 31)

    def test_an_explicit_period_line_still_wins(self):
        period = read_header([
            "Statement period 04/01/2025 - 04/30/2025",
            "JANUARY 31, 2025: THIS STATEMENT",
        ]).account.period
        assert (period.start, period.end) == (date(2025, 4, 1), date(2025, 4, 30))


class TestAccountNumberWithoutAMask:
    """SPEC §7.15 — `ACCOUNT NUMBER 0011016426` means the account ends 6426."""

    def test_last_four_of_a_long_run(self):
        assert read_header(["ACCOUNTNUMBER 0011016426"]).account.account_last4 == "6426"

    def test_a_masked_token_still_wins(self):
        reading = read_header(["ACCOUNT NUMBER 0011016426", "Checking ****4071"])
        assert reading.account.account_last4 == "4071"


class TestCommaLessMonthName:
    """OCR drops the comma: `JANUARY 31 2025` is still a date."""

    def test_month_day_year_without_a_comma(self):
        period = read_header(["JANUARY 31 2025: THIS STATEMENT"]).account.period
        assert period.end == date(2025, 1, 31)

    def test_a_run_together_cycle_label_still_matches(self):
        period = read_header(["DECEMBER 31 2024 LASTSTATEMENT"]).account.period
        assert period.start == date(2024, 12, 31)


class TestPeriodFromBalanceLines:
    """SPEC §7.15 — the header may state only a statement date; the balances state the range."""

    def test_as_of_dates_fill_a_missing_start(self):
        period = read_header([
            "Statement Date 04/30/2025",
            "Beginning Balance as of 04/01/2025 $597,068.70",
            "Ending Balance as of 04/30/2025 $509,121.59",
        ]).account.period
        assert period.start == date(2025, 4, 1)
        assert period.end == date(2025, 4, 30)

    def test_balance_lines_alone_are_enough(self):
        period = read_header([
            "Beginning Balance as of 04/01/2025 $597,068.70",
            "Ending Balance as of 04/30/2025 $509,121.59",
        ]).account.period
        assert (period.start, period.end) == (date(2025, 4, 1), date(2025, 4, 30))

    def test_an_explicit_range_is_not_overridden(self):
        period = read_header([
            "Statement period 03/01/2025 - 03/31/2025",
            "Beginning Balance as of 04/01/2025 $1.00",
        ]).account.period
        assert (period.start, period.end) == (date(2025, 3, 1), date(2025, 3, 31))

    def test_a_balance_line_with_no_date_changes_nothing(self):
        period = read_header(["Beginning balance $597,068.70"]).account.period
        assert period.start is None
