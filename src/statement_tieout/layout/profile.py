"""The layout profile (SPEC §7.4).

A profile is **data**, never code: column positions, a date format, and which
of the four side strategies applies. That is deliberately the only thing that
differs between one bank's statement and another's — it is what a model is
asked to produce on rung 2, and what makes an unseen template a data problem
rather than a code change.
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class SideStrategy(StrEnum):
    """How a row becomes a deposit or a withdrawal (SPEC §7.6), in priority order."""

    TWO_COLUMNS = "two_columns"
    SIGNED = "signed"
    SECTIONS = "sections"
    BALANCE_DELTA = "balance_delta"


#: Human date notation a model may answer with, longest token first so `YYYY`
#: is not consumed by `YY` (SPEC §7.4).
_HUMAN_DATE_TOKENS = (
    ("YYYY", "%Y"), ("MMMM", "%B"), ("MMM", "%b"), ("MON", "%b"),
    ("DD", "%d"), ("MM", "%m"), ("YY", "%y"),
)
_DATE_TOKEN = re.compile("|".join(token for token, _ in _HUMAN_DATE_TOKENS))


def normalize_date_format(fmt: str) -> str | None:
    """`MM/DD/YYYY` -> `%m/%d/%Y`. None when it cannot be read as a format.

    A model asked for a date format answers in the notation people use. A
    profile that validates but parses no dates is worse than a rejected one,
    because nothing downstream notices until the totals disagree.
    """
    if "%" in fmt:
        return fmt
    mapping = dict(_HUMAN_DATE_TOKENS)
    translated, cursor, saw_token = [], 0, False
    for match in _DATE_TOKEN.finditer(fmt.upper()):
        if match.start() > cursor and any(c.isalnum() for c in fmt[cursor : match.start()]):
            return None  # letters we cannot account for: not a format
        translated.append(fmt[cursor : match.start()])
        translated.append(mapping[match.group(0)])
        cursor = match.end()
        saw_token = True
    if not saw_token or any(c.isalnum() for c in fmt[cursor:]):
        return None
    translated.append(fmt[cursor:])
    return "".join(translated)


class Column(BaseModel):
    """A horizontal band. A word belongs to it when its midpoint falls inside."""

    x0: float
    x1: float

    def holds(self, center: float) -> bool:
        return self.x0 <= center <= self.x1


class LayoutProfile(BaseModel):
    """Everything the deterministic parser needs in order to read a statement."""

    date_column: Column
    amount_columns: list[Column] = Field(min_length=1, max_length=2)
    balance_column: Column | None = None
    side_strategy: SideStrategy
    date_formats: list[str] = Field(default_factory=lambda: ["%m/%d/%Y"])
    deposit_sections: list[str] = Field(default_factory=list)
    withdrawal_sections: list[str] = Field(default_factory=list)

    @field_validator("date_formats")
    @classmethod
    def _as_strptime(cls, formats: list[str]) -> list[str]:
        """Accept human notation from a model, refuse a profile with no usable format."""
        usable = [f for f in (normalize_date_format(x) for x in formats) if f]
        if not usable:
            raise ValueError("no usable date format: expected strptime directives")
        return usable

    @property
    def description_x1(self) -> float:
        """Descriptions end where the leftmost money column begins."""
        return min(column.x0 for column in self.amount_columns)
