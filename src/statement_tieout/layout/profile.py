"""The layout profile (SPEC §7.4).

A profile is **data**, never code: column positions, a date format, and which
of the four side strategies applies. That is deliberately the only thing that
differs between one bank's statement and another's — it is what a model is
asked to produce on rung 2, and what makes an unseen template a data problem
rather than a code change.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class SideStrategy(StrEnum):
    """How a row becomes a deposit or a withdrawal (SPEC §7.6), in priority order."""

    TWO_COLUMNS = "two_columns"
    SIGNED = "signed"
    SECTIONS = "sections"
    BALANCE_DELTA = "balance_delta"


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

    @property
    def description_x1(self) -> float:
        """Descriptions end where the leftmost money column begins."""
        return min(column.x0 for column in self.amount_columns)
