# ADR-004: Tool Runner from the Anthropic SDK; provider is an install parameter

- **Status:** Accepted
- **Date:** 2026-09-02

## Context

Two questions that look like one: what harness runs the agentic repair loop
(rung 4), and which vendor endpoint serves the model. The deliverable is a
plain function, `extract(pdf_path) -> dict`, that the reviewers will run on
their own machines against statements we have never seen.

## Decision

**Harness:** the tool-call loop from the regular `anthropic` package
(`client.beta.messages.tool_runner` over tools we define — `read_page_text`,
`find_amount`, `set_row`), with per-turn hooks enforcing the SPEC §7.15
bounds on turns and dollars.

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
3. **A hand-written `while stop_reason == "tool_use"` loop.** A close call —
   about sixty lines, no beta surface. Chosen against because the Tool
   Runner's per-turn hooks are precisely where the turn and cost ceilings
   belong, and reimplementing them is the part most likely to be wrong.
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

The repair loop needs tools the Tool Runner's hook model cannot express, or
Anthropic promotes a different loop helper out of beta.
