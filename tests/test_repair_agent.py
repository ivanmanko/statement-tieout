"""The bounded repair loop — rung 4 (SPEC §4 stage 9, §7.17).

The one place in this project where an agentic loop is justified, and the
tests say why: the verifier is free, deterministic and automatic, so the model
is told after every move whether it helped. What is pinned here is the
bounding and the discarding, not the model's cleverness.

Stub client, scripted turns. No network.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from statement_tieout.llm.client import ToolCall, ToolTurn
from statement_tieout.pdf.model import Page, Word
from statement_tieout.reconcile.agent import repair
from statement_tieout.reconcile.repair import RepairLedger
from statement_tieout.schema import Summary, Transaction

PRINTED = {
    "beginning_balance", "ending_balance", "deposits_total",
    "deposits_count", "withdrawals_total", "withdrawals_count",
}


@dataclass
class StubClient:
    """Replays scripted turns and records the transcript it was handed."""

    turns: list[ToolTurn] = field(default_factory=list)
    seen: list[list[dict]] = field(default_factory=list)
    price = None

    def complete_with_tools(self, system, transcript, tools):
        self.seen.append(list(transcript))
        if self.turns:
            return self.turns.pop(0)
        return ToolTurn(text="done", tool_calls=[], prompt_tokens=10, completion_tokens=5)


def turn(*calls, text="", tokens=(100, 20)):
    return ToolTurn(
        text=text,
        tool_calls=[ToolCall(id=f"c{i}", name=n, arguments=a) for i, (n, a) in enumerate(calls)],
        prompt_tokens=tokens[0],
        completion_tokens=tokens[1],
    )


def broken_ledger():
    """1000 + 300 - 50 = 1250 printed; one 200.00 deposit was never parsed."""
    return RepairLedger(
        summary=Summary(
            beginning_balance=Decimal("1000.00"), ending_balance=Decimal("1250.00"),
            deposits_total=Decimal("300.00"), deposits_count=2,
            withdrawals_total=Decimal("50.00"), withdrawals_count=1,
            printed_fields=PRINTED,
        ),
        transactions=[
            Transaction(date=date(2025, 1, 1), description="ROW 0", deposit=Decimal("100.00")),
            Transaction(date=date(2025, 1, 3), description="ROW 1", withdrawal=Decimal("50.00")),
        ],
        pages=[Page(number=1, words=[Word(text="x", x0=0.0, x1=1.0, top=0.0)],
                    text="01/02 AETNA REMITTANCE 200.00 1,200.00")],
    )


THE_FIX = ("insert_row", {"when": "2025-01-02", "description": "AETNA REMITTANCE",
                          "side": "deposit", "amount": "200.00"})


class TestItRepairs:
    def test_a_dropped_row_is_recovered(self):
        book = broken_ledger()
        client = StubClient([turn(("find_amount", {"amount": "200.00"})), turn(THE_FIX)])
        repair(book, client)
        assert book.reconciled is True
        assert "AETNA REMITTANCE" in [t.description for t in book.result()]

    def test_it_stops_as_soon_as_the_period_closes(self):
        book = broken_ledger()
        client = StubClient([turn(THE_FIX), turn(("drop_row", {"index": 0}))])
        repair(book, client)
        assert len(client.seen) == 1
        assert book.reconciled is True

    def test_usage_is_accumulated(self):
        usage = repair(broken_ledger(), StubClient([turn(THE_FIX)]))
        assert usage.calls == 1
        assert usage.prompt_tokens == 100


class TestItIsBounded:
    """SPEC §7.17 — the ceilings are enforced before each turn, not hoped for."""

    def test_the_turn_ceiling_stops_it(self):
        client = StubClient([turn(("list_rows", {"start": 0})) for _ in range(50)])
        repair(broken_ledger(), client, max_turns=4)
        assert len(client.seen) == 4

    def test_the_cost_ceiling_stops_it(self):
        client = StubClient([turn(("list_rows", {"start": 0}), tokens=(1_000_000, 0))
                             for _ in range(50)])
        client.price = _Price()
        repair(broken_ledger(), client, max_turns=50, max_cost_usd=1.0)
        assert len(client.seen) <= 3

    def test_a_turn_with_no_tool_calls_ends_it(self):
        client = StubClient([turn(text="I cannot see the problem")])
        repair(broken_ledger(), client, max_turns=10)
        assert len(client.seen) == 1


class TestItDiscardsWhatDoesNotWork:
    """SPEC §4 stage 9 — a half-repair is worse than an honest failure."""

    def test_edits_that_do_not_close_the_period_are_thrown_away(self):
        book = broken_ledger()
        client = StubClient([
            turn(("insert_row", {"when": "2025-01-02", "description": "WRONG",
                                 "side": "deposit", "amount": "7.00"})),
            turn(text="giving up"),
        ])
        repair(book, client)
        assert book.reconciled is False
        assert [t.description for t in book.result()] == ["ROW 0", "ROW 1"]


class TestWhatTheModelIsTold:
    def test_the_first_message_carries_the_state_and_the_diagnosis(self):
        client = StubClient([turn(text="ok")])
        repair(broken_ledger(), client)
        first = client.seen[0][0]["text"]
        assert "-200.00" in first
        assert "deposit" in first

    def test_every_tool_result_is_fed_back(self):
        client = StubClient([turn(("list_rows", {"start": 0})), turn(text="ok")])
        repair(broken_ledger(), client)
        second = client.seen[1]
        assert any(entry["role"] == "tool" for entry in second)
        assert any("ROW 0" in entry.get("text", "") for entry in second)

    def test_an_unknown_tool_is_reported_back_not_fatal(self):
        client = StubClient([turn(("fly_to_the_moon", {})), turn(text="ok")])
        repair(broken_ledger(), client)
        results = [e for e in client.seen[1] if e["role"] == "tool"]
        assert "unknown tool" in results[0]["text"]

    def test_bad_arguments_are_reported_back_not_fatal(self):
        client = StubClient([turn(("drop_row", {"wrong": 1})), turn(text="ok")])
        repair(broken_ledger(), client)
        results = [e for e in client.seen[1] if e["role"] == "tool"]
        assert "argument" in results[0]["text"]


@dataclass
class _Price:
    input_per_mtok: float = 1.0
    output_per_mtok: float = 1.0

    def of(self, completion):
        return (completion.prompt_tokens + completion.completion_tokens) / 1_000_000
