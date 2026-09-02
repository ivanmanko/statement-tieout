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
from pathlib import Path

from .layout.dates import starts_with_date
from .layout.heuristic import derive_profile
from .money import find_money, format_money
from .parse.header import HeaderReading, build_summary, read_header
from .parse.rows import ParsedRows, parse_rows
from .parse.segment import segment
from .pdf.loader import is_scanned, load_pages
from .pdf.model import Page, Word
from .reconcile import diagnose, reconcile
from .schema import Extraction, ExtractResult, PeriodResult, Reconciliation

logger = logging.getLogger("statement_tieout")

__all__ = ["extract", "extract_result"]


def extract(pdf_path: str) -> dict:
    """Extract one bank statement into the SPEC §3 structure."""
    return extract_result(pdf_path).model_dump(mode="json")


def extract_result(pdf_path: str) -> ExtractResult:
    """As `extract`, but typed — used by the CLI and the eval harness."""
    started = time.monotonic()
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

    periods = []
    for group in segment(pages):
        period, period_warnings = _period(group)
        periods.append(period)
        warnings.extend(period_warnings)

    extraction = Extraction(
        path="deterministic",
        latency_s=round(time.monotonic() - started, 3),
        warnings=warnings,
    )
    result = ExtractResult.from_periods(periods, extraction)
    _log(pdf_path, pages, scanned, result)
    return result


def _period(pages: list[Page]) -> tuple[PeriodResult, list[str]]:
    """Parse and reconcile one statement period."""
    lines = [line for page in pages for line in page.lines()]
    header_lines, body_lines = _split_at_first_row(lines)
    reading = read_header(header_lines, body_lines)

    profile = derive_profile(pages)
    if profile is None:
        parsed = ParsedRows(warnings=["no layout profile could be derived from these pages"])
    else:
        parsed = parse_rows(
            pages,
            profile,
            period=reading.account.period,
            opening_balance=reading.beginning_balance,
        )

    summary = build_summary(reading, parsed.transactions)
    result = reconcile(summary, parsed.transactions)
    result = _explain(result, reading, parsed, pages)

    return (
        PeriodResult(
            account=reading.account,
            summary=summary,
            transactions=parsed.transactions,
            reconciliation=result,
        ),
        parsed.warnings,
    )


def _explain(
    result: Reconciliation, reading: HeaderReading, parsed: ParsedRows, pages: list[Page]
) -> Reconciliation:
    """Attach a residual diagnosis to a period that did not reconcile (SPEC §5.1)."""
    if result.reconciled:
        return result

    summary = build_summary(reading, parsed.transactions)
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
