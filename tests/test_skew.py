"""Grouping words into lines on a page that was scanned at an angle.

SPEC §7.2. A sheet fed a degree or two off square puts the last column of a
row a whole line below the first, and every rule downstream — the column
alignment, the row parser, the summary block — is stated in terms of lines.
The page is read along its own angle instead of the sheet's.
"""

from dataclasses import replace

from statement_tieout.pdf.model import LINE_TOLERANCE, Page, Word, deskew, estimate_skew

from .helpers import line

SLOPE = 0.021
"""About 1.2 degrees. A sheet fed off square by this much is ordinary; the
generated corpus carries one (`gen_05`) for the same reason."""

PAGE_WIDTH = 612.0
"""US Letter. The angle only has to be right to within less than one line
across the widest page there is, which is what these tests assert."""


def within_a_line(found: float, actual: float) -> bool:
    return abs(found - actual) * PAGE_WIDTH < LINE_TOLERANCE


def straight_rows(count: int = 12) -> list[Word]:
    """A plain four-column table, one row per line, set square on the page."""
    words: list[Word] = []
    for index in range(count):
        words.extend(
            line(
                100.0 + index * 12.0,
                (30.0, f"01/{index + 1:02d}"),
                (90.0, "ACH CREDIT VENDOR PAYMENT"),
                (330.0, "1,809.28"),
                (490.0, "67,182.89"),
            )
        )
    return words


def tilt(words: list[Word], slope: float) -> list[Word]:
    """The same words as a scanner set a degree off square would place them.

    A rotation moves a word in *both* directions: down the page in proportion
    to how far right it sits, and across the page in proportion to how far
    down. The second is the one that breaks columns rather than lines.
    """
    return [
        replace(
            word,
            x0=word.x0 - word.top * slope,
            x1=word.x1 - word.top * slope,
            top=word.top + word.x0 * slope,
        )
        for word in words
    ]


class TestEstimatingTheAngle:
    def test_a_square_page_has_no_angle(self):
        assert estimate_skew(straight_rows()) == 0.0

    def test_the_angle_of_a_tilted_page_is_recovered(self):
        assert within_a_line(estimate_skew(tilt(straight_rows(), SLOPE)), SLOPE)

    def test_a_tilt_the_other_way_too(self):
        assert within_a_line(estimate_skew(tilt(straight_rows(), -SLOPE)), -SLOPE)

    def test_too_few_words_to_tell_is_no_angle(self):
        assert estimate_skew(straight_rows(count=1)) == 0.0

    def test_no_words_at_all(self):
        assert estimate_skew([]) == 0.0


class TestPuttingThePageBackSquare:
    def square(self, slope: float) -> Page:
        return Page(number=1, words=deskew(tilt(straight_rows(), slope)), source="ocr")

    def test_a_tilted_row_becomes_one_line_again(self):
        lines = self.square(SLOPE).lines()
        assert len(lines) == 12
        assert all(len(group) == 7 for group in lines)

    def test_the_row_keeps_its_reading_order(self):
        first = [word.text for word in self.square(SLOPE).lines()[0]]
        assert first == ["01/01", "ACH", "CREDIT", "VENDOR", "PAYMENT", "1,809.28", "67,182.89"]

    def test_the_columns_come_back_too(self):
        """The rotation drifts a column across the page as it goes down it, and
        a column that drifts is not a column — which is what the profile looks
        for before it will parse anything (SPEC §7.21.3)."""
        words = self.square(SLOPE).words
        amounts = [word for word in words if word.text == "1,809.28"]
        assert len(amounts) == 12
        assert max(word.x1 for word in amounts) - min(word.x1 for word in amounts) < 1.0

    def test_a_square_scan_is_left_alone(self):
        words = straight_rows()
        assert [word.x0 for word in deskew(words)] == [word.x0 for word in words]
