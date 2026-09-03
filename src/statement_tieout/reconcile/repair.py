"""The surface rung 4 works on (SPEC §4 stage 9, §7.18).

An agentic loop is worth running only where the verifier is free, automatic
and deterministic — and here it is. Every edit below re-runs the six checks
and answers with the new verdict, so the model is told after each move whether
it helped rather than being asked to judge its own work.

Nothing here talks to a model, a file or a network. The loop that does is in
`agent.py`; this is the state it manipulates, which is why it can be tested
exactly.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from ..money import find_money, format_money, parse_money
from ..pdf.model import Page
from ..schema import CheckState, Summary, Transaction
from .diagnose import diagnose
from .engine import reconcile

DEPOSIT, WITHDRAWAL = "deposit", "withdrawal"


class RepairLedger:
    """Parsed rows plus the pages they came from, editable and always verified."""

    def __init__(self, summary: Summary, transactions: list[Transaction], pages: list[Page]):
        self._summary = summary
        self._original = list(transactions)
        self._rows = list(transactions)
        self._pages = pages
        self.edits = 0

    # ------------------------------------------------------------------ state

    @property
    def reconciled(self) -> bool:
        return reconcile(self._summary, self._rows).reconciled

    def state(self) -> str:
        """The six checks and the residual, as the model sees them."""
        result = reconcile(self._summary, self._rows)
        if result.reconciled:
            return f"reconciled — residual {format_money(result.residual)}, {len(self._rows)} rows"
        checks = ", ".join(
            f"{name}={state}" for name, state in result.checks.items()
            if state is not CheckState.UNAVAILABLE
        )
        finding = diagnose(self._summary, self._rows)
        return (
            f"NOT reconciled — residual {format_money(result.residual)}, "
            f"{len(self._rows)} rows. checks: {checks}. "
            f"{finding.detail if finding else ''}"
        )

    def result(self) -> list[Transaction]:
        """The repaired rows if the period closed, otherwise the originals."""
        return self._rows if self.reconciled else self._original

    # ------------------------------------------------------------- inspection

    def read_page(self, number: int) -> str:
        page = next((p for p in self._pages if p.number == number), None)
        if page is None:
            return f"there is no page {number} in this period"
        return page.text

    def find_amount(self, amount: str) -> str:
        """Where this money appears on the page, and whether a row already has it."""
        try:
            value = abs(parse_money(amount))
        except ValueError:
            return f"{amount!r} is not an amount"

        carried = [
            index for index, row in enumerate(self._rows)
            if (row.deposit or row.withdrawal) == value
        ]
        found = [p.number for p in self._pages if _text_holds(p.text, value)]
        parts = []
        parts.append(
            f"page {', '.join(str(n) for n in found)}" if found
            else f"no page shows {format_money(value)}"
        )
        parts.append(
            f"already carried by row(s) {carried}" if carried else "no parsed row carries it"
        )
        return "; ".join(parts)

    def list_rows(self, start: int = 0, end: int | None = None) -> str:
        window = self._rows[start : end if end is not None else start + 20]
        if not window:
            return "no rows in that window"
        return "\n".join(
            f"[{start + offset}] {row.date.isoformat()} {row.description[:44]!r} "
            f"{DEPOSIT if row.deposit else WITHDRAWAL} "
            f"{format_money(row.deposit or row.withdrawal)}"
            for offset, row in enumerate(window)
        )

    # ---------------------------------------------------------------- editing

    def insert_row(self, when: str, description: str, side: str, amount: str) -> str:
        parsed_date = _as_date(when)
        if parsed_date is None:
            return f"{when!r} is not a date in YYYY-MM-DD form"
        if side not in (DEPOSIT, WITHDRAWAL):
            return f"side must be {DEPOSIT!r} or {WITHDRAWAL!r}, not {side!r}"
        try:
            value = abs(parse_money(amount))
        except (ValueError, InvalidOperation):
            return f"{amount!r} is not an amount"
        if value == Decimal("0.00"):
            return "an amount of zero moves no money"

        self._rows.append(
            Transaction(
                date=parsed_date,
                description=description,
                deposit=value if side == DEPOSIT else None,
                withdrawal=value if side == WITHDRAWAL else None,
            )
        )
        self.edits += 1
        return self.state()

    def drop_row(self, index: int) -> str:
        if not 0 <= index < len(self._rows):
            return f"there is no row {index}; the period has {len(self._rows)}"
        self._rows.pop(index)
        self.edits += 1
        return self.state()

    def set_side(self, index: int, side: str) -> str:
        if not 0 <= index < len(self._rows):
            return f"there is no row {index}; the period has {len(self._rows)}"
        if side not in (DEPOSIT, WITHDRAWAL):
            return f"side must be {DEPOSIT!r} or {WITHDRAWAL!r}, not {side!r}"
        row = self._rows[index]
        value = row.deposit or row.withdrawal
        self._rows[index] = row.model_copy(
            update={
                "deposit": value if side == DEPOSIT else None,
                "withdrawal": value if side == WITHDRAWAL else None,
            }
        )
        self.edits += 1
        return self.state()


def _text_holds(text: str, value: Decimal) -> bool:
    return any(abs(token.value) == value for token in find_money(text))


def _as_date(text: str) -> date | None:
    try:
        return datetime.strptime(text.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None
