# ADR-004: A provider-neutral tool loop; provider is an install parameter

- **Status:** Accepted
- **Date:** 2026-09-02

## Context

Two questions that look like one: what harness runs the agentic repair loop
(rung 4), and which vendor endpoint serves the model. The deliverable is a
plain function, `extract(pdf_path) -> dict`, that the reviewers will run on
their own machines against statements we have never seen.

## Decision

**Harness:** a small tool-call loop of our own, over tools we define
(`state`, `read_page`, `find_amount`, `list_rows`, `insert_row`, `drop_row`,
`set_side`), with the SPEC §7.17 ceilings on turns and dollars checked before
each turn.

> **Amended in implementation.** This ADR originally specified
> `client.beta.messages.tool_runner` from the `anthropic` package. That was
> written before the provider became DeepSeek in practice, and a
> vendor-specific loop cannot serve a provider chosen by env var — which is
> the entire point of the section below. The loop now keeps a
> **provider-neutral transcript** and each client renders it into its own wire
> format (`tool_calls`/`tool` messages for OpenAI-compatible,
> `tool_use`/`tool_result` blocks for Anthropic). About forty lines per
> provider, and the loop itself is written once.

**Provider:** selected by `LLM_PROVIDER` env var among `anthropic` (default),
`bedrock`, `vertex`, `foundry`. The same SDK ships `AnthropicBedrockMantle`,
`AnthropicVertex` and `AnthropicFoundry`, all exposing the same
`messages.create`, so this is one small module and no branching anywhere else.

## Alternatives considered

1. **`claude-agent-sdk` (Claude Code as a library).** Rejected. It supplies
   the Claude Code harness with built-in Read/Write/Edit/Bash tools and its
   own runtime — none of which we want, because our tools are domain tools
   over a parsed statement, not filesystem tools. It also makes `extract()`
   depend on a second runtime being installed and authenticated on the
   reviewer's machine, which is exactly the friction to avoid when the
   deliverable is a function.
2. **Shelling out to `claude -p`.** Rejected for the same deployment reason,
   plus reproducibility: `summary` is graded on exact match, and a subprocess
   agent re-reading a large PDF will not produce the same answer twice.
3. **A hand-written loop.** Originally rejected in favour of the Tool
   Runner's per-turn hooks; **adopted after all**, because a loop tied to one
   vendor's SDK cannot serve a provider selected by an env var. The ceilings
   turned out to be two comparisons at the top of the loop — less code than
   the hook plumbing they were meant to justify.
4. **Pinning one cloud (Bedrock, because their stack is AWS + Google).**
   Rejected as a *choice*: making the provider an installation parameter is
   strictly better for a product deployed into each client's private VPC,
   and it costs almost nothing given the SDK's client classes.

## Consequences

- `extract()` is an ordinary Python function; the only runtime requirement
  is `pip install` and, for the LLM rungs, one credential.
- Rung 4 is bounded by construction: hooks refuse the next turn once the
  turn count or the dollar ceiling is reached.
- Switching clouds is an env var, and the README says so — which is the
  claim that matters for a product deployed per-client.

## Revisit when

A provider appears whose tool-calling wire format neither renderer covers, or
the neutral transcript stops being able to express what a provider needs. That
translation is the only vendor-specific code left in the repair path, so it is
where the pressure would show first.
