"""Money parsing (SPEC §7.9).

Everything here is exact-equality on Decimal. If any of these start returning
float, the reconciliation tolerance of zero becomes unachievable.
"""

from decimal import Decimal

import pytest

from statement_tieout.money import find_money, format_money, money_to_json, parse_money


class TestParseMoneyPositive:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("1,234.56", "1234.56"),
            ("$1,234.56", "1234.56"),
            ("1234.56", "1234.56"),
            ("12,345,678.90", "12345678.90"),
            ("0.00", "0.00"),
            ("1,234.56 CR", "1234.56"),
            ("1,234.56CR", "1234.56"),
            ("  $1,234.56  ", "1234.56"),
        ],
    )
    def test_positive_forms(self, text, expected):
        assert parse_money(text) == Decimal(expected)


class TestParseMoneyNegative:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("-1,234.56", "-1234.56"),
            ("(1,234.56)", "-1234.56"),
            ("1,234.56-", "-1234.56"),
            ("1,234.56 DR", "-1234.56"),
            ("1,234.56DR", "-1234.56"),
            ("($1,234.56)", "-1234.56"),
            ("-$1,234.56", "-1234.56"),
        ],
    )
    def test_negative_forms(self, text, expected):
        assert parse_money(text) == Decimal(expected)


class TestParseMoneyRejects:
    @pytest.mark.parametrize(
        "text",
        [
            "",
            "   ",
            "abc",
            "1234",          # SPEC §7.9: exactly two decimal places required
            "1,234.5",
            "1,234.567",
            "01/28/2025",    # a date is not money
            "2025",
            "12.34.56",
            "1,234.56 XX",   # unknown marker
        ],
    )
    def test_rejected(self, text):
        with pytest.raises(ValueError):
            parse_money(text)


class TestExactness:
    def test_returns_decimal_not_float(self):
        assert isinstance(parse_money("0.10"), Decimal)

    def test_sum_of_tenths_is_exact(self):
        """The float failure mode this module exists to prevent."""
        total = sum((parse_money("0.10") for _ in range(10)), Decimal("0"))
        assert total == Decimal("1.00")

    def test_no_precision_loss_on_large_totals(self):
        rows = [parse_money("8,164.30"), parse_money("-17,459.90"), parse_money("7,900.83")]
        assert sum(rows, Decimal("0")) == Decimal("-1394.77")


class TestFindMoney:
    def test_finds_amount_and_balance_in_a_row(self):
        tokens = find_money("01/01/2025 MEDPRO INSURANCE PREMIUM -17,459.90 2,013,487.70")
        assert [t.value for t in tokens] == [Decimal("-17459.90"), Decimal("2013487.70")]

    def test_reports_spans(self):
        line = "Beginning balance $2,014,882.47"
        (token,) = find_money(line)
        assert line[token.start : token.end] == "$2,014,882.47"
        assert token.value == Decimal("2014882.47")

    def test_ignores_dates_and_account_numbers(self):
        line = "Statement period: January 01, 2025 - January 28, 2025 Account ****4071"
        assert find_money(line) == []

    def test_last_token_is_the_summary_amount(self):
        """SPEC §7.5 takes the last money-shaped token on a matching line."""
        tokens = find_money("Deposits and credits 12 items $2,180,764.90")
        assert tokens[-1].value == Decimal("2180764.90")

    def test_no_money_returns_empty(self):
        assert find_money("Date Description Amount Balance") == []


class TestSerialization:
    def test_format_money_always_two_places(self):
        assert format_money(Decimal("1234.5")) == "1234.50"
        assert format_money(Decimal("-0.1")) == "-0.10"
        assert format_money(Decimal("0")) == "0.00"

    def test_money_to_json_is_float_at_the_boundary_only(self):
        value = money_to_json(Decimal("2014882.47"))
        assert isinstance(value, float)
        assert value == 2014882.47
