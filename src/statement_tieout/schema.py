"""The output contract (SPEC §3).

`ExtractResult` is the single source of truth for the extractor's output. The
CLI, the eval harness and the LLM structured-output schema all derive from it;
nothing else may define or duplicate these fields.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field, field_serializer, model_validator

from .money import ZERO, money_to_json

__all__ = [
    "Account",
    "CheckState",
    "DateRange",
    "ExtractResult",
    "Extraction",
    "PeriodResult",
    "Reconciliation",
    "Summary",
    "Transaction",
]


class CheckState(StrEnum):
    """SPEC §5: a check whose input the statement did not print is never `ok`."""

    OK = "ok"
    FAIL = "fail"
    UNAVAILABLE = "unavailable"


#: Worst-state ordering used when several periods are aggregated (SPEC §3.3).
_SEVERITY = {CheckState.OK: 0, CheckState.UNAVAILABLE: 1, CheckState.FAIL: 2}


class DateRange(BaseModel):
    start: date | None = None
    end: date | None = None


class Account(BaseModel):
    bank: str | None = None
    account_last4: str | None = None
    period: DateRange = Field(default_factory=DateRange)


class Summary(BaseModel):
    """The period totals. Read from the printed block where there is one (ADR-001)."""

    beginning_balance: Decimal
    ending_balance: Decimal
    deposits_total: Decimal
    deposits_count: int
    withdrawals_total: Decimal
    withdrawals_count: int

    #: Which fields came off the page rather than from the parsed rows (SPEC §7.8).
    printed_fields: set[str] = Field(default_factory=set, exclude=True)

    @field_serializer("beginning_balance", "ending_balance", "deposits_total", "withdrawals_total")
    def _money(self, value: Decimal) -> float:
        return money_to_json(value)


class Transaction(BaseModel):
    date: date
    description: str
    deposit: Decimal | None = None
    withdrawal: Decimal | None = None

    @model_validator(mode="after")
    def _exactly_one_positive_side(self) -> Transaction:
        """SPEC §3 invariant 1: sign lives in the field choice, never in the value."""
        sides = [s for s in (self.deposit, self.withdrawal) if s is not None]
        if len(sides) != 1:
            raise ValueError("exactly one of deposit / withdrawal must be set")
        if sides[0] <= ZERO:
            raise ValueError("the amount must be positive; the side carries the sign")
        return self

    @property
    def signed(self) -> Decimal:
        """The amount as it moves the balance: positive in, negative out."""
        return self.deposit if self.deposit is not None else -self.withdrawal  # type: ignore[operator]

    @field_serializer("deposit", "withdrawal")
    def _money(self, value: Decimal | None) -> float | None:
        return None if value is None else money_to_json(value)


class Reconciliation(BaseModel):
    reconciled: bool
    checks: dict[str, CheckState]
    residual: Decimal
    diagnosis: str | None = None
    detail: str | None = None

    @model_validator(mode="after")
    def _reconciled_needs_evidence(self) -> Reconciliation:
        """SPEC §5: `reconciled` can never mean 'we had nothing to check'."""
        if self.reconciled and not self.carried_by:
            raise ValueError("reconciled=True requires at least one check in state ok")
        return self

    @property
    def carried_by(self) -> set[str]:
        """The checks that actually passed, as opposed to being unavailable."""
        return {name for name, state in self.checks.items() if state is CheckState.OK}

    @classmethod
    def reconciled_on(cls, checks: set[str]) -> Reconciliation:
        return cls(
            reconciled=True,
            checks=dict.fromkeys(sorted(checks), CheckState.OK),
            residual=ZERO,
        )

    @field_serializer("residual")
    def _money(self, value: Decimal) -> float:
        return money_to_json(value)


class PeriodResult(BaseModel):
    account: Account
    summary: Summary
    transactions: list[Transaction]
    reconciliation: Reconciliation


class Extraction(BaseModel):
    """How the answer was produced, for the cost story and the log line (SPEC §10)."""

    path: str = "deterministic"
    llm_calls: int = 0
    cost_usd: float = 0.0
    latency_s: float = 0.0
    warnings: list[str] = Field(default_factory=list)


class ExtractResult(BaseModel):
    account: Account
    summary: Summary
    transactions: list[Transaction]
    periods: list[PeriodResult]
    reconciliation: Reconciliation
    extraction: Extraction

    @classmethod
    def from_periods(cls, periods: list[PeriodResult], extraction: Extraction) -> ExtractResult:
        """Assemble the result, mirroring one period or aggregating several (SPEC §3)."""
        if not periods:
            raise ValueError("a result needs at least one period")

        first, last = periods[0], periods[-1]
        if len(periods) == 1:
            account, summary = first.account, first.summary
            transactions, reconciliation = first.transactions, first.reconciliation
        else:
            account = Account(
                bank=first.account.bank,
                account_last4=first.account.account_last4,
                period=DateRange(start=first.account.period.start, end=last.account.period.end),
            )
            summary = Summary(
                beginning_balance=first.summary.beginning_balance,
                ending_balance=last.summary.ending_balance,
                deposits_total=sum((p.summary.deposits_total for p in periods), ZERO),
                deposits_count=sum(p.summary.deposits_count for p in periods),
                withdrawals_total=sum((p.summary.withdrawals_total for p in periods), ZERO),
                withdrawals_count=sum(p.summary.withdrawals_count for p in periods),
            )
            transactions = [t for p in periods for t in p.transactions]
            reconciliation = _aggregate_reconciliation(periods)

        return cls(
            account=account,
            summary=summary,
            transactions=transactions,
            periods=periods,
            reconciliation=reconciliation,
            extraction=extraction,
        )


def _aggregate_reconciliation(periods: list[PeriodResult]) -> Reconciliation:
    """Worst state per check, summed residual, no single diagnosis (SPEC §3.3)."""
    checks: dict[str, CheckState] = {}
    for period in periods:
        for name, state in period.reconciliation.checks.items():
            current = checks.get(name)
            if current is None or _SEVERITY[state] > _SEVERITY[current]:
                checks[name] = state
    return Reconciliation(
        reconciled=all(p.reconciliation.reconciled for p in periods),
        checks=dict(sorted(checks.items())),
        residual=sum((p.reconciliation.residual for p in periods), ZERO),
    )
