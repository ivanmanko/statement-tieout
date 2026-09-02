"""Heuristic layout profiling — rung 0 of the ladder (SPEC §7.17).

This is the rung that costs nothing and the one generalization rests on: it
derives where the columns are from coordinates alone, and **declines** rather
than guessing when it cannot. Declining is what makes escalation meaningful.
"""

from statement_tieout.layout import SideStrategy
from statement_tieout.layout.heuristic import derive_profile

from .helpers import DATE_X, DESC_X, LEFT_X, RIGHT_X, line, page, rows_page

SIGNED_PAGE = rows_page(
    ("01/01/2025", "CIGNA CLAIMS PAYMENT", "8,164.30", "2,023,046.77"),
    ("01/01/2025", "MEDPRO INSURANCE PREMIUM", "-17,459.90", "2,005,586.87"),
    ("01/02/2025", "DEPOSIT BRANCH", "15,856.31", "2,021,443.18"),
    ("01/02/2025", "RENT NSI PROPERTIES", "-6,327.03", "2,015,116.15"),
)


class TestSignedLayout:
    def test_detects_the_signed_strategy(self):
        assert derive_profile([SIGNED_PAGE]).side_strategy is SideStrategy.SIGNED

    def test_finds_one_amount_column_and_one_balance_column(self):
        profile = derive_profile([SIGNED_PAGE])
        assert len(profile.amount_columns) == 1
        assert profile.balance_column is not None
        assert profile.amount_columns[0].x1 < profile.balance_column.x0

    def test_date_column_covers_the_date_tokens(self):
        profile = derive_profile([SIGNED_PAGE])
        assert profile.date_column.x0 <= DATE_X
        assert profile.date_column.x1 >= DATE_X + len("01/01/2025") * 6.0

    def test_picks_the_date_format_that_parses_the_rows(self):
        assert derive_profile([SIGNED_PAGE]).date_formats[0] == "%m/%d/%Y"

    def test_the_derived_profile_parses_its_own_page(self):
        """The point of a profile: it must round-trip the page it came from."""
        from statement_tieout.parse.rows import parse_rows

        profile = derive_profile([SIGNED_PAGE])
        parsed = parse_rows([SIGNED_PAGE], profile)
        assert len(parsed.transactions) == 4
        assert [t.deposit is not None for t in parsed.transactions] == [
            True, False, True, False
        ]


class TestBalanceColumnIsMeasuredNotAssumed:
    def test_a_rightmost_column_that_is_not_a_running_balance_stays_an_amount(self):
        """SPEC §7.17.4 — the chain has to actually hold."""
        noisy = rows_page(
            ("01/01/2025", "A", "-10.00", "77.00"),
            ("01/02/2025", "B", "-20.00", "12.00"),
            ("01/03/2025", "C", "-30.00", "95.00"),
        )
        profile = derive_profile([noisy])
        assert profile.balance_column is None
        assert len(profile.amount_columns) == 2


class TestTwoColumnLayout:
    def test_two_unsigned_money_columns_become_two_columns(self):
        p = page(
            line(100.0, (DATE_X, "01/01/2025"), (DESC_X, "DEPOSIT"), (LEFT_X, "15,856.31")),
            line(112.0, (DATE_X, "01/02/2025"), (DESC_X, "RENT"), (RIGHT_X, "6,327.03")),
            line(124.0, (DATE_X, "01/03/2025"), (DESC_X, "LOCKBOX"), (LEFT_X, "8,193.03")),
            line(136.0, (DATE_X, "01/04/2025"), (DESC_X, "CHECK"), (RIGHT_X, "9,635.89")),
        )
        profile = derive_profile([p])
        assert profile.side_strategy is SideStrategy.TWO_COLUMNS
        assert len(profile.amount_columns) == 2


class TestSectionLayout:
    def test_headings_are_recognised_and_classified(self):
        p = page(
            line(90.0, (DESC_X, "Deposits and Additions")),
            line(100.0, (DATE_X, "01/01/2025"), (DESC_X, "LOCKBOX"), (LEFT_X, "8,193.03")),
            line(112.0, (DATE_X, "01/02/2025"), (DESC_X, "WIRE IN"), (LEFT_X, "1,200.00")),
            line(124.0, (DESC_X, "Total Withdrawals")),
            line(136.0, (DATE_X, "01/03/2025"), (DESC_X, "CHECK 1041"), (LEFT_X, "9,635.89")),
            line(148.0, (DATE_X, "01/04/2025"), (DESC_X, "CHECK 1042"), (LEFT_X, "635.89")),
        )
        profile = derive_profile([p])
        assert profile.side_strategy is SideStrategy.SECTIONS
        assert "Deposits and Additions" in profile.deposit_sections
        assert "Total Withdrawals" in profile.withdrawal_sections


class TestBalanceDeltaLayout:
    def test_unsigned_amounts_with_a_running_balance_use_the_delta(self):
        p = rows_page(
            ("01/01/2025", "IN", "10.00", "1,010.00"),
            ("01/02/2025", "OUT", "4.00", "1,006.00"),
            ("01/03/2025", "IN", "6.00", "1,012.00"),
            ("01/04/2025", "OUT", "2.00", "1,010.00"),
        )
        profile = derive_profile([p])
        assert profile.side_strategy is SideStrategy.BALANCE_DELTA
        assert profile.balance_column is not None


class TestDeclining:
    def test_no_candidate_rows_yields_no_profile(self):
        p = page(
            line(100.0, (DESC_X, "GREAT LAKES COMMERCE BANK")),
            line(112.0, (DESC_X, "Beginning balance"), (LEFT_X, "2,014,882.47")),
        )
        assert derive_profile([p]) is None

    def test_too_few_candidate_rows_yields_no_profile(self):
        p = rows_page(
            ("01/01/2025", "A", "-10.00", "990.00"),
            ("01/02/2025", "B", "-20.00", "970.00"),
        )
        assert derive_profile([p]) is None

    def test_unsigned_single_column_without_balance_or_sections_declines(self):
        """Nothing on the page says which side these rows are on."""
        p = rows_page(
            ("01/01/2025", "A", "10.00", None),
            ("01/02/2025", "B", "20.00", None),
            ("01/03/2025", "C", "30.00", None),
            ("01/04/2025", "D", "40.00", None),
        )
        assert derive_profile([p]) is None


class TestNoise:
    def test_money_inside_descriptions_does_not_become_a_column(self):
        p = rows_page(
            ("01/01/2025", "INVOICE 12.34 PAID", "-10.00", "990.00"),
            ("01/02/2025", "PLAIN", "-20.00", "970.00"),
            ("01/03/2025", "PLAIN", "-30.00", "940.00"),
            ("01/04/2025", "PLAIN", "-40.00", "900.00"),
        )
        profile = derive_profile([p])
        assert len(profile.amount_columns) == 1
        assert profile.amount_columns[0].x0 > DESC_X


class TestYearlessDates:
    def test_yearless_rows_are_still_candidates(self):
        p = rows_page(
            ("01/01", "A", "-10.00", "990.00"),
            ("01/02", "B", "-20.00", "970.00"),
            ("01/03", "C", "-30.00", "940.00"),
            ("01/04", "D", "-40.00", "900.00"),
        )
        profile = derive_profile([p])
        assert profile is not None
        assert profile.date_formats[0] == "%m/%d"
