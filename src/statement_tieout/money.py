"""Money parsing and formatting (SPEC §7.9).

Amounts are `Decimal` from the moment they leave the page until the moment
they enter JSON. Nothing here may return `float`: reconciliation runs at a
tolerance of exactly zero, and summing a few hundred floats does not stay
exact to the cent.

A money token has **exactly two decimal places**. That single rule is what
keeps years, page numbers and account digits out of the amount scan.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

__all__ = ["MoneyToken", "ZERO", "find_money", "format_money", "money_to_json", "parse_money"]

ZERO = Decimal("0.00")
_CENTS = Decimal("0.01")

#: One money token. The leading `(?<!...)` stops a match from starting inside a
#: longer number or word, and the trailing `(?!\d)` stops `1,234.56` from being
#: pulled out of `1,234.567`.
_MONEY_RE = re.compile(
    r"""
    (?<![\w.,])
    (?P<open>\()?
    (?P<sign>-)?
    \$?
    (?P<int>\d{1,3}(?:,\d{3})*|\d+)
    \.
    (?P<frac>\d{2})
    (?P<close>\))?
    (?:\ ?(?P<marker>CR|DR))?
    (?P<trail>-)?
    (?!\d)
    """,
    re.VERBOSE,
)


@dataclass(frozen=True)
class MoneyToken:
    """A money-shaped substring, its value, and where it sat in the line."""

    text: str
    value: Decimal
    start: int
    end: int


def _value_of(match: re.Match[str]) -> Decimal | None:
    """Decimal for a regex match, or None when its markers contradict."""
    opened, closed = bool(match["open"]), bool(match["close"])
    if opened != closed:
        return None  # a lone parenthesis is punctuation, not a negative amount

    magnitude = Decimal(f"{match['int'].replace(',', '')}.{match['frac']}")
    negative = opened or bool(match["sign"]) or bool(match["trail"]) or match["marker"] == "DR"
    return -magnitude if negative else magnitude


def parse_money(text: str) -> Decimal:
    """Parse one complete money string. Raises ValueError on anything else.

    Accepts the forms declared in SPEC §7.9: plain, `$`-prefixed, negative by
    leading minus, parentheses, trailing minus, or a `DR` marker; `CR` is
    positive.
    """
    match = _MONEY_RE.fullmatch(text.strip())
    value = _value_of(match) if match else None
    if value is None:
        raise ValueError(f"not a money amount: {text!r}")
    return value


def find_money(text: str) -> list[MoneyToken]:
    """Every money token in a line, in order of appearance.

    Callers that want a summary amount take the last token (SPEC §7.5);
    callers parsing a transaction row use column coordinates instead and use
    this only to recognise which words are amounts at all.
    """
    tokens = []
    for match in _MONEY_RE.finditer(text):
        value = _value_of(match)
        if value is not None:
            tokens.append(
                MoneyToken(text=match.group(0), value=value, start=match.start(), end=match.end())
            )
    return tokens


def format_money(value: Decimal) -> str:
    """Canonical two-decimal string, for output, logs and residuals."""
    return str(value.quantize(_CENTS))


def money_to_json(value: Decimal) -> float:
    """The only place a money value is allowed to become a float."""
    return float(value.quantize(_CENTS))
