# CLAUDE.md — agent rules for this repository

Take-home assignment: extract structured data from bank statement PDFs and
report whether it reconciles. The process is graded, not just the code. These
rules bind every change an agent makes here.

## Source of truth

- **SPEC.md defines behavior.** Code implements SPEC; the eval harness
  asserts SPEC. If a change alters behavior, update SPEC.md in the same
  commit. A heuristic, threshold or vocabulary that affects observable
  behavior and is not declared in SPEC §7 is a bug ("hidden hardcoded
  behavior").
- **`ExtractResult` in `schema.py` is the single source of the output
  contract.** The CLI, the eval harness and the LLM structured-output schema
  all derive from it. Never invent, rename or duplicate schema fields.
- Architecture decisions live in `docs/adr/`. Do not silently deviate from an
  accepted ADR — flag the conflict instead.

## The non-negotiable rules of this domain

- **No `float` for money, anywhere.** `Decimal` from string to output;
  `float` appears only in JSON serialization. Reconciliation tolerance is
  exactly zero — a one-cent residual is a failure.
- **No bank-specific or file-specific branches.** No condition may key on a
  bank name, a filename, or a sample's page count. Generalization to unseen
  statements is a third of the grade; a parser that recognizes our four files
  scores zero on it.
- **Heuristics are tuned on the tuning file only** (SPEC §10.1). Every other
  sample is held out. Do not look at a held-out file to choose a threshold.
- **A failure is reported, never hidden.** A period that does not reconcile
  is returned with its residual, its failing checks and a diagnosis. A check
  whose input the statement did not print is `unavailable`, never `ok`.
- **The model never transcribes what cannot be verified** (SPEC §7.17). It
  returns layout profiles, not rows; page-at-a-time transcription is allowed
  only where a local arithmetic check can accept or reject it.

## Engineering discipline

- **Test before implementation** for every deterministic module: money
  parsing, reconciliation checks, residual diagnosis, period segmentation,
  row parsing under a given profile. Commit the failing test first, the
  implementation second.
- **Unit tests never require an LLM, a network, or a sample PDF.** Anything
  nondeterministic is stubbed (`StubProfiler`); parsing tests run on
  synthetic word/coordinate fixtures. The eval harness is the only place
  that touches real PDFs and the real model.
- Run `uv run pytest` and `uv run ruff check .` before every commit; never
  commit red.
- Commits: small, English, one logical step each, prefixed
  `feat|fix|test|docs|chore|ci|eval`. No AI-attribution footers.

## Scope

Do not add features beyond SPEC (HTTP API, UI, categorization, persistence,
batch processing). Cut scope is recorded in README, not silently implemented.
Samples live in `samples/` and are gitignored — never commit a statement PDF.
Secrets exist only in the environment.
