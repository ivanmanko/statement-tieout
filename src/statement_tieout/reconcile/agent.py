"""Rung 4: the bounded repair loop (SPEC §4 stage 9, §7.17).

This is the one place in the project where an agentic loop earns its cost, and
the reason is the same one the whole design rests on: the verifier is free,
deterministic and automatic. Every tool the model calls answers with the fresh
verdict, so it is *told* whether a move helped rather than asked to judge its
own work — which is what separates a loop that converges from one that
wanders.

Two properties are enforced here rather than requested in the prompt. The
ceilings on turns and dollars are checked before each turn. And the edits are
kept only if the period ends up reconciled; otherwise the whole repair is
discarded, so a model can never turn an honest failure into a confident wrong
answer.
"""

from __future__ import annotations

import json

from ..llm.client import LLMClient, ToolCall, Usage
from .repair import RepairLedger

MAX_REPAIR_TURNS = 12
MAX_REPAIR_COST_USD = 0.25

SYSTEM = """A bank statement period did not reconcile. The printed totals are \
trusted; the parsed rows are not. Find what the parser got wrong and fix it \
with the tools.

Every tool answers with the new verdict, so you always know whether your last \
move helped. Work in small steps and check. Prefer finding the evidence on the \
page over guessing: `find_amount` tells you whether an amount appears on a \
page and whether a row already carries it.

Stop as soon as the period reconciles. If you cannot make it reconcile, say \
so and stop — a wrong repair is worse than none, and will be discarded."""

#: SPEC §7.17. Descriptions are what the model plans against, so they say what
#: each tool is *for*, not merely what it takes.
TOOLS: list[dict] = [
    {
        "name": "state",
        "description": "Re-run the six checks. Returns the residual and what still fails.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "read_page",
        "description": "The raw text of one page of this period.",
        "parameters": {
            "type": "object",
            "properties": {"number": {"type": "integer"}},
            "required": ["number"],
        },
    },
    {
        "name": "find_amount",
        "description": (
            "Whether an amount appears on any page, and whether a parsed row already "
            "carries it. The fastest way to tell a dropped row from a doubled one."
        ),
        "parameters": {
            "type": "object",
            "properties": {"amount": {"type": "string"}},
            "required": ["amount"],
        },
    },
    {
        "name": "list_rows",
        "description": "Parsed rows in a window, with their indexes, sides and amounts.",
        "parameters": {
            "type": "object",
            "properties": {
                "start": {"type": "integer"},
                "end": {"type": "integer"},
            },
            "required": ["start"],
        },
    },
    {
        "name": "insert_row",
        "description": "Add a transaction the parser missed. Dates are YYYY-MM-DD.",
        "parameters": {
            "type": "object",
            "properties": {
                "when": {"type": "string"},
                "description": {"type": "string"},
                "side": {"type": "string", "enum": ["deposit", "withdrawal"]},
                "amount": {"type": "string"},
            },
            "required": ["when", "description", "side", "amount"],
        },
    },
    {
        "name": "drop_row",
        "description": "Remove a row the parser read twice.",
        "parameters": {
            "type": "object",
            "properties": {"index": {"type": "integer"}},
            "required": ["index"],
        },
    },
    {
        "name": "set_side",
        "description": "Move a row to the other side when it was read as the wrong one.",
        "parameters": {
            "type": "object",
            "properties": {
                "index": {"type": "integer"},
                "side": {"type": "string", "enum": ["deposit", "withdrawal"]},
            },
            "required": ["index", "side"],
        },
    },
]


def repair(
    ledger: RepairLedger,
    client: LLMClient,
    *,
    max_turns: int = MAX_REPAIR_TURNS,
    max_cost_usd: float = MAX_REPAIR_COST_USD,
) -> Usage:
    """Let the model try to close this period, within a hard ceiling."""
    usage = Usage()
    transcript: list[dict] = [{"role": "user", "text": _opening(ledger)}]
    price = getattr(client, "price", None)

    for _ in range(max_turns):
        if usage.cost_usd >= max_cost_usd:
            break

        turn = client.complete_with_tools(SYSTEM, transcript, TOOLS)
        usage.add(turn.as_completion(), price)
        transcript.append(
            {
                "role": "assistant",
                "text": turn.text,
                "tool_calls": turn.tool_calls,
                "raw": turn.raw,
            }
        )
        if not turn.tool_calls:
            break

        for call in turn.tool_calls:
            transcript.append(
                {"role": "tool", "call_id": call.id, "name": call.name,
                 "text": _run(ledger, call)}
            )
        if ledger.reconciled:
            break

    return usage


def _opening(ledger: RepairLedger) -> str:
    return f"{ledger.state()}\n\nThe first twenty rows:\n{ledger.list_rows(0, 20)}"


def _run(ledger: RepairLedger, call: ToolCall) -> str:
    """Execute one tool. Every failure is answered, never raised."""
    handler = {
        "state": lambda: ledger.state(),
        "read_page": ledger.read_page,
        "find_amount": ledger.find_amount,
        "list_rows": ledger.list_rows,
        "insert_row": ledger.insert_row,
        "drop_row": ledger.drop_row,
        "set_side": ledger.set_side,
    }.get(call.name)
    if handler is None:
        return f"unknown tool {call.name!r}; the tools are {', '.join(t['name'] for t in TOOLS)}"
    try:
        return handler(**call.arguments)
    except TypeError as error:
        return f"bad argument: {error}"


def arguments_of(raw: str | dict) -> dict:
    """Tool arguments as a dict, whichever way the provider encoded them."""
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
