"""Splitting a file into statement periods (SPEC §7.3).

Segmentation happens *before* parsing, because "all periods are detected
correctly" is a stated requirement and a binder of concatenated statements
reconciles per period or not at all.
"""

from statement_tieout.parse.segment import segment

from .helpers import DESC_X, line, page


def statement_page(number: int, *lines: str, top: float = 100.0):
    return page(
        *[line(top + i * 12.0, (DESC_X, text)) for i, text in enumerate(lines)],
        number=number,
    )


def plain(number: int):
    return statement_page(number, "01/01/2025 SOME TRANSACTION 10.00")


class TestSinglePeriod:
    def test_no_anchor_is_one_period(self):
        pages = [plain(1), plain(2), plain(3)]
        assert segment(pages) == [pages]

    def test_an_anchor_only_on_the_first_page_is_one_period(self):
        pages = [
            statement_page(1, "IXONIA BANK", "Beginning balance 1,000.00"),
            plain(2),
            plain(3),
        ]
        assert [[p.number for p in group] for group in segment(pages)] == [[1, 2, 3]]

    def test_a_single_page_is_one_period(self):
        assert len(segment([plain(1)])) == 1


class TestSeveralPeriods:
    def test_a_second_beginning_balance_starts_a_new_period(self):
        pages = [
            statement_page(1, "Beginning balance 1,000.00"),
            plain(2),
            plain(3),
            statement_page(4, "Beginning balance 2,000.00"),
            plain(5),
        ]
        assert [[p.number for p in g] for g in segment(pages)] == [[1, 2, 3], [4, 5]]

    def test_a_changed_account_number_starts_a_new_period(self):
        pages = [
            statement_page(1, "Account ****4071"),
            plain(2),
            statement_page(3, "Account ****6426"),
            plain(4),
        ]
        assert [[p.number for p in g] for g in segment(pages)] == [[1, 2], [3, 4]]

    def test_the_same_account_number_repeated_does_not_split(self):
        pages = [
            statement_page(1, "Account ****4071"),
            statement_page(2, "Account ****4071"),
            statement_page(3, "Account ****4071"),
        ]
        assert len(segment(pages)) == 1

    def test_changed_period_dates_start_a_new_period(self):
        pages = [
            statement_page(1, "Statement period 04/01/2025 - 04/30/2025"),
            plain(2),
            statement_page(3, "Statement period 05/01/2025 - 05/31/2025"),
            plain(4),
        ]
        assert [[p.number for p in g] for g in segment(pages)] == [[1, 2], [3, 4]]


class TestBoilerplateGuard:
    def test_an_anchor_on_every_page_is_a_footer_not_a_period_marker(self):
        pages = [
            statement_page(n, f"01/0{n}/2025 TXN 10.00", "Previous balance 1,000.00")
            for n in (1, 2, 3, 4)
        ]
        assert len(segment(pages)) == 1

    def test_an_anchor_on_all_but_one_page_still_splits(self):
        pages = [
            statement_page(1, "Beginning balance 1,000.00"),
            plain(2),
            statement_page(3, "Beginning balance 2,000.00"),
            statement_page(4, "Beginning balance 3,000.00"),
        ]
        assert len(segment(pages)) == 3
