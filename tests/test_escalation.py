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
from statement_tieout.llm.client import Completion

from .helpers import DATE_X, DESC_X, LEFT_X, line, page, two_column_row


@dataclass
class StubClient:
    replies: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)

    def complete_json(self, system: str, user: str, schema: dict) -> Completion:
        self.calls.append(user)
        return Completion(
            content=self.replies.pop(0) if self.replies else "{}",
            prompt_tokens=500,
            completion_tokens=40,
        )


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
        assert len(client.calls) == 1

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
        assert usage.calls == 3
        assert usage.prompt_tokens == 1500


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
        assert usage.calls > 3  # three profile attempts, then the repair loop

    def test_a_repair_that_does_not_close_the_period_changes_nothing(self):
        client = StubClient(["{}"] * 20)
        result, _, _ = extract_period([self.broken()], client)
        assert result.reconciliation.reconciled is False
        assert len(result.transactions) == 6
