# Statement Tie-Out

Extracts account identity, period totals and transactions from a bank
statement PDF — and **reports whether the result reconciles**, per period,
naming the residual and the probable cause when it does not.

```python
from statement_tieout import extract
result = extract("statement.pdf")     # -> dict
```

Start with **[SPEC.md](SPEC.md)**: it was written before the code and is what
the eval harness asserts against. Decisions are in [docs/adr/](docs/adr/);
what went wrong along the way is in [docs/ai/AI_NOTES.md](docs/ai/AI_NOTES.md).

| | |
|---|---|
| Tests | 154 unit tests — no LLM, no network, no sample PDF required |
| Accuracy | measured by `evals/run_evals.py`, table below |
| Cost on a text-layer statement | **$0.00** — the deterministic rung makes no API call |

## The idea

The assignment grades three things: `summary` on exact match, transactions
that "reconcile with totals", and generalization to unseen statements. Read
together they hand you something unusual: **a free, automatic oracle**. The
balance equation and the printed transaction counts let the extractor decide
for itself whether it got the answer right — no labels, no reference output,
no human.

Two consequences shape everything here.

**The summary is *read*, not computed** ([ADR-001](docs/adr/001-summary-from-printed-block.md)).
Almost every statement prints its own totals block. If you instead sum the
parsed rows into `summary`, then "the summary matches" and "the transactions
reconcile" become the same event, and the check proves nothing. Reading the
block keeps the two views independent, which is what makes comparing them
informative.

**The model is not in the hot path** ([ADR-002](docs/adr/002-escalation-ladder.md)).
Extraction is an escalation ladder. Each rung produces a candidate, the same
free verifier accepts or rejects it, and we climb only on rejection:

| rung | producer | cost |
|---|---|---|
| 0 | layout profile derived from word coordinates | $0 |
| 1 | cached profile for a known template fingerprint | $0 |
| 2 | model profiles the layout from 1–2 **sample pages** | ~1 call / statement |
| 3 | vision transcription, one page per call (scans) | N calls |
| 4 | bounded agentic repair of a period that will not close | bounded |

A model asked to transcribe three hundred rows will drop or double a few, and
the balance equation will say so immediately — that is the trap the sample
files are built around. So on rung 2 the model returns a *layout profile* —
where the columns are, how the sides are marked — and the rows are then read
deterministically. The rule is not "never let a model read a table"; it is
**never transcribe what you cannot verify**.

**Rungs 2–4 are not built in this delivery.** See [Cut scope](#cut-scope).

## Reconciliation

Six checks, each `ok` / `fail` / **`unavailable`**:

| check | what it evidences |
|---|---|
| `printed_block_closes` | the printed block is internally consistent |
| `balance_equation` | **the parsed rows close the printed balances** |
| `deposits_total` / `withdrawals_total` | the rows match the printed totals |
| `deposits_count` / `withdrawals_count` | no row lost or doubled |

`unavailable` means the statement did not print what the check needs — it is
never reported as `ok`, and a period is reconciled only when the rows were
actually evidenced against the page. A block agreeing with itself is not
enough.

**Tolerance is exactly zero.** All money is `Decimal` from the page to the
JSON boundary ([ADR-003](docs/adr/003-decimal-and-tri-state-checks.md)).

### The residual is an address

When counts are printed, the arithmetic of a single-row error is forced:

| what went wrong | Δdeposits | Δwithdrawals | residual |
|---|---|---|---|
| a deposit of `X` was not parsed | −1 | 0 | `−X` |
| a deposit of `X` was parsed twice | +1 | 0 | `+X` |
| a deposit of `X` landed on the withdrawal side | −1 | +1 | `−2X` |

So the output does not say "period 3 failed by 1,240.50". It says *a deposit
of 1,240.50 was not parsed, and that amount appears on page 47* — and when a
running-balance column is present, it names the row index instead of the
period. A diagnosis is **reported, never auto-applied**: a wrong guess that
is printed costs a reviewer a minute, while one that silently rewrites a
transaction produces a corrupted result that reconciles.

## Running it

```bash
uv sync
```

```bash
uv run python -m statement_tieout path/to/statement.pdf --summary
```

Drop the `--summary` for the full JSON, `-o out.json` to write it, `--log`
for the structured log line. Exit code is **0** when every period reconciled,
**1** when one did not (the result is still printed), **2** when the file
could not be read.

```
Great_Lakes_Commerce_Bank_-_x4071_-_2025-01.pdf
GREAT LAKES COMMERCE BANK · account 4071 · 2025-01-01 to 2025-01-28
1 period(s), 292 transaction(s)

period 1: reconciled
      ok  printed_block_closes
      ok  balance_equation
      ok  deposits_total
      ok  withdrawals_total
      --  deposits_count
      --  withdrawals_count

rung: deterministic · 0 LLM call(s) · $0.0000 · 0.421s
```

Tests, lint and the accuracy harness:

```bash
uv run pytest && uv run ruff check . && uv run python evals/run_evals.py
```

Statement PDFs are **not committed** (`samples/` is gitignored). Put the
assignment's files there before running the harness.

## Measured accuracy

Produced by `uv run python evals/run_evals.py`, not estimated. `match` is
exact equality against ground truth a human read off the printed page,
recorded in `evals/expected/`.

| file | periods | rows | reconciled | checks ok | residual | match | LLM calls | cost | latency |
|---|---|---|---|---|---|---|---|---|---|
| Great Lakes x4071 2025-01 | 1 | 292 | **yes** | 4/6 | 0.00 | **11/11** | 0 | $0.0000 | 0.42 s |
| S2.6.1.1-2 Account 6426 | 1 | 0 | no | 0/6 | — | n/a | 0 | $0.0000 | 0.01 s |

**Great Lakes** — the tuning file (SPEC §10.1). Every ground-truth field
exact: bank, account, period, all six summary values, period count and
transaction count. The two count checks are `unavailable` because this
statement prints totals but no counts; the counts in the output are derived
and marked as such.

**S2.6.1.1-2 Account 6426** — four pages, **zero characters of text layer**.
A pure scan. The extractor reads nothing, reports every check `unavailable`,
names the pages in a warning, and exits non-zero. Reading it needs rung 3,
which is out of scope here. No ground truth is recorded for it because
obtaining any would require the transcription that is missing.

> `Binder2_Redacted.pdf` (53.8 MB) and `April 2021.pdf` (6.2 MB) are not yet
> in this table. When they are run, this section is regenerated from the
> harness rather than edited by hand.

## Generalization

A third of the grade, and the reason for two rules that are enforced rather
than intended:

- **No bank-specific or file-specific branch exists.** No condition anywhere
  keys on a bank name, a filename, or a page count. The layout profile is the
  only thing that varies between statements, and it is data.
- **Heuristics were tuned on one file only.** Great Lakes is the declared
  tuning file; every other sample is held out (SPEC §10.1), so its result
  *measures* the heuristics instead of confirming them.

The parser is tested against four different ways a statement can mark which
side a row is on — two amount columns, a signed column, section headings, or
a running-balance delta — on synthetic fixtures rather than on the samples we
happen to have. Rung 0 **declines** when the page gives no evidence for any
of them, rather than guessing: an escalation is a correct outcome, a
confidently wrong side is not.

## Known weaknesses

- **Scans are not read at all.** Half the samples available while building
  this are scanned. Rung 3 is designed and not built.
- **A lopsided two-column layout loses its rarer column.** A money cluster
  appearing on under 30% of rows is discarded as an amount embedded in a
  description (SPEC §7.17.3). A statement that is 90% deposits would lose the
  withdrawal column — and then fail to reconcile, which is the right failure
  mode but still a failure.
- **Multi-period detection is untested against a real binder.** The anchors
  and the boilerplate guard are covered by synthetic fixtures only.
- **Ground truth for the summary is read by the same eyes that wrote the
  parser**, from the same printed block. `printed_block_closes` catches a
  misread that breaks the block's own arithmetic, but not one that preserves
  it. An independent reader would be better.
- **No cost or latency figure exists for the LLM rungs**, because they are
  not built. The $0.00 in the table is real, and it is only the free rung.

## Cut scope

Deliberate, and recorded here rather than silently implemented:

- **HTTP API and UI** — the assignment marks them "really optional".
- **Rungs 2–4** — the model-backed layout profile, vision transcription and
  the bounded repair agent. The seams exist (`LayoutProfile` is already the
  structured-output contract, and the ladder branches where the verifier
  fails), the rungs do not. This was the largest call: with the time
  available, a measured deterministic rung plus an honest report of its limit
  is worth more than an unmeasured model path.
- **OCR as an alternative rung-3 producer** — legitimate, and the verifier
  would catch its digit confusions. Not built for the same reason.
- **Transaction categorization, description normalization, batch processing,
  persistence** — not asked for.

## Provider configuration

For the rungs that use a model, the provider is an **installation
parameter**, not a build-time choice — `LLM_PROVIDER` selects `anthropic`
(default), `bedrock`, `vertex` or `foundry`, all of which expose the same
`messages.create` through the same SDK
([ADR-004](docs/adr/004-agent-harness-and-provider.md)). For a product
deployed into each client's own VPC, where the model is hosted differently
everywhere, that is worth more than any particular cloud.

The agent loop for rung 4 is the Anthropic SDK's tool runner over domain
tools, **not** the Claude Agent SDK: `extract()` must stay an ordinary Python
function that runs on a reviewer's machine with `pip install` and one
credential.

## How this was built

With Claude Code, in the discipline the repository documents:
[SPEC.md](SPEC.md) written before any code, then failing test → implementation
as separate commits for every deterministic module, `pytest` and `ruff` green
before each one. The commit history shows the pairs. Unit tests never touch a
model, a network or a sample PDF — the eval harness is the only thing that
opens a real file.

`CLAUDE.md` carries the rules that bind every change here; the two that did
the most work are "any heuristic affecting observable behavior and not
declared in SPEC §7 is a bug" and "no `float` for money, anywhere".
