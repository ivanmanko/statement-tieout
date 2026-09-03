"""Transaction row parsing under a given layout profile (SPEC §7.6, §7.11–7.12).

Every fixture is synthetic words-with-coordinates. No PDF is opened here: the
profile is the seam, and this module's job is to be correct for *any* profile,
not for any particular bank.
"""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from statement_tieout.layout import Column, LayoutProfile, SideStrategy
from statement_tieout.parse.rows import parse_rows
from statement_tieout.schema import DateRange

from .helpers import BALANCE_X, DATE_X, DESC_X, LEFT_X, RIGHT_X, line, page, word

BALANCE_COLUMN = Column(x0=480.0, x1=560.0)


def profile(
    strategy: SideStrategy = SideStrategy.SIGNED,
    amount_columns: tuple[Column, ...] = (Column(x0=320.0, x1=400.0),),
    balance_column: Column | None = BALANCE_COLUMN,
    deposit_sections: tuple[str, ...] = (),
    withdrawal_sections: tuple[str, ...] = (),
) -> LayoutProfile:
    return LayoutProfile(
        date_column=Column(x0=20.0, x1=85.0),
        amount_columns=list(amount_columns),
        balance_column=balance_column,
        side_strategy=strategy,
        date_formats=["%m/%d/%Y"],
        deposit_sections=list(deposit_sections),
        withdrawal_sections=list(withdrawal_sections),
    )


class TestSignedStrategy:
    def test_negative_amount_is_a_withdrawal(self):
        p = page(line(100.0, (DATE_X, "01/01/2025"), (DESC_X, "ACH MEDTRONIC"),
                      (LEFT_X, "-17,459.90"), (BALANCE_X, "1,992,427.24")))
        (txn,) = parse_rows([p], profile()).transactions
        assert txn.withdrawal == Decimal("17459.90")
        assert txn.deposit is None

    def test_positive_amount_is_a_deposit(self):
        p = page(line(100.0, (DATE_X, "01/01/2025"), (DESC_X, "CIGNA CLAIMS PAYMENT"),
                      (LEFT_X, "8,164.30"), (BALANCE_X, "2,023,046.77")))
        (txn,) = parse_rows([p], profile()).transactions
        assert txn.deposit == Decimal("8164.30")

    def test_description_is_the_text_between_date_and_amount(self):
        p = page(line(100.0, (DATE_X, "01/01/2025"), (DESC_X, "EFT - UNITEDHEALTHCARE PAYMENT"),
                      (LEFT_X, "7,900.83"), (BALANCE_X, "2,030,947.60")))
        (txn,) = parse_rows([p], profile()).transactions
        assert txn.description == "EFT - UNITEDHEALTHCARE PAYMENT"

    def test_date_is_parsed_with_the_declared_format(self):
        p = page(line(100.0, (DATE_X, "01/28/2025"), (DESC_X, "CHECK PAID"),
                      (LEFT_X, "-20,170.35"), (BALANCE_X, "2,193,439.63")))
        (txn,) = parse_rows([p], profile()).transactions
        assert txn.date == date(2025, 1, 28)

    def test_running_balances_are_collected_alongside(self):
        p = page(
            line(100.0, (DATE_X, "01/01/2025"), (DESC_X, "A"), (LEFT_X, "10.00"),
                 (BALANCE_X, "1,010.00")),
            line(112.0, (DATE_X, "01/02/2025"), (DESC_X, "B"), (LEFT_X, "-4.00"),
                 (BALANCE_X, "1,006.00")),
        )
        parsed = parse_rows([p], profile())
        assert parsed.balances == [Decimal("1010.00"), Decimal("1006.00")]


class TestTwoColumnStrategy:
    def test_left_column_deposits_right_column_withdrawals(self):
        prof = profile(
            SideStrategy.TWO_COLUMNS,
            amount_columns=(Column(x0=320.0, x1=395.0), Column(x0=400.0, x1=470.0)),
        )
        p = page(
            line(100.0, (DATE_X, "01/01/2025"), (DESC_X, "DEPOSIT BRANCH"), (LEFT_X, "15,856.31")),
            line(112.0, (DATE_X, "01/02/2025"), (DESC_X, "RENT LLC"), (RIGHT_X, "6,327.03")),
        )
        first, second = parse_rows([p], prof).transactions
        assert first.deposit == Decimal("15856.31")
        assert second.withdrawal == Decimal("6327.03")


class TestSectionStrategy:
    def test_heading_decides_the_side(self):
        prof = profile(
            SideStrategy.SECTIONS,
            deposit_sections=("Deposits and Additions",),
            withdrawal_sections=("Checks Paid", "Other Withdrawals"),
            balance_column=None,
        )
        p = page(
            line(90.0, (DESC_X, "Deposits and Additions")),
            line(100.0, (DATE_X, "01/01/2025"), (DESC_X, "LOCKBOX"), (LEFT_X, "8,193.03")),
            line(120.0, (DESC_X, "Checks Paid")),
            line(130.0, (DATE_X, "01/03/2025"), (DESC_X, "CHECK 1041"), (LEFT_X, "9,635.89")),
        )
        first, second = parse_rows([p], prof).transactions
        assert first.deposit == Decimal("8193.03")
        assert second.withdrawal == Decimal("9635.89")

    def test_rows_before_any_heading_are_warned_about_not_guessed(self):
        prof = profile(
            SideStrategy.SECTIONS,
            deposit_sections=("Deposits and Additions",),
            balance_column=None,
        )
        p = page(line(100.0, (DATE_X, "01/01/2025"), (DESC_X, "ORPHAN"), (LEFT_X, "10.00")))
        parsed = parse_rows([p], prof)
        assert parsed.transactions == []
        assert any("section" in w for w in parsed.warnings)


class TestBalanceDeltaStrategy:
    def test_sign_of_the_balance_step_decides(self):
        prof = profile(SideStrategy.BALANCE_DELTA)
        p = page(
            line(100.0, (DATE_X, "01/01/2025"), (DESC_X, "IN"), (LEFT_X, "10.00"),
                 (BALANCE_X, "1,010.00")),
            line(112.0, (DATE_X, "01/02/2025"), (DESC_X, "OUT"), (LEFT_X, "4.00"),
                 (BALANCE_X, "1,006.00")),
        )
        first, second = parse_rows([p], prof, opening_balance=Decimal("1000.00")).transactions
        assert first.deposit == Decimal("10.00")
        assert second.withdrawal == Decimal("4.00")


class TestNonRowLines:
    def test_column_headers_are_not_transactions(self):
        p = page(
            line(80.0, (DATE_X, "Date"), (DESC_X, "Description"), (LEFT_X, "Amount"),
                 (BALANCE_X, "Balance")),
            line(100.0, (DATE_X, "01/01/2025"), (DESC_X, "A"), (LEFT_X, "10.00"),
                 (BALANCE_X, "1,010.00")),
        )
        assert len(parse_rows([p], profile()).transactions) == 1

    def test_a_repeated_header_on_page_two_is_not_swallowed_as_a_continuation(self):
        """SPEC §7.15: a continuation carries words only in the description band."""
        first = page(line(100.0, (DATE_X, "01/01/2025"), (DESC_X, "A"), (LEFT_X, "10.00"),
                          (BALANCE_X, "1,010.00")))
        second = page(
            line(60.0, (DATE_X, "Date"), (DESC_X, "Description"), (LEFT_X, "Amount"),
                 (BALANCE_X, "Balance")),
            number=2,
        )
        (txn,) = parse_rows([first, second], profile()).transactions
        assert txn.description == "A"

    def test_wrapped_description_is_appended_to_the_previous_row(self):
        p = page(
            line(100.0, (DATE_X, "01/01/2025"), (DESC_X, "WIRE CONTINENTAL"), (LEFT_X, "-10.00"),
                 (BALANCE_X, "990.00")),
            line(110.0, (DESC_X, "SURGICAL MGMT LLC")),
        )
        (txn,) = parse_rows([p], profile()).transactions
        assert txn.description == "WIRE CONTINENTAL SURGICAL MGMT LLC"

    def test_money_inside_the_description_band_is_not_an_amount(self):
        p = page(line(100.0, (DATE_X, "01/01/2025"), (DESC_X, "INVOICE 12.34 PAID"),
                      (LEFT_X, "-10.00"), (BALANCE_X, "990.00")))
        (txn,) = parse_rows([p], profile()).transactions
        assert txn.withdrawal == Decimal("10.00")
        assert "12.34" in txn.description


class TestDates:
    def test_date_outside_the_period_is_kept_and_warned_about(self):
        """SPEC §7.11: dropping it would break reconciliation silently."""
        p = page(line(100.0, (DATE_X, "02/14/2025"), (DESC_X, "LATE"), (LEFT_X, "10.00"),
                      (BALANCE_X, "1,010.00")))
        parsed = parse_rows(
            [p], profile(), period=DateRange(start=date(2025, 1, 1), end=date(2025, 1, 28))
        )
        assert len(parsed.transactions) == 1
        assert any("2025-02-14" in w for w in parsed.warnings)

    @pytest.mark.parametrize(
        ("text", "fmt", "expected"),
        [("01/28/2025", "%m/%d/%Y", date(2025, 1, 28)),
         ("28/01/2025", "%d/%m/%Y", date(2025, 1, 28)),
         ("2025-01-28", "%Y-%m-%d", date(2025, 1, 28))],
    )
    def test_declared_formats_are_honored(self, text, fmt, expected):
        prof = profile()
        prof.date_formats = [fmt]
        p = page(line(100.0, (DATE_X, text), (DESC_X, "X"), (LEFT_X, "10.00"),
                      (BALANCE_X, "1,010.00")))
        (txn,) = parse_rows([p], prof).transactions
        assert txn.date == expected

    def test_multi_word_date_is_joined(self):
        """pdfplumber splits on whitespace, so `Jan 28, 2025` arrives as three words."""
        prof = profile()
        prof.date_column = Column(x0=20.0, x1=125.0)
        prof.date_formats = ["%b %d, %Y"]
        p = page(line(100.0, (DATE_X, "Jan 28, 2025"), (140.0, "X"), (LEFT_X, "10.00"),
                      (BALANCE_X, "1,010.00")))
        (txn,) = parse_rows([p], prof).transactions
        assert txn.date == date(2025, 1, 28)
        assert txn.description == "X"

    def test_yearless_format_takes_the_year_from_the_period(self):
        prof = profile()
        prof.date_formats = ["%m/%d"]
        p = page(line(100.0, (DATE_X, "01/28"), (DESC_X, "X"), (LEFT_X, "10.00"),
                      (BALANCE_X, "1,010.00")))
        parsed = parse_rows(
            [p], prof, period=DateRange(start=date(2025, 1, 1), end=date(2025, 1, 31))
        )
        assert parsed.transactions[0].date == date(2025, 1, 28)

    def test_yearless_format_rolls_over_a_year_boundary(self):
        prof = profile()
        prof.date_formats = ["%m/%d"]
        p = page(
            line(100.0, (DATE_X, "12/20"), (DESC_X, "X"), (LEFT_X, "10.00"),
                 (BALANCE_X, "1,010.00")),
            line(112.0, (DATE_X, "01/05"), (DESC_X, "Y"), (LEFT_X, "10.00"),
                 (BALANCE_X, "1,020.00")),
        )
        parsed = parse_rows(
            [p], prof, period=DateRange(start=date(2024, 12, 15), end=date(2025, 1, 14))
        )
        assert [t.date for t in parsed.transactions] == [date(2024, 12, 20), date(2025, 1, 5)]

    def test_yearless_format_without_a_period_is_warned_about(self):
        prof = profile()
        prof.date_formats = ["%m/%d"]
        p = page(line(100.0, (DATE_X, "01/28"), (DESC_X, "X"), (LEFT_X, "10.00"),
                      (BALANCE_X, "1,010.00")))
        parsed = parse_rows([p], prof)
        assert any("year" in w for w in parsed.warnings)


class TestZeroAmountRows:
    """SPEC §7.12 — a row that moves no money is not a transaction.

    Found by running the binder: one such row raised a validation error and
    killed the whole extraction, which is the wrong failure mode for a file
    of 99 pages.
    """

    def test_a_zero_amount_row_is_skipped_not_fatal(self):
        p = page(
            line(100.0, (DATE_X, "01/01/2025"), (DESC_X, "BEGINNING BALANCE"),
                 (LEFT_X, "0.00"), (BALANCE_X, "1,000.00")),
            line(112.0, (DATE_X, "01/02/2025"), (DESC_X, "REAL"), (LEFT_X, "10.00"),
                 (BALANCE_X, "1,010.00")),
        )
        parsed = parse_rows([p], profile())
        assert [t.description for t in parsed.transactions] == ["REAL"]

    def test_the_skip_is_reported(self):
        p = page(line(100.0, (DATE_X, "01/01/2025"), (DESC_X, "NIL"), (LEFT_X, "0.00"),
                      (BALANCE_X, "1,000.00")))
        parsed = parse_rows([p], profile())
        assert parsed.transactions == []
        assert any("zero" in w for w in parsed.warnings)

    def test_a_negative_zero_is_also_skipped(self):
        p = page(line(100.0, (DATE_X, "01/01/2025"), (DESC_X, "NIL"), (LEFT_X, "-0.00"),
                      (BALANCE_X, "1,000.00")))
        assert parse_rows([p], profile()).transactions == []


class TestOneBadRowDoesNotKillTheDocument:
    """SPEC §7.14 — a row the contract rejects costs that row, not the file."""

    def test_a_rejected_row_is_skipped_and_counted(self, monkeypatch):
        import statement_tieout.parse.rows as rows_module

        real = rows_module.Transaction

        def only_the_second_is_bad(**fields):
            if fields.get("description") == "BAD":
                raise ValidationError.from_exception_data("Transaction", [])
            return real(**fields)

        monkeypatch.setattr(rows_module, "Transaction", only_the_second_is_bad)
        p = page(
            line(100.0, (DATE_X, "01/01/2025"), (DESC_X, "GOOD"), (LEFT_X, "10.00"),
                 (BALANCE_X, "1,010.00")),
            line(112.0, (DATE_X, "01/02/2025"), (DESC_X, "BAD"), (LEFT_X, "20.00"),
                 (BALANCE_X, "1,030.00")),
            line(124.0, (DATE_X, "01/03/2025"), (DESC_X, "ALSO GOOD"), (LEFT_X, "30.00"),
                 (BALANCE_X, "1,060.00")),
        )
        parsed = parse_rows([p], profile())
        assert [t.description for t in parsed.transactions] == ["GOOD", "ALSO GOOD"]
        assert any("rejected" in w for w in parsed.warnings)


class TestMultiColumnSummaryLines:
    """SPEC §7.13 — a daily-balance table is not a list of transactions.

    Measured on the Ixonia binder: one such table added 2,920,908.79 of
    deposits that never happened, because each `Apr 11 521,451.70` pair after
    the first landed in the amount column.
    """

    def test_date_amount_date_is_rejected(self):
        p = page(line(100.0, (DATE_X, "01/01/2025"), (DESC_X, "607,330.75"),
                      (200.0, "01/11/2025"), (LEFT_X, "521,451.70"),
                      (BALANCE_X, "1,010.00")))
        parsed = parse_rows([p], profile())
        assert parsed.transactions == []

    def test_a_date_inside_a_description_is_kept(self):
        """No amount separates it from the row's own date."""
        p = page(line(100.0, (DATE_X, "01/01/2025"), (DESC_X, "PAYABLES 01/15/2025 INVOICE"),
                      (LEFT_X, "56,378.66"), (BALANCE_X, "1,010.00")))
        (txn,) = parse_rows([p], profile()).transactions
        assert txn.deposit == Decimal("56378.66")
        assert "01/15/2025" in txn.description

    def test_the_rejection_is_reported(self):
        p = page(line(100.0, (DATE_X, "01/01/2025"), (DESC_X, "607,330.75"),
                      (200.0, "01/11/2025"), (LEFT_X, "521,451.70")))
        assert any("summary" in w for w in parse_rows([p], profile()).warnings)

    def test_an_ordinary_row_is_untouched(self):
        p = page(line(100.0, (DATE_X, "01/01/2025"), (DESC_X, "ACH MEDTRONIC"),
                      (LEFT_X, "-17,459.90"), (BALANCE_X, "1,992,427.24")))
        assert len(parse_rows([p], profile()).transactions) == 1

    def test_a_multi_word_date_is_recognised_in_the_scan(self):
        """`Apr 11` is two words; the real binder prints its dates that way."""
        prof = profile()
        prof.date_formats = ["%b %d"]
        p = page(line(100.0, (DATE_X, "Apr 01"), (DESC_X, "607,330.75"),
                      (200.0, "Apr 11"), (LEFT_X, "521,451.70"),
                      (BALANCE_X, "1,010.00")))
        assert parse_rows([p], prof).transactions == []


class TestFragmentedTokens:
    """SPEC §7.2 — OCR splits one printed value across two boxes.

    Measured on the Renasant statement: `363.80` arrives as `363` then `80`,
    `-13,495.14` as `-13,495` then `14`, `01-21` as `0`, `1-2`, `1`. Fragments
    are joined only inside one column and only when they touch.
    """

    def touching(self, top, *cells):
        """A line where the given cells sit flush against one another."""
        words = []
        for x0, text in cells:
            words.append(word(text, x0, top))
        return words

    def test_an_amount_split_before_its_cents_is_joined(self):
        p = page(
            self.touching(100.0, (DATE_X, "01/01/2025")) +
            [word("PAYMENT", DESC_X, 100.0)] +
            [word("363", 330.0, 100.0), word("80", 348.0, 100.0)] +
            [word("1,010.00", BALANCE_X, 100.0)]
        )
        (txn,) = parse_rows([p], profile()).transactions
        assert txn.deposit == Decimal("363.80")

    def test_a_negative_amount_split_before_its_cents_is_joined(self):
        p = page(
            self.touching(100.0, (DATE_X, "01/01/2025")) +
            [word("WITHDRAWAL", DESC_X, 100.0)] +
            [word("-13,495", 330.0, 100.0), word("14", 372.0, 100.0)] +
            [word("990.00", BALANCE_X, 100.0)]
        )
        (txn,) = parse_rows([p], profile()).transactions
        assert txn.withdrawal == Decimal("13495.14")

    def test_a_date_split_across_boxes_is_joined(self):
        prof = profile()
        prof.date_formats = ["%m-%d"]
        p = page(
            [word("0", DATE_X, 100.0), word("1-2", DATE_X + 6.0, 100.0),
             word("1", DATE_X + 24.0, 100.0)] +
            [word("TRANSFER", DESC_X, 100.0)] +
            [word("373.80", 330.0, 100.0), word("1,010.00", BALANCE_X, 100.0)]
        )
        parsed = parse_rows(
            [p], prof, period=DateRange(start=date(2025, 1, 1), end=date(2025, 1, 31))
        )
        assert parsed.transactions[0].date == date(2025, 1, 21)

    def test_the_longest_join_wins(self):
        """`0` + `1-2` + `1` is 01-21, not 01-2 with a stray 1 left over."""
        prof = profile()
        prof.date_formats = ["%m-%d"]
        p = page(
            [word("0", DATE_X, 100.0), word("1-2", DATE_X + 6.0, 100.0),
             word("1", DATE_X + 24.0, 100.0)] +
            [word("X", DESC_X, 100.0), word("10.00", 330.0, 100.0),
             word("1,010.00", BALANCE_X, 100.0)]
        )
        parsed = parse_rows(
            [p], prof, period=DateRange(start=date(2025, 1, 1), end=date(2025, 1, 31))
        )
        assert parsed.transactions[0].date.day == 21

    def test_boxes_that_do_not_touch_are_not_joined(self):
        """Two separate values in one column would be a layout problem, not a fragment."""
        p = page(
            self.touching(100.0, (DATE_X, "01/01/2025")) +
            [word("PAYMENT", DESC_X, 100.0)] +
            [word("363", 330.0, 100.0), word("80", 380.0, 100.0)] +
            [word("1,010.00", BALANCE_X, 100.0)]
        )
        assert parse_rows([p], profile()).transactions == []

    def test_an_intact_amount_is_left_alone(self):
        p = page(line(100.0, (DATE_X, "01/01/2025"), (DESC_X, "PAYMENT"),
                      (LEFT_X, "-17,459.90"), (BALANCE_X, "990.00")))
        (txn,) = parse_rows([p], profile()).transactions
        assert txn.withdrawal == Decimal("17459.90")
