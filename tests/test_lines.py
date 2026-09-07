"""Grouping words into visual lines (SPEC §7.2).

A line is anchored on the mean height of the words already in it, not on
whichever word happened to come first. Measured on the Ixonia binder: a cheque
image's serial number sits in the left margin three points above the row
beside it, and anchored on that stray token the row's own words fell on
either side of the tolerance — `May` and the balance on one line, `15
CHECK#40840 331.80` on the next — so the row was two lines and neither was a
transaction.
"""

from statement_tieout.pdf.model import LINE_TOLERANCE, Page, Word

from .helpers import word


def texts(lines: list[list[Word]]) -> list[list[str]]:
    return [[w.text for w in line] for line in lines]


class TestAStrayTokenDoesNotTearARow:
    def torn_page(self) -> Page:
        """The measured case, to the tenth of a point."""
        return Page(number=1, words=[
            Word(text="000010630", x0=7.0, x1=17.0, top=162.8),
            Word(text="May", x0=53.0, x1=73.0, top=165.6),
            Word(text="392,253.52", x0=519.0, x1=566.0, top=165.6),
            Word(text="15", x0=73.0, x1=86.0, top=166.1),
            Word(text="CHECK#40840", x0=81.0, x1=154.0, top=166.4),
            Word(text="331.80", x0=458.0, x1=487.0, top=166.4),
        ])

    def test_the_row_is_one_line(self):
        (line,) = self.torn_page().lines()
        assert [w.text for w in line] == [
            "000010630", "May", "15", "CHECK#40840", "331.80", "392,253.52"
        ]

    def test_adjacent_rows_stay_apart(self):
        """Rows a pitch apart are never pulled together by the mean."""
        rows = [
            [word("May", 53.0, 155.9), word("15", 73.0, 155.9), word("1,869.76", 450.0, 155.9)],
            [word("May", 53.0, 165.6), word("15", 73.0, 166.1), word("331.80", 458.0, 166.4)],
            [word("May", 53.0, 176.0), word("15", 73.0, 176.5), word("626.41", 458.0, 176.8)],
        ]
        page = Page(number=1, words=[w for row in rows for w in row])
        assert texts(page.lines()) == texts(rows)

    def test_a_word_beyond_the_tolerance_of_the_mean_starts_a_new_line(self):
        page = Page(number=1, words=[
            word("A", 10.0, 100.0),
            word("B", 40.0, 100.0),
            word("C", 70.0, 100.0 + LINE_TOLERANCE + 0.5),
        ])
        assert texts(page.lines()) == [["A", "B"], ["C"]]

    def test_words_come_back_left_to_right_within_a_line(self):
        page = Page(number=1, words=[word("B", 40.0, 100.5), word("A", 10.0, 101.0)])
        assert texts(page.lines()) == [["A", "B"]]
