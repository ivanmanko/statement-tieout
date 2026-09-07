"""Putting scanned pages back on one margin (SPEC §7.2).

A sheet fed through a scanner lands a few points to the left or right of the
sheet before it, and every rule downstream is stated in terms of columns. A
layout profile derived from one page then misses the date column on the next
by exactly that offset, and the whole page reads as no rows at all. The
offset is measured per page from the rows' own left edge and taken out at
ingest, like the tilt — so nothing downstream has to know.
"""

from dataclasses import replace

from statement_tieout.pdf.model import Page, Word
from statement_tieout.pdf.register import (
    MAX_REGISTRATION,
    MIN_ROWS_FOR_REGISTRATION,
    register,
    row_margin,
)

from .helpers import BALANCE_X, DATE_X, DESC_X, LEFT_X, line


def rows(count: int, *, shift: float = 0.0) -> list[Word]:
    """A run of transaction rows, the whole block moved sideways by `shift`."""
    words: list[Word] = []
    for index in range(count):
        words.extend(
            line(
                100.0 + index * 12.0,
                (DATE_X + shift, f"01/{index + 1:02d}/2025"),
                (DESC_X + shift, "ACH CREDIT VENDOR PAYMENT"),
                (LEFT_X + shift, "1,809.28"),
                (BALANCE_X + shift, "67,182.89"),
            )
        )
    return words


def scanned(number: int, words: list[Word]) -> Page:
    return Page(number=number, words=words, text=" ".join(w.text for w in words), source="ocr")


class TestTheRowMargin:
    def test_is_where_the_rows_begin(self):
        assert row_margin(scanned(1, rows(6))) == DATE_X

    def test_a_shifted_page_reports_its_own_margin(self):
        assert row_margin(scanned(1, rows(6, shift=14.0))) == DATE_X + 14.0

    def test_too_few_rows_is_no_margin(self):
        assert row_margin(scanned(1, rows(MIN_ROWS_FOR_REGISTRATION - 1))) is None

    def test_a_page_with_no_rows_has_no_margin(self):
        header = line(40.0, (DESC_X, "ACME BANK STATEMENT"))
        assert row_margin(scanned(1, header)) is None


class TestRegistration:
    def test_the_odd_page_out_is_moved_back_onto_the_others(self):
        pages = [scanned(1, rows(6)), scanned(2, rows(6, shift=14.0)), scanned(3, rows(6))]
        first, second, third = register(pages)
        assert row_margin(second) == row_margin(first) == row_margin(third) == DATE_X
        assert [w.x0 for w in second.words] == [w.x0 for w in first.words]
        assert [w.x1 for w in second.words] == [w.x1 for w in first.words]

    def test_pages_already_on_the_margin_are_untouched(self):
        pages = [scanned(1, rows(6)), scanned(2, rows(6, shift=14.0)), scanned(3, rows(6))]
        first, _, third = register(pages)
        assert first is pages[0]
        assert third is pages[2]

    def test_a_shift_the_other_way_too(self):
        pages = [scanned(1, rows(6)), scanned(2, rows(6, shift=-11.0)), scanned(3, rows(6))]
        assert row_margin(register(pages)[1]) == DATE_X

    def test_a_text_layer_page_is_never_moved(self):
        """Exact coordinates need no registration, whatever the scans around them do."""
        digital = replace(scanned(2, rows(6, shift=14.0)), source="text")
        pages = [scanned(1, rows(6)), digital, scanned(3, rows(6))]
        assert register(pages)[1] is digital

    def test_a_page_with_too_few_rows_is_left_where_it_is(self):
        sparse = scanned(2, rows(MIN_ROWS_FOR_REGISTRATION - 1, shift=14.0))
        pages = [scanned(1, rows(6)), sparse, scanned(3, rows(6))]
        assert register(pages)[1] is sparse

    def test_a_shift_too_large_to_be_registration_is_not_applied(self):
        """Past the limit the page is laid out differently, not fed crooked."""
        far = scanned(2, rows(6, shift=MAX_REGISTRATION + 20.0))
        pages = [scanned(1, rows(6)), far, scanned(3, rows(6))]
        assert register(pages)[1] is far

    def test_vertical_positions_and_text_are_unchanged(self):
        pages = [scanned(1, rows(6)), scanned(2, rows(6, shift=14.0)), scanned(3, rows(6))]
        before = pages[1]
        after = register(pages)[1]
        assert [w.top for w in after.words] == [w.top for w in before.words]
        assert [w.text for w in after.words] == [w.text for w in before.words]
        assert after.text == before.text
        assert after.number == 2 and after.source == "ocr"

    def test_a_single_page_has_nothing_to_register_against(self):
        only = scanned(1, rows(6, shift=14.0))
        assert register([only])[0] is only
