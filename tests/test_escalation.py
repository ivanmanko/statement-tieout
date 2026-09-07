"""Climbing the ladder (SPEC §4).

The rule that makes the whole design safe: we escalate **only** when the free
verifier has already refused the cheaper answer, and we keep the model's
answer **only** if it reconciles. A model that makes things worse is
discarded, silently and deterministically.

Synthetic pages, stub client, no PDF and no network.
"""

import json
from dataclasses import dataclass, field

from statement_tieout.api import extract_period
from statement_tieout.llm.client import Completion, ToolTurn

from .helpers import DATE_X, DESC_X, LEFT_X, line, page, two_column_row


@dataclass
class StubClient:
    """Answers rung 2 from a script; declines to repair anything on rung 4."""

    replies: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    tool_turns: list[ToolTurn] = field(default_factory=list)

    def complete_json(self, system: str, user: str, schema: dict) -> Completion:
        self.calls.append(user)
        return Completion(
            content=self.replies.pop(0) if self.replies else "{}",
            prompt_tokens=500,
            completion_tokens=40,
        )

    def complete_with_tools(self, system: str, transcript: list[dict], tools: list[dict]):
        self.calls.append(transcript[0]["text"])
        if self.tool_turns:
            return self.tool_turns.pop(0)
        return ToolTurn(text="I cannot close this", prompt_tokens=300, completion_tokens=20)


def reconciling_page():
    """A statement the deterministic rung reads on its own.

    Six rows so that both amount columns clear the alignment rule, and an
    unbroken running balance so the balance column is recognised as one.
    """
    header = line(60.0, (DESC_X, "ACME BANK"))
    totals = [
        line(72.0, (DESC_X, "Beginning balance"), (LEFT_X, "1,000.00")),
        line(84.0, (DESC_X, "Total deposits"), (LEFT_X, "600.00")),
        line(96.0, (DESC_X, "Total withdrawals"), (LEFT_X, "180.00")),
        line(108.0, (DESC_X, "Ending balance"), (LEFT_X, "1,420.00")),
    ]
    ledger = [
        ("IN A", "100.00", None, "1,100.00"),
        ("IN B", "200.00", None, "1,300.00"),
        ("OUT C", None, "50.00", "1,250.00"),
        ("IN D", "300.00", None, "1,550.00"),
        ("OUT E", None, "60.00", "1,490.00"),
        ("OUT F", None, "70.00", "1,420.00"),
    ]
    rows = [
        two_column_row(140.0 + i * 12.0, f"01/0{i + 1}/2025", description,
                       deposit=deposit, withdrawal=withdrawal, balance=balance)
        for i, (description, deposit, withdrawal, balance) in enumerate(ledger)
    ]
    return page(header, *totals, *rows)


def unreadable_page():
    """Rows whose side nothing on the page reveals: rung 0 declines."""
    header = [
        line(60.0, (DESC_X, "ACME BANK")),
        line(72.0, (DESC_X, "Beginning balance"), (LEFT_X, "1,000.00")),
        line(84.0, (DESC_X, "Ending balance"), (LEFT_X, "1,250.00")),
    ]
    rows = [
        line(100.0 + i * 12.0, (DATE_X, f"01/0{i + 1}/2025"), (DESC_X, f"ROW{i}"),
             (LEFT_X, amount))
        for i, amount in enumerate(("100.00", "200.00", "50.00"))
    ]
    return page(*header, *rows)


class TestNoEscalationWhenTheFreePathWorks:
    def test_a_reconciling_period_never_calls_the_model(self):
        client = StubClient()
        result, _, usage = extract_period([reconciling_page()], client)
        assert result.reconciliation.reconciled is True
        assert client.calls == []
        assert usage.calls == 0

    def test_the_path_is_reported_as_deterministic(self):
        _, _, usage = extract_period([reconciling_page()], StubClient())
        assert usage.cost_usd == 0.0


class TestEscalationWhenItDoesNot:
    def test_the_model_is_asked_only_after_the_verifier_refuses(self):
        client = StubClient([json.dumps({
            "date_column": {"x0": 20.0, "x1": 90.0},
            "amount_columns": [{"x0": 320.0, "x1": 400.0}],
            "balance_column": None,
            "side_strategy": "signed",
            "date_formats": ["%m/%d/%Y"],
            "deposit_sections": [],
            "withdrawal_sections": [],
        })])
        extract_period([unreadable_page()], client)
        assert client.calls, "the model was never asked"
        assert "reconcile" in client.calls[0], "the first ask carries the verifier's refusal"

    def test_the_residual_is_handed_to_the_model_as_feedback(self):
        client = StubClient(["{}"])
        extract_period([unreadable_page()], client)
        assert "reconcile" in client.calls[0]

    def test_a_model_answer_that_does_not_reconcile_is_discarded(self):
        """A wrong profile must not replace an honest failure."""
        nonsense = json.dumps({
            "date_column": {"x0": 900.0, "x1": 950.0},
            "amount_columns": [{"x0": 900.0, "x1": 950.0}],
            "balance_column": None,
            "side_strategy": "signed",
            "date_formats": ["%m/%d/%Y"],
            "deposit_sections": [],
            "withdrawal_sections": [],
        })
        client = StubClient([nonsense, nonsense, nonsense])
        result, _, _ = extract_period([unreadable_page()], client)
        assert result.reconciliation.reconciled is False
        assert result.transactions == []

    def test_usage_is_reported_even_when_the_model_did_not_help(self):
        client = StubClient(["{}", "{}", "{}"])
        _, _, usage = extract_period([unreadable_page()], client)
        assert usage.calls == 4  # three profile attempts plus one repair turn
        assert usage.prompt_tokens == 1800


class TestNoClientConfigured:
    def test_without_a_client_the_period_simply_reports_its_failure(self):
        result, warnings, usage = extract_period([unreadable_page()], None)
        assert result.reconciliation.reconciled is False
        assert usage.calls == 0
        assert any("profile" in w for w in warnings)


class TestRepairRung:
    """SPEC §4 stage 9 — rung 4 runs last, and only on what is still broken."""

    def broken(self):
        """Printed totals say 300/50 over three rows; one deposit was never parsed."""
        header = [
            line(60.0, (DESC_X, "ACME BANK")),
            line(72.0, (DESC_X, "Beginning balance"), (LEFT_X, "1,000.00")),
            line(84.0, (DESC_X, "Total deposits"), (LEFT_X, "300.00")),
            line(96.0, (DESC_X, "Total withdrawals"), (LEFT_X, "50.00")),
            line(108.0, (DESC_X, "Ending balance"), (LEFT_X, "1,250.00")),
        ]
        ledger = [
            ("IN A", "100.00", None, "1,100.00"),
            ("OUT B", None, "50.00", "1,050.00"),
            ("IN C", "20.00", None, "1,070.00"),
            ("OUT D", None, "10.00", "1,060.00"),
            ("IN E", "30.00", None, "1,090.00"),
            ("OUT F", None, "40.00", "1,050.00"),
        ]
        rows = [
            two_column_row(140.0 + i * 12.0, f"01/0{i + 1}/2025", description,
                           deposit=deposit, withdrawal=withdrawal, balance=balance)
            for i, (description, deposit, withdrawal, balance) in enumerate(ledger)
        ]
        return page(*header, *rows)

    def test_it_is_not_reached_when_the_period_reconciles(self):
        client = StubClient()
        extract_period([reconciling_page()], client)
        assert client.calls == []

    def test_it_runs_after_rung_2_has_failed(self):
        """Rung 2 answers nothing usable, so the repair loop gets its turn."""
        client = StubClient(["{}", "{}", "{}"])
        _, _, usage = extract_period([self.broken()], client)
        assert usage.calls == 4  # three profile attempts, then one repair turn

    def test_a_repair_that_does_not_close_the_period_changes_nothing(self):
        client = StubClient(["{}"] * 20)
        result, _, _ = extract_period([self.broken()], client)
        assert result.reconciliation.reconciled is False
        assert len(result.transactions) == 6


class TestRungZeroOffersSeveralProfiles:
    """SPEC §4 stage 3 — the free rung produces candidates, the verifier chooses.

    Measured on the Ixonia binder: the page with the most date-and-money
    lines was a cheque listing, not the table, and its profile misplaced a
    column for the whole period. The table page's profile reconciles; it is
    reached without a model and without a network.
    """

    def listing_page(self, number: int = 2):
        """Denser than the table, and laid out nothing like it."""
        return page(*[
            line(100.0 + i * 12.0, (DATE_X, f"01/1{i}/2025"), (90.0, f"4078{i}"),
                 (150.0, f"{100 + i}.00"), (250.0, f"01/2{i}/2025"), (310.0, f"4079{i}"),
                 (370.0, f"{300 + i}.00"))
            for i in range(8)
        ], number=number)

    def test_the_period_reconciles_on_the_table_page_profile(self):
        period, _, usage = extract_period([reconciling_page(), self.listing_page()], None)
        assert period.reconciliation.reconciled
        assert usage.calls == 0
        assert len(period.transactions) == 6

    def test_the_model_is_not_asked_when_a_free_candidate_reconciles(self):
        client = StubClient(replies=["{}"])
        period, _, _ = extract_period([reconciling_page(), self.listing_page()], client)
        assert period.reconciliation.reconciled
        assert client.calls == []


class TestWhichFreeAttemptStands:
    """SPEC §7.21 — when no candidate reconciles, the one that fails least badly.

    Reconciled first; then more checks `ok`; then fewer rows whose amount had
    to be inferred from the balance step (§7.10) — an inferred amount is the
    tool's own doubt, and a profile that sees the amounts printed on the page
    is a better reading than one that has to work them out; then the smaller
    residual.
    """

    def rank(self, *, reconciled=False, ok=(), recovered=0, residual="0.00"):
        from decimal import Decimal

        from statement_tieout.api import free_rank
        from statement_tieout.parse.rows import ParsedRows
        from statement_tieout.schema import CheckState, Reconciliation, Transaction

        checks = {name: CheckState.FAIL for name in ("balance_equation", "deposits_total")}
        checks.update(dict.fromkeys(ok, CheckState.OK))
        rows = [
            Transaction(date="2025-01-01", description="x", deposit=Decimal("1.00"),
                        recovered=i < recovered)
            for i in range(max(recovered, 1))
        ]
        return free_rank(
            Reconciliation(reconciled=reconciled, checks=checks, residual=Decimal(residual)),
            ParsedRows(transactions=rows),
        )

    def test_reconciled_beats_everything(self):
        assert self.rank(reconciled=True, ok=("balance_equation",), residual="0.00") > self.rank(
            ok=("balance_equation", "deposits_total"), residual="0.01"
        )

    def test_more_checks_ok_beats_fewer(self):
        assert self.rank(ok=("balance_equation",), residual="900.00") > self.rank(residual="1.00")

    def test_fewer_inferred_rows_beats_a_smaller_residual(self):
        assert self.rank(recovered=6, residual="152000.00") > self.rank(
            recovered=86, residual="118000.00"
        )

    def test_the_smaller_residual_wins_last(self):
        assert self.rank(residual="-10.00") > self.rank(residual="20.00")


def listing_lines(top: float, *rows, heading: str = "CHECKS PAID"):
    """A cheque listing, two to a line, under its own repeated header."""
    lines = [
        line(top, (DESC_X, heading)),
        line(top + 12.0, (DATE_X, "Date"), (80.0, "Check No."), (150.0, "Amount"),
             (200.0, "Date"), (240.0, "Check No."), (300.0, "Amount")),
    ]
    for index, cells in enumerate(rows):
        lines.append(line(top + 24.0 + index * 12.0, *cells))
    return lines


def period_with_cheques(*, in_the_table: bool):
    """Six table rows and two cheques. The cheques are either *only* in the
    listing, or in the table as well — the listing looks the same both ways."""
    cheques = [("1693", "363.80"), ("1694", "386.02")]
    table = [
        ("IN A", "100.00", None),
        ("IN B", "200.00", None),
        ("OUT C", None, "50.00"),
        ("IN D", "300.00", None),
        ("OUT E", None, "60.00"),
        ("OUT F", None, "70.00"),
    ]
    if in_the_table:
        table += [(f"CHECK {number}", None, amount) for number, amount in cheques]
    withdrawals = "929.82"  # 180.00 of table rows plus the two cheques, counted once
    header = line(60.0, (DESC_X, "ACME BANK"))
    totals = [
        line(72.0, (DESC_X, "Beginning balance"), (LEFT_X, "1,000.00")),
        line(84.0, (DESC_X, "Total deposits"), (LEFT_X, "600.00")),
        line(96.0, (DESC_X, "Total withdrawals"), (LEFT_X, withdrawals)),
        line(108.0, (DESC_X, "Ending balance"), (LEFT_X, "670.18")),
    ]
    rows = [
        two_column_row(140.0 + i * 12.0, f"01/0{i % 9 + 1}/2025", description,
                       deposit=deposit, withdrawal=withdrawal)
        for i, (description, deposit, withdrawal) in enumerate(table)
    ]
    listing = listing_lines(
        140.0 + len(rows) * 12.0 + 12.0,
        ((DATE_X, "01/02/2025"), (80.0, cheques[0][0]), (150.0, cheques[0][1]),
         (200.0, "01/21/2025"), (262.0, cheques[1][0]), (300.0, cheques[1][1])),
    )
    return page(header, *totals, *rows, *listing)


class TestAListingIsAddedOnlyWhenTheVerifierAgrees:
    """SPEC §7.22 — the same listing is the only record of its cheques on one
    statement and a duplicate view of the table on another, and nothing on
    the page tells the two apart. The verifier does: the listing is added to
    the rows when that reconciles the period, and left out when it does not."""

    def test_cheques_printed_only_in_the_listing_are_added(self):
        period, warnings, _ = extract_period([period_with_cheques(in_the_table=False)], None)
        assert period.reconciliation.reconciled
        assert len(period.transactions) == 8
        assert any("listing" in w for w in warnings)

    def test_a_listing_that_duplicates_the_table_is_left_out(self):
        period, warnings, _ = extract_period([period_with_cheques(in_the_table=True)], None)
        assert period.reconciliation.reconciled
        assert len(period.transactions) == 8
        assert any("listing" in w and "left out" in w for w in warnings)

    def test_listing_rows_keep_document_order(self):
        period, _, _ = extract_period([period_with_cheques(in_the_table=False)], None)
        assert [t.line for t in period.transactions] == sorted(t.line for t in period.transactions)
