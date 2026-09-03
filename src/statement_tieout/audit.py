"""The audit view (SPEC §8).

An auditor does not buy extracted rows. They perform a bank reconciliation and
must leave behind a workpaper that survives review, which imposes three
requirements ordinary extraction does not: the population must be provably
complete, every figure must be traceable to evidence, and an exception must be
attributable — to the tool or to the document.

Nothing here computes a figure of its own. It reads `ExtractResult` and
restates it in the terms the reader actually works in; a second source of truth
for any number would be the same defect as an undeclared heuristic.
"""

from __future__ import annotations

import re
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field, field_serializer

from .money import ZERO, format_money, money_to_json
from .schema import ExtractResult, PeriodResult

__all__ = ["Attribution", "AuditReport", "PeriodAudit", "Verdict", "audit"]


class Verdict(StrEnum):
    TIED = "tied"
    TIED_WITH_NOTES = "tied_with_notes"
    EXCEPTIONS_IDENTIFIED = "exceptions_identified"
    NOT_TIED = "not_tied"

    @property
    def next_step_hint(self) -> str:
        return {
            Verdict.TIED: "Reconciliation complete — carry to the cash lead schedule.",
            Verdict.TIED_WITH_NOTES: "Reconciled, but vouch the noted items before relying on it.",
            Verdict.EXCEPTIONS_IDENTIFIED: "Work the named items; each carries a page reference.",
            Verdict.NOT_TIED: "Re-perform this period by hand.",
        }[self]


#: Worst-first, for taking the weakest period as the file's verdict.
_SEVERITY = {
    Verdict.TIED: 0,
    Verdict.TIED_WITH_NOTES: 1,
    Verdict.EXCEPTIONS_IDENTIFIED: 2,
    Verdict.NOT_TIED: 3,
}


class Attribution(StrEnum):
    """Whose exception this is. `unexplained` is the honest third state."""

    NONE = "none"
    EXTRACTION_UNCERTAINTY = "extraction_uncertainty"
    STATEMENT_INCONSISTENCY = "statement_inconsistency"
    UNEXPLAINED = "unexplained"


class Completeness(BaseModel):
    """Whether this population may be sampled from at all."""

    bounded: bool
    printed_deposits: int | None = None
    printed_withdrawals: int | None = None
    parsed_deposits: int = 0
    parsed_withdrawals: int = 0
    missing_deposits: int | None = None
    missing_withdrawals: int | None = None
    statement: str = ""


class AuditException(BaseModel):
    kind: Attribution
    description: str
    action: str
    amount: Decimal | None = None
    page: int | None = None

    @field_serializer("amount")
    def _money(self, value: Decimal | None) -> float | None:
        return None if value is None else money_to_json(value)


class PeriodAudit(BaseModel):
    index: int
    bank: str | None = None
    account_last4: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    verdict: Verdict
    attribution: Attribution
    completeness: Completeness
    residual: Decimal
    extraction_uncertainty: Decimal
    statement_inconsistency: Decimal
    transactions: int
    exceptions: list[AuditException] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)

    @field_serializer("residual", "extraction_uncertainty", "statement_inconsistency")
    def _money(self, value: Decimal) -> float:
        return money_to_json(value)


class AuditReport(BaseModel):
    verdict: Verdict
    periods: list[PeriodAudit]
    transactions: int
    warnings: list[str] = Field(default_factory=list)


def audit(result: ExtractResult) -> AuditReport:
    """Restate an extraction as the workpaper an auditor actually needs."""
    warnings = list(result.extraction.warnings)
    periods = [
        _period(index, period, warnings)
        for index, period in enumerate(result.periods, start=1)
    ]
    return AuditReport(
        verdict=max((p.verdict for p in periods), key=lambda v: _SEVERITY[v]),
        periods=periods,
        transactions=len(result.transactions),
        warnings=warnings,
    )


def _period(index: int, period: PeriodResult, warnings: list[str]) -> PeriodAudit:
    completeness = _completeness(period)
    doubts = _doubt_items(period, warnings)
    residual = period.reconciliation.residual

    attribution, extraction, inconsistency = _attribute(period, doubts, residual)
    exceptions = list(doubts)
    if residual != ZERO:
        exceptions.insert(0, _residual_exception(period, attribution, residual))

    verdict = _verdict(period, completeness, doubts, attribution)
    return PeriodAudit(
        index=index,
        bank=period.account.bank,
        account_last4=period.account.account_last4,
        period_start=_iso(period.account.period.start),
        period_end=_iso(period.account.period.end),
        verdict=verdict,
        attribution=attribution,
        completeness=completeness,
        residual=residual,
        extraction_uncertainty=extraction,
        statement_inconsistency=inconsistency,
        transactions=len(period.transactions),
        exceptions=exceptions,
        next_steps=_next_steps(verdict, completeness, doubts, attribution, period),
    )


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _completeness(period: PeriodResult) -> Completeness:
    """SPEC §8: nothing can be sampled from a list of unknown length."""
    summary = period.summary
    printed = summary.printed_fields
    deposits = sum(1 for t in period.transactions if t.deposit is not None)
    withdrawals = len(period.transactions) - deposits
    bounded = {"deposits_count", "withdrawals_count"} <= printed

    if not bounded:
        return Completeness(
            bounded=False,
            parsed_deposits=deposits,
            parsed_withdrawals=withdrawals,
            statement=(
                "The statement prints no transaction counts, so completeness of the "
                "population cannot be established from this document alone."
            ),
        )

    short_deposits = summary.deposits_count - deposits
    short_withdrawals = summary.withdrawals_count - withdrawals
    if short_deposits == 0 and short_withdrawals == 0:
        statement = (
            f"The statement prints {summary.deposits_count} deposits and "
            f"{summary.withdrawals_count} withdrawals; both were found. "
            "The population is complete."
        )
    else:
        statement = (
            f"The statement prints {summary.deposits_count} deposits and "
            f"{summary.withdrawals_count} withdrawals; {deposits} and {withdrawals} were "
            f"found. The population is short by {short_deposits} deposit(s) and "
            f"{short_withdrawals} withdrawal(s)."
        )
    return Completeness(
        bounded=True,
        printed_deposits=summary.deposits_count,
        printed_withdrawals=summary.withdrawals_count,
        parsed_deposits=deposits,
        parsed_withdrawals=withdrawals,
        missing_deposits=short_deposits,
        missing_withdrawals=short_withdrawals,
        statement=statement,
    )


#: Warnings that mean the tool is unsure of something, and what to do about it.
_DOUBT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("read as a multi-column summary table",
     "Confirm on the page that these lines are not transactions."),
    ("carried a zero amount",
     "Confirm these lines carry no amount on the page."),
    ("rejected by the output contract",
     "Inspect these lines on the page; they were dropped."),
    ("appeared before any known section heading",
     "Determine the side of these rows on the page."),
    ("carry no text layer",
     "The pages were read by OCR; descriptions are less faithful than amounts."),
    ("no layout profile could be derived",
     "No column layout was recognised; re-perform this period by hand."),
)


def _doubt_items(period: PeriodResult, warnings: list[str]) -> list[AuditException]:
    """Everything the tool is unsure of that does not itself move the residual."""
    items: list[AuditException] = []

    inferred = [t for t in period.transactions if t.recovered]
    for transaction in inferred:
        items.append(AuditException(
            kind=Attribution.EXTRACTION_UNCERTAINTY,
            description=(
                f"Amount inferred from the running balance, not read: "
                f"{format_money(transaction.deposit or transaction.withdrawal)} on "
                f"{transaction.date.isoformat()}"
            ),
            action="Vouch this amount to the page before relying on it.",
            amount=transaction.deposit or transaction.withdrawal,
            page=transaction.page,
        ))

    for warning in warnings:
        for marker, action in _DOUBT_PATTERNS:
            if marker in warning:
                items.append(AuditException(
                    kind=Attribution.EXTRACTION_UNCERTAINTY,
                    description=warning,
                    action=action,
                ))
                break
    return items


_AMOUNT_IN_DETAIL = re.compile(r"[-(]?\$?\d[\d,]*\.\d{2}")
_PAGE_IN_DETAIL = re.compile(r"page (\d+)")

#: Diagnoses that name the tool as the cause (SPEC §5.1).
_TOOL_DIAGNOSES = {
    "dropped_row", "duplicated_row", "side_flip", "zero_amount_rows",
    "row_level_break", "amount_matches_row",
}


def _attribute(
    period: PeriodResult, doubts: list[AuditException], residual: Decimal
) -> tuple[Attribution, Decimal, Decimal]:
    """Whose exception the residual is (SPEC §8)."""
    if residual == ZERO:
        return Attribution.NONE, ZERO, ZERO

    diagnosis = period.reconciliation.diagnosis
    if diagnosis in _TOOL_DIAGNOSES:
        return Attribution.EXTRACTION_UNCERTAINTY, residual, ZERO

    # The document may be blamed only when nothing is left in doubt.
    if not doubts and diagnosis is None:
        return Attribution.STATEMENT_INCONSISTENCY, ZERO, residual

    return Attribution.UNEXPLAINED, ZERO, ZERO


def _residual_exception(
    period: PeriodResult, attribution: Attribution, residual: Decimal
) -> AuditException:
    detail = period.reconciliation.detail or "The statement does not reconcile."
    page = _PAGE_IN_DETAIL.search(detail)
    actions = {
        Attribution.EXTRACTION_UNCERTAINTY:
            "Inspect the named page: this is the tool's doubt, not an audit finding.",
        Attribution.STATEMENT_INCONSISTENCY:
            "The statement disagrees with itself. Raise it — and obtain a bank confirmation.",
        Attribution.UNEXPLAINED:
            "Re-perform this period by hand; the residual is not attributable.",
    }
    return AuditException(
        kind=attribution,
        description=f"Residual {format_money(residual)}. {detail}",
        action=actions.get(attribution, "Review."),
        amount=residual,
        page=int(page.group(1)) if page else None,
    )


def _verdict(
    period: PeriodResult,
    completeness: Completeness,
    doubts: list[AuditException],
    attribution: Attribution,
) -> Verdict:
    if period.reconciliation.reconciled:
        return Verdict.TIED if completeness.bounded and not doubts else Verdict.TIED_WITH_NOTES
    if attribution is Attribution.UNEXPLAINED:
        return Verdict.NOT_TIED
    return Verdict.EXCEPTIONS_IDENTIFIED


def _next_steps(
    verdict: Verdict,
    completeness: Completeness,
    doubts: list[AuditException],
    attribution: Attribution,
    period: PeriodResult,
) -> list[str]:
    steps = [verdict.next_step_hint]
    if not completeness.bounded:
        steps.append(
            "Do not sample from this population: the statement prints no counts, so its "
            "completeness cannot be established. Obtain a bank confirmation first."
        )
    elif completeness.missing_deposits or completeness.missing_withdrawals:
        steps.append(
            f"The population is short by "
            f"{(completeness.missing_deposits or 0) + (completeness.missing_withdrawals or 0)} "
            "row(s) against the printed counts. Recover them before sampling."
        )
    if attribution is Attribution.STATEMENT_INCONSISTENCY:
        steps.append(
            "The document's own totals do not agree with its own lines. This is a finding "
            "about the statement, not about the extraction."
        )
    if doubts:
        steps.append(
            f"{len(doubts)} item(s) below are the tool's own doubt — vouch, do not report."
        )
    return steps
