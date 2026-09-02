"""Command line entry point: `statement-tieout <pdf>` (SPEC §1)."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .api import extract_result
from .money import format_money
from .pdf.loader import ExtractionError
from .schema import CheckState, ExtractResult


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="statement-tieout",
        description="Extract a bank statement and report whether it reconciles.",
    )
    parser.add_argument("pdf", help="path to the statement PDF")
    parser.add_argument("-o", "--out", help="write JSON here instead of stdout")
    parser.add_argument("--summary", action="store_true",
                        help="print the reconciliation report instead of the JSON")
    parser.add_argument("--log", action="store_true", help="emit the structured log line")
    args = parser.parse_args(argv)

    if args.log:
        logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)

    try:
        result = extract_result(args.pdf)
    except ExtractionError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if args.summary:
        print(_report(Path(args.pdf).name, result))
    else:
        payload = json.dumps(result.model_dump(mode="json"), indent=2)
        if args.out:
            Path(args.out).write_text(payload)
            print(f"wrote {args.out}", file=sys.stderr)
        else:
            print(payload)

    return 0 if result.reconciliation.reconciled else 1


def _report(name: str, result: ExtractResult) -> str:
    """The human view: what reconciled, what did not, and by how much."""
    lines = [name, "=" * len(name)]
    account = result.account
    lines.append(
        f"{account.bank or '(bank unknown)'} · account {account.account_last4 or '????'} · "
        f"{account.period.start or '?'} to {account.period.end or '?'}"
    )
    lines.append(f"{len(result.periods)} period(s), {len(result.transactions)} transaction(s)")
    lines.append("")

    for index, period in enumerate(result.periods, start=1):
        state = "reconciled" if period.reconciliation.reconciled else "DID NOT RECONCILE"
        lines.append(f"period {index}: {state}")
        for check, value in period.reconciliation.checks.items():
            mark = {CheckState.OK: "ok", CheckState.FAIL: "FAIL",
                    CheckState.UNAVAILABLE: "--"}[value]
            lines.append(f"    {mark:>4}  {check}")
        if not period.reconciliation.reconciled:
            lines.append(f"    residual {format_money(period.reconciliation.residual)}")
            if period.reconciliation.detail:
                lines.append(f"    {period.reconciliation.detail}")
        lines.append("")

    extraction = result.extraction
    lines.append(
        f"rung: {extraction.path} · {extraction.llm_calls} LLM call(s) · "
        f"${extraction.cost_usd:.4f} · {extraction.latency_s}s"
    )
    lines.extend(f"warning: {w}" for w in extraction.warnings)
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
