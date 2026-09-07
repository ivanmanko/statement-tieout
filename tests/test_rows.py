"""Transaction row parsing under a given layout profile (SPEC §7.6, §7.12–7.12).

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

    def test_a_heading_set_at_the_left_margin_still_opens_its_section(self):
        """SPEC §7.6 — a section heading is a heading wherever it is set.

        Statements print it at the left margin as often as indented into the
        description column, and the first one usually sits above the first
        transaction row. Read only where the descriptions are, the opening
        section never opens and every row under it loses its side.
        """
        prof = profile(
            SideStrategy.SECTIONS,
            deposit_sections=("DEPOSITS AND OTHER CREDITS",),
            withdrawal_sections=("WITHDRAWALS AND OTHER DEBITS",),
            balance_column=None,
        )
        p = page(
            line(90.0, (DATE_X, "DEPOSITS AND OTHER CREDITS")),
            line(100.0, (DATE_X, "01/01/2025"), (DESC_X, "LOCKBOX"), (LEFT_X, "8,193.03")),
            line(120.0, (DATE_X, "WITHDRAWALS AND OTHER DEBITS")),
            line(130.0, (DATE_X, "01/03/2025"), (DESC_X, "CHECK 1041"), (LEFT_X, "9,635.89")),
        )
        parsed = parse_rows([p], prof)
        assert [t.deposit for t in parsed.transactions] == [Decimal("8193.03"), None]
        assert [t.withdrawal for t in parsed.transactions] == [None, Decimal("9635.89")]
        assert not any("section" in w for w in parsed.warnings)

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
        """SPEC §7.16: a continuation carries words only in the description band."""
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
        """SPEC §7.12: dropping it would break reconciliation silently."""
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
    """SPEC §7.13 — a row that moves no money is not a transaction.

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
    """SPEC §7.15 — a row the contract rejects costs that row, not the file."""

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
    """SPEC §7.14 — a daily-balance table is not a list of transactions.

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


class TestBalanceStepRecovery:
    """SPEC §7.10 — a row that kept only its balance takes its amount from the step.

    Measured on the Ixonia binder: OCR loses a row's description and amount
    together, leaving `Apr 25    487,634.08`. The step from the previous
    balance is the amount, and the chain would reject it if it were wrong.
    """

    def rows_with_a_gap(self):
        return page(
            line(100.0, (DATE_X, "01/01/2025"), (DESC_X, "FIRST"), (LEFT_X, "100.00"),
                 (BALANCE_X, "1,100.00")),
            line(112.0, (DATE_X, "01/02/2025"), (BALANCE_X, "1,050.00")),
            line(124.0, (DATE_X, "01/03/2025"), (DESC_X, "THIRD"), (LEFT_X, "-25.00"),
                 (BALANCE_X, "1,025.00")),
        )

    def test_the_missing_amount_comes_from_the_step(self):
        parsed = parse_rows([self.rows_with_a_gap()], profile(),
                            opening_balance=Decimal("1000.00"))
        assert [t.deposit or -t.withdrawal for t in parsed.transactions] == [
            Decimal("100.00"), Decimal("-50.00"), Decimal("-25.00")
        ]

    def test_the_recovery_is_reported(self):
        parsed = parse_rows([self.rows_with_a_gap()], profile(),
                            opening_balance=Decimal("1000.00"))
        assert any("balance step" in w for w in parsed.warnings)

    def test_a_rising_step_is_a_deposit(self):
        p = page(
            line(100.0, (DATE_X, "01/01/2025"), (BALANCE_X, "1,250.00")),
        )
        (txn,) = parse_rows([p], profile(), opening_balance=Decimal("1000.00")).transactions
        assert txn.deposit == Decimal("250.00")

    def test_a_zero_step_is_not_a_transaction(self):
        """This is what excludes the BEGINNING BALANCE and ENDING BALANCE rows."""
        p = page(line(100.0, (DATE_X, "01/01/2025"), (DESC_X, "BEGINNING BALANCE"),
                      (BALANCE_X, "1,000.00")))
        assert parse_rows([p], profile(), opening_balance=Decimal("1000.00")).transactions == []

    def test_it_never_overrides_an_amount_that_is_present(self):
        p = page(line(100.0, (DATE_X, "01/01/2025"), (DESC_X, "REAL"), (LEFT_X, "10.00"),
                      (BALANCE_X, "9,999.00")))
        (txn,) = parse_rows([p], profile(), opening_balance=Decimal("1000.00")).transactions
        assert txn.deposit == Decimal("10.00")

    def test_without_a_balance_column_nothing_is_recovered(self):
        prof = profile(balance_column=None)
        p = page(line(100.0, (DATE_X, "01/01/2025"), (DESC_X, "NO AMOUNT")))
        assert parse_rows([p], prof, opening_balance=Decimal("1000.00")).transactions == []

    def test_without_a_previous_balance_nothing_is_recovered(self):
        p = page(line(100.0, (DATE_X, "01/01/2025"), (BALANCE_X, "1,250.00")))
        assert parse_rows([p], profile()).transactions == []


class TestProvenance:
    """SPEC §8 — a figure that cannot be traced to evidence cannot be relied on.

    An auditor referencing a workpaper has to get from a line in the output
    back to the place on the page it was read from.
    """

    def two_pages(self):
        first = page(
            line(100.0, (DATE_X, "01/01/2025"), (DESC_X, "A"), (LEFT_X, "10.00"),
                 (BALANCE_X, "1,010.00")),
            line(112.0, (DATE_X, "01/02/2025"), (DESC_X, "B"), (LEFT_X, "-4.00"),
                 (BALANCE_X, "1,006.00")),
        )
        second = page(
            line(100.0, (DATE_X, "01/03/2025"), (DESC_X, "C"), (LEFT_X, "6.00"),
                 (BALANCE_X, "1,012.00")),
            number=2,
        )
        return [first, second]

    def test_each_row_carries_the_page_it_came_from(self):
        parsed = parse_rows(self.two_pages(), profile())
        assert [t.page for t in parsed.transactions] == [1, 1, 2]

    def test_each_row_carries_its_line_on_that_page(self):
        parsed = parse_rows(self.two_pages(), profile())
        assert [t.line for t in parsed.transactions] == [0, 1, 0]

    def test_a_recovered_row_is_marked_as_such(self):
        """SPEC §8: the tool's own doubt must be separable from a real difference."""
        p = page(
            line(100.0, (DATE_X, "01/01/2025"), (DESC_X, "REAL"), (LEFT_X, "100.00"),
                 (BALANCE_X, "1,100.00")),
            line(112.0, (DATE_X, "01/02/2025"), (BALANCE_X, "1,050.00")),
        )
        parsed = parse_rows([p], profile(), opening_balance=Decimal("1000.00"))
        assert [t.recovered for t in parsed.transactions] == [False, True]

    def test_provenance_survives_serialization(self):
        parsed = parse_rows(self.two_pages(), profile())
        dumped = parsed.transactions[2].model_dump(mode="json")
        assert dumped["page"] == 2
        assert dumped["line"] == 0


class TestListingSetSeveralToALine:
    """SPEC §7.22 — a listing prints several transactions to a line.

    Measured on two of the four samples: a cheque section headed `NUMBER DATE
    AMOUNT NUMBER DATE AMOUNT` (Renasant) and `Date Check No. Amount` repeated
    three times (Ixonia). The header names the slots and where each one's
    date and amount sit; every line under it is read slot by slot, into a
    listing kept *apart* from the table's rows, because whether the listing
    adds to the table or duplicates it is the verifier's call (api).
    """

    HEADER = ((30.0, "NUMBER"), (100.0, "DATE"), (170.0, "AMOUNT"),
              (250.0, "NUMBER"), (320.0, "DATE"), (390.0, "AMOUNT"))
    PERIOD = DateRange(start=date(2024, 12, 31), end=date(2025, 1, 31))

    def yearless(self):
        return profile(balance_column=None).model_copy(update={"date_formats": ["%m-%d"]})

    def listing(self, *rows, heading="CHECKS", top=100.0):
        lines = [line(top, (DESC_X, heading)), line(top + 12.0, *self.HEADER)]
        for index, cells in enumerate(rows):
            lines.append(line(top + 24.0 + index * 12.0, *cells))
        return lines

    def test_two_records_are_read_from_one_line(self):
        p = page(*self.listing(
            ((30.0, "1693"), (100.0, "01-02"), (170.0, "363.80"),
             (250.0, "1694"), (320.0, "01-21"), (390.0, "386.02")),
        ))
        parsed = parse_rows([p], self.yearless(), period=self.PERIOD)
        assert parsed.transactions == []
        assert [(t.date, t.withdrawal) for t in parsed.listing] == [
            (date(2025, 1, 2), Decimal("363.80")),
            (date(2025, 1, 21), Decimal("386.02")),
        ]

    def test_a_partly_filled_last_line(self):
        p = page(*self.listing(
            ((30.0, "1693"), (100.0, "01-02"), (170.0, "363.80"),
             (250.0, "1694"), (320.0, "01-21"), (390.0, "386.02")),
            ((30.0, "1695"), (100.0, "01-30"), (170.0, "12.00")),
        ))
        parsed = parse_rows([p], self.yearless(), period=self.PERIOD)
        assert [t.withdrawal for t in parsed.listing] == [
            Decimal("363.80"), Decimal("386.02"), Decimal("12.00")
        ]

    def test_the_heading_says_which_side(self):
        p = page(*self.listing(
            ((30.0, "1693"), (100.0, "01-02"), (170.0, "363.80")), heading="CREDITS",
        ))
        (record,) = parse_rows([p], self.yearless(), period=self.PERIOD).listing
        assert record.deposit == Decimal("363.80")

    def test_a_header_naming_a_check_is_a_withdrawal_listing_without_a_heading(self):
        header = ((30.0, "Date"), (70.0, "Check No."), (150.0, "Amount"),
                  (230.0, "Date"), (270.0, "Check No."), (350.0, "Amount"))
        p = page(
            line(100.0, *header),
            line(112.0, (30.0, "04/30"), (70.0, "40782"), (150.0, "10,003.00"),
                 (230.0, "04/09"), (270.0, "40789"), (350.0, "6,148.64")),
        )
        prof = profile(balance_column=None).model_copy(update={"date_formats": ["%m/%d"]})
        records = parse_rows([p], prof, period=DateRange(start=date(2025, 4, 1),
                                                          end=date(2025, 4, 30))).listing
        assert [(t.date, t.withdrawal) for t in records] == [
            (date(2025, 4, 30), Decimal("10003.00")),
            (date(2025, 4, 9), Decimal("6148.64")),
        ]

    def test_no_heading_and_no_check_leaves_the_records_unsided(self):
        p = page(
            line(100.0, *self.HEADER),
            line(112.0, (30.0, "1693"), (100.0, "01-02"), (170.0, "363.80")),
        )
        parsed = parse_rows([p], self.yearless(), period=self.PERIOD)
        assert parsed.listing == []
        assert any("listing" in w and "side" in w for w in parsed.warnings)

    def test_a_noise_line_inside_the_listing_does_not_end_it(self):
        p = page(*self.listing(
            ((30.0, "1693"), (100.0, "01-02"), (170.0, "363.80")),
            ((10.0, "00001023"),),
            ((30.0, "1695"), (100.0, "01-30"), (170.0, "12.00")),
        ))
        assert len(parse_rows([p], self.yearless(), period=self.PERIOD).listing) == 2

    def test_a_line_that_fills_no_slot_ends_the_listing_and_is_read_as_usual(self):
        p = page(
            *self.listing(((30.0, "1693"), (100.0, "01-02"), (170.0, "363.80"))),
            line(200.0, (DATE_X, "01-07"), (DESC_X, "WITHDRAWAL"), (LEFT_X, "-13,495.14")),
        )
        parsed = parse_rows([p], self.yearless(), period=self.PERIOD)
        assert [t.withdrawal for t in parsed.listing] == [Decimal("363.80")]
        assert [t.withdrawal for t in parsed.transactions] == [Decimal("13495.14")]

    def test_a_listing_ends_with_its_page(self):
        first = page(*self.listing(((30.0, "1693"), (100.0, "01-02"), (170.0, "363.80"))))
        second = page(line(60.0, (30.0, "1695"), (100.0, "01-30"), (170.0, "12.00")), number=2)
        parsed = parse_rows([first, second], self.yearless(), period=self.PERIOD)
        assert len(parsed.listing) == 1

    def test_a_header_with_one_slot_is_not_a_listing(self):
        p = page(
            line(100.0, (DESC_X, "CHECKS")),
            line(112.0, (DATE_X, "DATE"), (LEFT_X, "AMOUNT")),
            line(124.0, (DATE_X, "01-02"), (DESC_X, "CHECK 1693"), (LEFT_X, "-363.80")),
        )
        parsed = parse_rows([p], self.yearless(), period=self.PERIOD)
        assert parsed.listing == []
        assert [t.withdrawal for t in parsed.transactions] == [Decimal("363.80")]

    def test_ocr_fragments_inside_a_slot_are_joined(self):
        """Measured: `01-02` arrives as `0`, `1-02`; `363.80` as `363`, `80`."""
        p = page(
            *self.listing()[:2],
            [word("1693", 30.0, 124.0), word("0", 100.0, 124.0), word("1-02", 106.0, 124.0),
             word("363", 170.0, 124.0), word("80", 188.0, 124.0)],
        )
        (record,) = parse_rows([p], self.yearless(), period=self.PERIOD).listing
        assert (record.date, record.withdrawal) == (date(2025, 1, 2), Decimal("363.80"))

    def test_a_lookalike_inside_a_joined_date_is_repaired(self):
        """Measured: `01-21` arrives as `0`, `1-2`, `i` — the glyph repair sees no
        digit in `i` alone, so the join is where `1` is tried for it."""
        p = page(
            *self.listing()[:2],
            [word("1694", 30.0, 124.0), word("0", 100.0, 124.0), word("1-2", 106.0, 124.0),
             word("i", 124.0, 124.0), word("386.02", 170.0, 124.0)],
        )
        (record,) = parse_rows([p], self.yearless(), period=self.PERIOD).listing
        assert record.date == date(2025, 1, 21)

    def test_records_carry_their_page_and_line(self):
        p = page(*self.listing(((30.0, "1693"), (100.0, "01-02"), (170.0, "363.80"))), number=3)
        (record,) = parse_rows([p], self.yearless(), period=self.PERIOD).listing
        assert (record.page, record.line) == (3, 2)
        assert record.recovered is False


class TestRowWithoutAReadableDate:
    """SPEC §7.23 — a row whose date OCR mangled still moves the balance.

    Measured on the Ixonia binder: `Nov 01` arrives as `No V 01` and `May 01`
    as `May O1`, neither of which is a date, so the first row of a statement
    was dropped and its chain broke at row 0. The amount is printed in a
    declared column and the balance continues the chain; only the date is
    missing, and a statement lists in date order.
    """

    def test_takes_the_previous_rows_date_when_the_chain_holds(self):
        p = page(
            line(100.0, (DATE_X, "01/01/2025"), (DESC_X, "FIRST"), (LEFT_X, "100.00"),
                 (BALANCE_X, "1,100.00")),
            line(112.0, (DATE_X, "O1/O2/2025"), (DESC_X, "REMOTE DEPOSIT"), (LEFT_X, "50.00"),
                 (BALANCE_X, "1,150.00")),
            line(124.0, (DATE_X, "01/03/2025"), (DESC_X, "THIRD"), (LEFT_X, "-25.00"),
                 (BALANCE_X, "1,125.00")),
        )
        parsed = parse_rows([p], profile(), opening_balance=Decimal("1000.00"))
        assert [t.deposit for t in parsed.transactions] == [
            Decimal("100.00"), Decimal("50.00"), None
        ]
        middle = parsed.transactions[1]
        assert middle.date == date(2025, 1, 1)
        assert middle.date_inherited is True
        assert middle.recovered is False
        assert parsed.transactions[0].date_inherited is False
        assert parsed.balances == [Decimal("1100.00"), Decimal("1150.00"), Decimal("1125.00")]
        assert any("date" in w and "1 row" in w for w in parsed.warnings)

    def test_the_first_row_of_a_period_takes_the_period_start(self):
        p = page(
            line(100.0, (DATE_X, "No V 01"), (DESC_X, "APPLIEDIND/PAYABLES"),
                 (LEFT_X, "329.41"), (BALANCE_X, "478,671.60")),
            line(112.0, (DATE_X, "11/01/2024"), (DESC_X, "NEXT"), (LEFT_X, "1,465.95"),
                 (BALANCE_X, "480,137.55")),
        )
        parsed = parse_rows(
            [p], profile(), opening_balance=Decimal("478342.19"),
            period=DateRange(start=date(2024, 11, 1), end=date(2024, 11, 29)),
        )
        first = parsed.transactions[0]
        assert (first.date, first.deposit) == (date(2024, 11, 1), Decimal("329.41"))
        assert first.date_inherited is True

    def test_with_no_date_to_inherit_the_row_is_left_out_and_counted(self):
        p = page(
            line(100.0, (DATE_X, "No V 01"), (DESC_X, "FIRST"), (LEFT_X, "329.41"),
                 (BALANCE_X, "478,671.60")),
        )
        parsed = parse_rows([p], profile(), opening_balance=Decimal("478342.19"))
        assert parsed.transactions == []
        assert any("date" in w and "left out" in w for w in parsed.warnings)

    def test_a_line_whose_balance_does_not_continue_the_chain_is_not_a_row(self):
        p = page(
            line(100.0, (DATE_X, "01/01/2025"), (DESC_X, "FIRST"), (LEFT_X, "100.00"),
                 (BALANCE_X, "1,100.00")),
            line(112.0, (DESC_X, "Total deposits"), (LEFT_X, "600.00"), (BALANCE_X, "1,420.00")),
        )
        parsed = parse_rows([p], profile(), opening_balance=Decimal("1000.00"))
        assert len(parsed.transactions) == 1

    def test_the_side_the_page_gives_must_agree_with_the_chain(self):
        p = page(
            line(100.0, (DATE_X, "01/01/2025"), (DESC_X, "FIRST"), (LEFT_X, "100.00"),
                 (BALANCE_X, "1,100.00")),
            # A negative amount says withdrawal; the balance went up by that much.
            line(112.0, (DESC_X, "ODD"), (LEFT_X, "-50.00"), (BALANCE_X, "1,150.00")),
        )
        parsed = parse_rows([p], profile(), opening_balance=Decimal("1000.00"))
        assert len(parsed.transactions) == 1

    def test_without_a_balance_column_nothing_is_inherited(self):
        p = page(
            line(100.0, (DATE_X, "01/01/2025"), (DESC_X, "FIRST"), (LEFT_X, "100.00")),
            line(112.0, (DESC_X, "SECOND"), (LEFT_X, "50.00")),
        )
        parsed = parse_rows([p], profile(balance_column=None))
        assert len(parsed.transactions) == 1
        assert parsed.transactions[0].description == "FIRST"

    def test_an_amount_is_never_inferred_and_a_date_inherited_on_the_same_row(self):
        """Two doubts on one row is a row nothing on the page evidences."""
        p = page(
            line(100.0, (DATE_X, "01/01/2025"), (DESC_X, "FIRST"), (LEFT_X, "100.00"),
                 (BALANCE_X, "1,100.00")),
            line(112.0, (DESC_X, "GHOST"), (BALANCE_X, "1,150.00")),
        )
        parsed = parse_rows([p], profile(), opening_balance=Decimal("1000.00"))
        assert len(parsed.transactions) == 1
