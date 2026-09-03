"""`extract(pdf_path) -> dict` — the deliverable (SPEC §4).

The pipeline is an escalation ladder (ADR-002). This module owns rung 0: read
the pages, cut them into periods, derive a layout profile from coordinates,
parse, and reconcile. Rungs 2–4 hang off the same seam — a period that does
not reconcile is where a model would earn its cost — and until they are wired
in, a failure is *reported*, never hidden.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from .layout.dates import starts_with_date
from .layout.heuristic import derive_profile
from .layout.llm import profile_from_pages
from .layout.profile import LayoutProfile
from .llm.client import LLMClient, Usage, build_client
from .money import find_money, format_money
from .parse.header import HeaderReading, build_summary, read_header
from .parse.rows import ParsedRows, parse_rows
from .parse.segment import segment
from .pdf.loader import is_scanned, load_pages
from .pdf.model import Page, Word
from .reconcile import diagnose, reconcile
from .reconcile.agent import MAX_REPAIR_COST_USD, repair
from .reconcile.repair import RepairLedger
from .schema import Extraction, ExtractResult, PeriodResult, Reconciliation, Summary

logger = logging.getLogger("statement_tieout")

__all__ = ["extract", "extract_period", "extract_result"]


def extract(pdf_path: str) -> dict:
    """Extract one bank statement into the SPEC §3 structure."""
    return extract_result(pdf_path).model_dump(mode="json")


def extract_result(pdf_path: str, *, client: LLMClient | None = None) -> ExtractResult:
    """As `extract`, but typed — used by the CLI and the eval harness.

    `client` is injected by tests; in normal use it comes from the
    environment and is None when nothing is configured, which is the
    supported case rather than an error.
    """
    started = time.monotonic()
    client = client if client is not None else build_client()
    pages = load_pages(pdf_path)

    warnings: list[str] = []
    scanned = [page.number for page in pages if is_scanned(page)]
    unread = [page.number for page in pages if page.source == "empty"]
    if scanned:
        warnings.append(
            f"{len(scanned)} page(s) carry no text layer and were read by OCR "
            f"(pages {scanned[:10]}); descriptions from a scan are less faithful "
            "than descriptions from a text layer"
        )
    if unread:
        warnings.append(
            f"{len(unread)} page(s) yielded nothing at all, not even by OCR (pages {unread[:10]})"
        )

    periods, spend = [], Usage()
    for group in segment(pages):
        # SPEC §7.18: the repair budget is for the whole file, not per period.
        period, period_warnings, period_usage = extract_period(
            group, client, MAX_REPAIR_COST_USD - spend.cost_usd
        )
        periods.append(period)
        warnings.extend(period_warnings)
        spend.calls += period_usage.calls
        spend.prompt_tokens += period_usage.prompt_tokens
        spend.completion_tokens += period_usage.completion_tokens
        spend.cost_usd += period_usage.cost_usd

    extraction = Extraction(
        path=_path_taken(spend, warnings),
        llm_calls=spend.calls,
        cost_usd=round(spend.cost_usd, 6),
        latency_s=round(time.monotonic() - started, 3),
        warnings=warnings,
    )
    result = ExtractResult.from_periods(periods, extraction)
    _log(pdf_path, pages, scanned, result)
    return result


def _path_taken(spend: Usage, warnings: list[str]) -> str:
    if any("repaired by rung 4" in warning for warning in warnings):
        return "agentic_repair"
    return "llm_profile" if spend.calls else "deterministic"


def extract_period(
    pages: list[Page],
    client: LLMClient | None = None,
    repair_budget_usd: float = MAX_REPAIR_COST_USD,
) -> tuple[PeriodResult, list[str], Usage]:
    """Parse and reconcile one statement period, climbing the ladder if needed.

    The escalation rule in one place: the model is asked only after the free
    verifier has refused the free answer, and its answer is kept only if it
    reconciles. A profile that makes things worse is discarded, so a model
    can never turn an honest failure into a confident wrong result.
    """
    lines = [line for page in pages for line in page.lines()]
    header_lines, body_lines = _split_at_first_row(lines)
    reading = read_header(header_lines, body_lines)

    profile = derive_profile(pages)
    attempt = _attempt(pages, profile, reading)
    usage = Usage()

    if not attempt.reconciliation.reconciled and client is not None:
        attempt, usage = _escalate(pages, reading, attempt, client)
    if not attempt.reconciliation.reconciled and client is not None:
        attempt, repair_usage = _repair(
            pages, attempt, client, repair_budget_usd - usage.cost_usd
        )
        usage = _combined(usage, repair_usage)

    warnings = list(attempt.warnings)
    if profile is None and not usage.calls:
        warnings.append("no layout profile could be derived from these pages")

    return (
        PeriodResult(
            account=reading.account,
            summary=attempt.summary,
            transactions=attempt.parsed.transactions,
            reconciliation=attempt.reconciliation,
        ),
        warnings,
        usage,
    )


@dataclass
class _Attempt:
    """One profile's worth of parsed rows, and what the verifier made of them."""

    parsed: ParsedRows
    summary: Summary
    reconciliation: Reconciliation

    @property
    def warnings(self) -> list[str]:
        return self.parsed.warnings


def _attempt(
    pages: list[Page], profile: LayoutProfile | None, reading: HeaderReading
) -> _Attempt:
    if profile is None:
        parsed = ParsedRows()
    else:
        parsed = parse_rows(
            pages,
            profile,
            period=reading.account.period,
            opening_balance=reading.beginning_balance,
        )
    summary = build_summary(reading, parsed.transactions)
    result = _explain(reconcile(summary, parsed.transactions), summary, reading, parsed, pages)
    return _Attempt(parsed=parsed, summary=summary, reconciliation=result)


def _escalate(
    pages: list[Page], reading: HeaderReading, attempt: _Attempt, client: LLMClient
) -> tuple[_Attempt, Usage]:
    """Rung 2: ask the model for a layout, and keep it only if it reconciles."""
    feedback = attempt.reconciliation.detail or (
        f"the rows did not reconcile; residual {attempt.reconciliation.residual}"
    )
    profile, usage = profile_from_pages(pages, client, feedback=feedback)
    if profile is None:
        return attempt, usage

    candidate = _attempt(pages, profile, reading)
    return (candidate if candidate.reconciliation.reconciled else attempt), usage


def _repair(
    pages: list[Page], attempt: _Attempt, client: LLMClient, budget_usd: float
) -> tuple[_Attempt, Usage]:
    """Rung 4: let the model edit the rows, and keep them only if the period closes."""
    if budget_usd <= 0:
        return attempt, Usage()

    ledger = RepairLedger(attempt.summary, attempt.parsed.transactions, pages)
    usage = repair(ledger, client, max_cost_usd=budget_usd)
    if not ledger.reconciled:
        return attempt, usage

    repaired = ParsedRows(
        transactions=ledger.result(),
        balances=[],
        warnings=[*attempt.parsed.warnings, f"{ledger.edits} row(s) repaired by rung 4"],
    )
    return (
        _Attempt(
            parsed=repaired,
            summary=attempt.summary,
            reconciliation=reconcile(attempt.summary, repaired.transactions),
        ),
        usage,
    )


def _combined(first: Usage, second: Usage) -> Usage:
    return Usage(
        calls=first.calls + second.calls,
        prompt_tokens=first.prompt_tokens + second.prompt_tokens,
        completion_tokens=first.completion_tokens + second.completion_tokens,
        cost_usd=first.cost_usd + second.cost_usd,
    )


def _explain(
    result: Reconciliation,
    summary: Summary,
    reading: HeaderReading,
    parsed: ParsedRows,
    pages: list[Page],
) -> Reconciliation:
    """Attach a residual diagnosis to a period that did not reconcile (SPEC §5.1)."""
    if result.reconciled:
        return result

    aligned = len(parsed.balances) == len(parsed.transactions)
    finding = diagnose(
        summary,
        parsed.transactions,
        running_balances=parsed.balances if aligned else None,
        opening_balance=reading.beginning_balance,
        page_text={page.number: page.text for page in pages},
    )
    if finding is None:
        return result
    return result.model_copy(update={"diagnosis": finding.kind, "detail": finding.detail})


def _split_at_first_row(lines: list[list[Word]]) -> tuple[list[list[Word]], list[list[Word]]]:
    """SPEC §7.5: the summary block is searched above the first transaction row."""
    for index, line in enumerate(lines):
        text = " ".join(word.text for word in line)
        if starts_with_date(text) and find_money(text):
            return lines[:index], lines[index:]
    return lines, []


def _log(pdf_path: str, pages: list[Page], scanned: list[int], result: ExtractResult) -> None:
    """One structured line per extraction (SPEC §8)."""
    logger.info(
        json.dumps(
            {
                "pdf": Path(pdf_path).name,
                "pages": len(pages),
                "scanned_pages": len(scanned),
                "periods": [
                    {
                        "rows": len(period.transactions),
                        "checks": {k: str(v) for k, v in period.reconciliation.checks.items()},
                        "residual": format_money(period.reconciliation.residual),
                        "diagnosis": period.reconciliation.diagnosis,
                    }
                    for period in result.periods
                ],
                "rung": result.extraction.path,
                "llm_calls": result.extraction.llm_calls,
                "cost_usd": result.extraction.cost_usd,
                "latency_s": result.extraction.latency_s,
                "warnings": result.extraction.warnings,
            },
            default=str,
        )
    )
