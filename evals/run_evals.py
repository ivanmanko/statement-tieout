"""Run the extractor over every sample and print a measured accuracy table.

    uv run python evals/run_evals.py [--samples samples] [--report evals/reports/local.json]

This is the acceptance gate (SPEC §10.2). Two things are checked, and they are
independent:

* **Against ground truth** — `evals/expected/<stem>.json`, where a human read
  the printed summary block. Compared for *exact* equality, because that is
  how the assignment grades it.
* **Against itself** — the reconciliation. This one needs no ground truth at
  all, which is why it is the check that survives contact with the unseen
  statements the extractor will actually be graded on.

Exits non-zero when a file with an expectation mismatches, or when any file
raises. A file that fails to reconcile and says so is *reported*, not an
error — that is the honest outcome the SPEC asks for.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from statement_tieout import extract_result  # noqa: E402
from statement_tieout.money import format_money  # noqa: E402
from statement_tieout.schema import CheckState, ExtractResult  # noqa: E402

EXPECTED_DIR = Path(__file__).resolve().parent / "expected"
SUMMARY_FIELDS = (
    "beginning_balance", "ending_balance",
    "deposits_total", "deposits_count",
    "withdrawals_total", "withdrawals_count",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", default="samples", help="directory of statement PDFs")
    parser.add_argument("--report", default="evals/reports/local.json")
    args = parser.parse_args(argv)

    pdfs = sorted(Path(args.samples).glob("*.pdf"))
    if not pdfs:
        print(f"no PDFs in {args.samples}/ — see README", file=sys.stderr)
        return 2

    rows = [_evaluate(pdf) for pdf in pdfs]
    print(_table(rows))
    print()
    print(_verdict(rows))

    report = Path(args.report)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(rows, indent=2, default=str))
    print(f"\nreport: {report}")

    return 1 if any(row["status"] == "fail" for row in rows) else 0


def _evaluate(pdf: Path) -> dict:
    expected = _expected_for(pdf)
    row: dict = {"file": pdf.name, "expectation": _expectation_kind(expected)}
    try:
        result = extract_result(str(pdf))
    except Exception as error:  # a crash is always a failure of this harness
        row.update(status="fail", error=f"{type(error).__name__}: {error}",
                   traceback=traceback.format_exc())
        return row

    row.update(_observed(result))
    row["invariant_violations"] = _invariants(result)
    row["summary_diff"] = _compare(result, expected)
    row["period_diff"] = _compare_periods(result, expected)
    row["status"] = _status(row)
    return row


def _expected_for(pdf: Path) -> dict | None:
    path = EXPECTED_DIR / f"{pdf.stem}.json"
    return json.loads(path.read_text()) if path.exists() else None


def _expectation_kind(expected: dict | None) -> str:
    if expected is None:
        return "none"
    return "no ground truth" if expected.get("no_ground_truth") else "ground truth"


def _observed(result: ExtractResult) -> dict:
    return {
        "periods": len(result.periods),
        "transactions": len(result.transactions),
        "reconciled": result.reconciliation.reconciled,
        "checks": {name: str(state) for name, state in result.reconciliation.checks.items()},
        "checks_ok": sum(
            1 for state in result.reconciliation.checks.values() if state is CheckState.OK
        ),
        "checks_failed": sorted(
            name for name, state in result.reconciliation.checks.items()
            if state is CheckState.FAIL
        ),
        "residual": format_money(result.reconciliation.residual),
        "diagnosis": result.reconciliation.diagnosis,
        "detail": result.reconciliation.detail,
        "rung": result.extraction.path,
        "llm_calls": result.extraction.llm_calls,
        "cost_usd": result.extraction.cost_usd,
        "latency_s": result.extraction.latency_s,
        "warnings": result.extraction.warnings,
    }


def _invariants(result: ExtractResult) -> list[str]:
    """SPEC §3 invariants, checked on every result regardless of ground truth."""
    violations = []
    for index, txn in enumerate(result.transactions):
        sides = [s for s in (txn.deposit, txn.withdrawal) if s is not None]
        if len(sides) != 1 or sides[0] <= Decimal("0"):
            violations.append(f"transaction {index}: exactly one positive side required")

    for index, period in enumerate(result.periods):
        deposits = sum(1 for t in period.transactions if t.deposit is not None)
        withdrawals = len(period.transactions) - deposits
        if period.summary.deposits_count != deposits and "deposits_count" not in (
            period.summary.printed_fields
        ):
            violations.append(f"period {index}: derived deposits_count disagrees with the rows")
        if period.summary.withdrawals_count != withdrawals and "withdrawals_count" not in (
            period.summary.printed_fields
        ):
            violations.append(f"period {index}: derived withdrawals_count disagrees")

    if len(result.periods) == 1 and result.summary != result.periods[0].summary:
        violations.append("single period: top-level summary does not mirror the period")
    return violations


def _compare(result: ExtractResult, expected: dict | None) -> dict | None:
    """Exact equality on the summary fields a human read off the page."""
    if expected is None or expected.get("no_ground_truth"):
        return None

    diff: dict = {"matched": [], "mismatched": {}}
    observed = result.summary.model_dump(mode="json")
    for field in SUMMARY_FIELDS:
        if field not in expected.get("summary", {}):
            continue
        want, got = expected["summary"][field], observed[field]
        if want == got:
            diff["matched"].append(field)
        else:
            diff["mismatched"][field] = {"expected": want, "got": got}

    account = result.account.model_dump(mode="json")
    for field in ("bank", "account_last4"):
        if field in expected.get("account", {}):
            want, got = expected["account"][field], account[field]
            if want == got:
                diff["matched"].append(field)
            else:
                diff["mismatched"][field] = {"expected": want, "got": got}

    if "period" in expected.get("account", {}):
        want, got = expected["account"]["period"], account["period"]
        if want == got:
            diff["matched"].append("period")
        else:
            diff["mismatched"]["period"] = {"expected": want, "got": got}

    for key, observed_value in (
        ("periods", len(result.periods)),
        ("transactions_count", len(result.transactions)),
    ):
        if key in expected:
            if expected[key] == observed_value:
                diff["matched"].append(key)
            else:
                diff["mismatched"][key] = {"expected": expected[key], "got": observed_value}

    return diff


def _compare_periods(result: ExtractResult, expected: dict | None) -> dict | None:
    """Ground truth for one named period.

    A binder's top-level summary is an aggregate, so it cannot be checked
    against a single statement's printed block. Where ground truth exists for
    one period — the assignment states the first Ixonia statement in full —
    it is asserted against that period directly.
    """
    if expected is None or not expected.get("period_expectations"):
        return None

    diff: dict = {"matched": [], "mismatched": {}}
    for expectation in expected["period_expectations"]:
        index = expectation["index"]
        label = f"period[{index}]"
        if index >= len(result.periods):
            diff["mismatched"][label] = {
                "expected": "a period", "got": f"only {len(result.periods)} found"
            }
            continue
        period = result.periods[index]
        observed = {
            **period.summary.model_dump(mode="json"),
            "bank": period.account.bank,
            "account_last4": period.account.account_last4,
            "period": period.account.period.model_dump(mode="json"),
            "transactions_count": len(period.transactions),
        }
        wanted = {
            **expectation.get("summary", {}),
            **{k: v for k, v in expectation.get("account", {}).items()},
        }
        if "transactions_count" in expectation:
            wanted["transactions_count"] = expectation["transactions_count"]
        for field, want in wanted.items():
            got = observed.get(field)
            if want == got:
                diff["matched"].append(f"{label}.{field}")
            else:
                diff["mismatched"][f"{label}.{field}"] = {"expected": want, "got": got}
    return diff


def _status(row: dict) -> str:
    if row["invariant_violations"]:
        return "fail"
    for key in ("summary_diff", "period_diff"):
        diff = row.get(key)
        if diff is not None and diff["mismatched"]:
            return "fail"
    diff = row["summary_diff"]
    if diff is not None and row["reconciled"]:
        return "pass"
    return "reported"


def _table(rows: list[dict]) -> str:
    header = (
        f"{'file':<46} {'per':>3} {'rows':>5} {'recon':>6} {'ok':>2} "
        f"{'residual':>12} {'match':>7} {'calls':>5} {'cost':>7} {'sec':>5}  status"
    )
    lines = [header, "-" * len(header)]
    for row in rows:
        if "error" in row:
            lines.append(f"{row['file'][:46]:<46} {'CRASHED: ' + row['error'][:40]}")
            continue
        lines.append(
            f"{row['file'][:46]:<46} {row['periods']:>3} {row['transactions']:>5} "
            f"{('yes' if row['reconciled'] else 'NO'):>6} {row['checks_ok']:>2} "
            f"{row['residual']:>12} {_match(row):>7} {row['llm_calls']:>5} "
            f"{row['cost_usd']:>7.4f} "
            f"{row['latency_s']:>5.2f}  {row['status']}"
        )
        mismatched = {}
        for key in ("summary_diff", "period_diff"):
            if row.get(key):
                mismatched.update(row[key]["mismatched"])
        if mismatched:
            for field, delta in mismatched.items():
                lines.append(
                    f"{'':<46}   {field}: expected {delta['expected']}, got {delta['got']}"
                )
        if row.get("detail"):
            lines.append(f"{'':<46}   {row['detail']}")
        for warning in row.get("warnings", []):
            lines.append(f"{'':<46}   warning: {warning}")
    return "\n".join(lines)


def _match(row: dict) -> str:
    """How many ground-truth fields matched exactly, out of how many were stated."""
    diff = row["summary_diff"]
    if diff is None and row.get("period_diff") is None:
        return "-"
    if diff is None:
        diff = {"matched": [], "mismatched": {}}
    matched = len(diff["matched"])
    total = matched + len(diff["mismatched"])
    periods = row.get("period_diff")
    if periods is not None:
        matched += len(periods["matched"])
        total += len(periods["matched"]) + len(periods["mismatched"])
    return f"{matched}/{total}"


def _verdict(rows: list[dict]) -> str:
    failed = [r for r in rows if r["status"] == "fail"]
    reported = [r for r in rows if r["status"] == "reported"]
    passed = [r for r in rows if r["status"] == "pass"]
    parts = [f"{len(passed)} passed", f"{len(reported)} reported without ground truth"]
    if failed:
        parts.append(f"{len(failed)} FAILED")
    return " · ".join(parts)


if __name__ == "__main__":
    raise SystemExit(main())
