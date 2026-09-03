"""Turning OCR output into words with coordinates (SPEC §7.2).

The OCR engine itself is not exercised here — these tests pin the conversion,
which is pure and is where the interesting failure modes live. An engine that
returns `03/31ENDINGBALANCEFROMPRIORSTATEMENT` as one box has to become a
date in the date column and a description beside it, or nothing downstream
works.
"""

from statement_tieout.pdf.ocr import Segment, words_from_segments


def segment(text: str, x0: float, x1: float, top: float = 100.0, score: float = 0.99):
    return Segment(text=text, x0=x0, x1=x1, top=top, score=score)


class TestSplitting:
    def test_whitespace_splits(self):
        words = words_from_segments([segment("04/01 CHECK #25205", 0.0, 180.0)], scale=1.0)
        assert [w.text for w in words] == ["04/01", "CHECK", "#25205"]

    def test_digit_to_letter_boundary_splits(self):
        words = words_from_segments([segment("03/31ENDINGBALANCE", 0.0, 180.0)], scale=1.0)
        assert [w.text for w in words] == ["03/31", "ENDINGBALANCE"]

    def test_letter_to_digit_boundary_splits(self):
        words = words_from_segments([segment("AccountXXXX1858", 0.0, 150.0)], scale=1.0)
        assert words[-1].text == "1858"

    def test_lowercase_to_uppercase_boundary_splits(self):
        words = words_from_segments([segment("PriorStatementBalance", 0.0, 210.0)], scale=1.0)
        assert [w.text for w in words] == ["Prior", "Statement", "Balance"]

    def test_a_money_token_is_never_split(self):
        words = words_from_segments([segment("1,908,989.60", 0.0, 120.0)], scale=1.0)
        assert [w.text for w in words] == ["1,908,989.60"]

    def test_a_date_is_never_split(self):
        assert [w.text for w in words_from_segments([segment("04/01", 0.0, 50.0)], scale=1.0)] == [
            "04/01"
        ]


class TestGeometry:
    def test_box_is_divided_in_proportion_to_character_count(self):
        first, second = words_from_segments([segment("AB CDEF", 0.0, 120.0)], scale=1.0)
        assert first.x0 == 0.0
        assert first.x1 == 40.0
        assert second.x0 == 40.0
        assert second.x1 == 120.0

    def test_coordinates_are_divided_by_the_render_scale(self):
        (word,) = words_from_segments([segment("TOTAL", 300.0, 450.0, top=600.0)], scale=3.0)
        assert (word.x0, word.x1, word.top) == (100.0, 150.0, 200.0)

    def test_a_single_token_keeps_the_whole_box(self):
        (word,) = words_from_segments([segment("1,908,989.60", 90.0, 210.0)], scale=1.0)
        assert (word.x0, word.x1) == (90.0, 210.0)


class TestFiltering:
    def test_low_confidence_segments_are_dropped(self):
        segments = [segment("GOOD", 0.0, 40.0), segment("mush", 50.0, 90.0, score=0.2)]
        assert [w.text for w in words_from_segments(segments, scale=1.0)] == ["GOOD"]

    def test_empty_and_whitespace_segments_are_ignored(self):
        segments = [segment("", 0.0, 10.0), segment("   ", 20.0, 30.0)]
        assert words_from_segments(segments, scale=1.0) == []

    def test_words_come_back_in_reading_order(self):
        segments = [segment("SECOND", 100.0, 160.0, top=200.0), segment("FIRST", 0.0, 50.0)]
        assert [w.text for w in words_from_segments(segments, scale=1.0)] == ["FIRST", "SECOND"]


class TestSeparatorRepair:
    """SPEC §7.2 — the one OCR repair: a thousands separator misread as a slash.

    `32/537.69` is what the engine returns for `32,537.69` on a real page, at
    every render scale tried. Nothing else in a statement takes that shape.
    """

    def test_a_slash_between_digit_groups_becomes_a_comma(self):
        (word,) = words_from_segments([segment("32/537.69", 0.0, 90.0)], scale=1.0)
        assert word.text == "32,537.69"

    def test_a_pipe_is_repaired_too(self):
        (word,) = words_from_segments([segment("1|234|567.89", 0.0, 120.0)], scale=1.0)
        assert word.text == "1,234,567.89"

    def test_a_date_is_left_alone(self):
        (word,) = words_from_segments([segment("04/01/2025", 0.0, 60.0)], scale=1.0)
        assert word.text == "04/01/2025"

    def test_a_fraction_without_a_money_tail_is_left_alone(self):
        (word,) = words_from_segments([segment("1/234", 0.0, 40.0)], scale=1.0)
        assert word.text == "1/234"


class TestColonSplitting:
    """A colon is a field separator; OCR glues the label to it."""

    def test_a_colon_splits_the_segment(self):
        words = words_from_segments([segment("2024:LASTSTATEMENT", 0.0, 180.0)], scale=1.0)
        assert [w.text for w in words] == ["2024", "LASTSTATEMENT"]

    def test_a_period_between_digit_groups_becomes_a_comma(self):
        """Found on the Ixonia binder: `1,782.02` read as `1.782.02`."""
        words = words_from_segments(
            [segment("1.782.02", 0.0, 80.0), segment("63.779.55", 90.0, 180.0)], scale=1.0
        )
        assert [w.text for w in words] == ["1,782.02", "63,779.55"]

    def test_european_notation_is_left_alone(self):
        """`1.234,56` ends in a comma group, so it is not this misread."""
        (word,) = words_from_segments([segment("1.234,56", 0.0, 80.0)], scale=1.0)
        assert word.text == "1.234,56"

    def test_a_dotted_date_is_left_alone(self):
        (word,) = words_from_segments([segment("04.01.2025", 0.0, 80.0)], scale=1.0)
        assert word.text == "04.01.2025"
