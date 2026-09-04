"""Recognising a date inside a line of statement text.

SPEC §7.19 declares the formats. This file is about the spellings a *scan*
produces: OCR sets the characters it sees and none of the spaces it does not.
"""

from datetime import date

from statement_tieout.layout.dates import find_dates, parse_date, starts_with_date


class TestDeclaredFormats:
    def test_the_common_notations(self):
        assert parse_date("04/01/2025") == date(2025, 4, 1)
        assert parse_date("2025-04-01") == date(2025, 4, 1)
        assert parse_date("February 01, 2026") == date(2026, 2, 1)
        assert parse_date("1 February 2026") == date(2026, 2, 1)

    def test_trailing_punctuation_is_tried_away(self):
        assert parse_date("DECEMBER 31, 2024:") == date(2024, 12, 31)

    def test_a_non_date_stays_none(self):
        assert parse_date("Statement Period") is None
        assert parse_date("4,521.00") is None


class TestOCRDropsTheSpaceAfterAComma:
    """A scanned header: `Statement Period February 01,2026`.

    The comma is printed and the space beside it is not resolved, so the
    period goes unread and every row of the statement falls back to a
    stand-in year — on a statement whose rows carry no year of their own.
    """

    def test_a_month_name_date_written_without_the_space(self):
        assert parse_date("February 01,2026") == date(2026, 2, 1)

    def test_a_single_digit_day_too(self):
        assert parse_date("February 1,2026") == date(2026, 2, 1)

    def test_an_abbreviated_month(self):
        assert parse_date("Feb 28,2026") == date(2026, 2, 28)

    def test_found_inside_a_longer_line(self):
        assert find_dates("Statement Period February 01,2026") == [date(2026, 2, 1)]

    def test_a_row_still_opens_with_a_date(self):
        assert starts_with_date("Feb 28,2026 ACH CREDIT 1,809.28")

    def test_a_comma_that_is_not_a_date_stays_unread(self):
        assert parse_date("1,809.28") is None
        assert find_dates("REMOTE DEPOSIT,LINK 1,809.28") == []


class TestARangeGluedByItsHyphen:
    """A scanned header: `Statement Period February 1,2026-February 28,2026`.

    The hyphen between the two ends of the range is set without spaces, so the
    year of the first date and the month of the second arrive as one word and
    neither date can be read. No declared format puts a hyphen between a digit
    and a letter, so separating them there cannot break a date apart.
    """

    def test_both_ends_of_the_range_are_read(self):
        assert find_dates("Statement Period February 1,2026-February 28,2026") == [
            date(2026, 2, 1),
            date(2026, 2, 28),
        ]

    def test_a_spaced_range_is_unaffected(self):
        assert find_dates("Statement period: January 01, 2025 - January 28, 2025") == [
            date(2025, 1, 1),
            date(2025, 1, 28),
        ]

    def test_a_hyphen_between_digits_is_left_alone(self):
        """`04-01-2025` is a declared format; splitting it would destroy it."""
        assert parse_date("04-01-2025") == date(2025, 4, 1)
        assert find_dates("Beginning balance 04-01-2025") == [date(2025, 4, 1)]

    def test_a_phone_number_is_not_a_date(self):
        assert find_dates("Customer Service 1-800-CALL-WELLS(1-800-225-5935)") == []

    def test_a_postcode_is_not_a_date(self):
        assert find_dates("P.O.Box 6995,Portland,OR 97228-6995") == []
