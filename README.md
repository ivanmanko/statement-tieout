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
| Tests | 199 unit tests — no LLM, no network, no sample PDF required |
| Accuracy | measured by `evals/run_evals.py`, table below |
| Cost | **$0.00 and zero API calls** on every sample, scans included |

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

| rung | producer | cost | built |
|---|---|---|---|
| 0 | layout profile derived from word coordinates | $0 | yes |
| 1 | cached profile for a known template fingerprint | $0 | no |
| 2 | model profiles the layout from 1–2 **sample pages** | ~1 call | yes |
| 3 | model transcribes pages OCR reads poorly | N calls | no |
| 4 | bounded agentic repair of a period that will not close | bounded | no |

A model asked to transcribe three hundred rows will drop or double a few, and
the balance equation will say so immediately — that is the trap the sample
files are built around. So when the model is used it returns a *layout
profile* — where the columns are, how the sides are marked — and the rows are
then read deterministically. The rule is not "never let a model read a table";
it is **never transcribe what you cannot verify**.

Two rules make the ladder safe, and both are tested: the model is asked
**only after** the verifier has refused the free answer, and its profile is
kept **only if** the rows it produces reconcile. A model that makes things
worse is discarded, so it can never turn an honest failure into a confident
wrong result.

**Every sample below is handled at rung 0**, so rung 2 never fires on them and
every number in the accuracy table was produced without an API key. See
[Cut scope](#cut-scope).

## Scans are ingest, not a special case

Two of the three samples have **no text layer at all** — and not the big one:
a 565 KB file and a 6.2 MB file are pure scans while the 13.7 KB one is
digital. Both scans are rasterized digital documents, crisp machine-set type
rather than photographs.

So a scanned page is rendered and read by a local OCR engine (ONNX,
pip-installable, no system dependency, no network) which returns **text with
bounding boxes** — exactly the words-with-coordinates a text layer gives.
Everything downstream is then identical, and OCR's inevitable digit errors
meet the same free verifier as everything else
([ADR-005](docs/adr/005-ocr-is-ingest-not-a-rung.md)).

Measured: 284 transactions across 15 scanned pages, reconciling to the cent
against the printed totals, in 51 seconds, for **$0.00**.

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
exact equality against ground truth a human read off the printed page — off a
rendering of it, for the scans — recorded in `evals/expected/`.

| file | ingest | periods | rows | reconciled | checks ok | residual | match | calls | cost | latency |
|---|---|---|---|---|---|---|---|---|---|---|
| Great Lakes x4071 2025-01 | text | 1 | 292 | **yes** | 4/6 | 0.00 | **11/11** | 0 | $0.00 | 0.2 s |
| April 2021 (Fulton Bank) | **OCR** | 1 | 284 | **yes** | 4/6 | 0.00 | **8/8** | 0 | $0.00 | 51 s |
| S2.6.1.1-2 Acct 6426 (Renasant) | **OCR** | 1 | 6 of 10 | no | 1/6 | 13,871.16 | 7/9 | 0 | $0.00 | 6 s |

Three banks, three unrelated layouts, one parser, no bank-specific code.

**Great Lakes** — the tuning file (SPEC §10.1). Every ground-truth field
exact. One signed amount column plus a running balance.

**April 2021** — held out, and the strongest result here: fifteen scanned
pages, a *horizontal* summary block (labels in one row, amounts in the next),
two amount columns of which the deposits column carries only 16% of the rows,
and a logo set at 18.4 pt beside a 10.4 pt address on the same line. Every
ground-truth field exact; the period reconciles to the cent.

**S2.6.1.1-2 Account 6426** — held out, and the one that does not reconcile.
Bank, account, period and all four printed totals are read correctly, and the
printed block closes against itself. But this statement is section-based
(`CHECKS` / `OTHER DEBITS` / `CREDITS`) and its `CHECKS` section prints **two
transactions per line** — `NUMBER DATE AMOUNT NUMBER DATE AMOUNT`. The row
model reads one transaction per line, so 4 of 10 rows are missed and the
residual is 13,871.16. Named, quantified, reported.

> `Binder2_Redacted.pdf` (53.8 MB) has not been run. When it is, this section
> is regenerated from the harness rather than edited by hand.

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

- **One transaction per line.** A section printing two side by side loses
  half its rows (above). The single largest correctness gap.
- **Descriptions from a scan are less faithful.** OCR returns line segments
  and drops spaces, so an all-capitals description stays one token
  (`REMOTEDEPOSITLINK`). Amounts, dates and balances are unaffected, which is
  why reconciliation still works — but a consumer of `description` should
  know.
- **A column seen only once is not claimed.** Alignment identifies a money
  column from two occurrences; one is genuinely ambiguous with an amount
  inside a sentence, and is left out rather than guessed.
- **The bank name is the largest type on the page**, which on a scan whose
  logo OCRs onto two lines returns `RENASANT` rather than `RENASANT BANK`.
- **Ground truth for the summary was read by the same eyes that wrote the
  parser.** `printed_block_closes` catches a misread that breaks the block's
  own arithmetic, but not one that preserves it. An independent reader would
  be better.
- **Latency on scans is ~3.4 s per page**, so ~51 s for fifteen pages. Fine
  for a batch tool, wrong for an interactive one.
- **Rung 2 is unmeasured.** It is built and tested against a stub, but no
  sample has failed in a way that triggers it, so there is no real run to
  quote a cost or a latency from. The $0.00 in the table is real, and it is
  the whole story so far.

## Cut scope

Deliberate, and recorded here rather than silently implemented:

- **HTTP API and UI** — the assignment marks them "really optional".
- **Rungs 1–4** — the profile cache, the model-derived layout profile, model
  transcription, and the bounded repair agent. The seams exist
  (`LayoutProfile` is already the structured-output contract, and the ladder
  branches exactly where the verifier fails); the rungs do not. The reason is
  measurement, not time: **no sample has needed one yet.** Building a model
  path before the free path has failed would be paying for a capability with
  no evidence it is required — and the evidence, when it arrives, is a
  reconciliation failure naming the file and the residual.
- **Multi-transaction rows** — the one gap the evidence *does* point at, and
  the next thing to build. It is a change to the row model, not a rung.
- **Transaction categorization, description normalization, batch processing,
  persistence** — not asked for.

## Provider configuration

The provider is an **installation parameter**, not a build-time choice — for
a product deployed into each client's own VPC, where the model is hosted
differently everywhere, that is worth more than any particular cloud
([ADR-004](docs/adr/004-agent-harness-and-provider.md)).

```bash
cp .env.example .env     # then set LLM_API_KEY
```

`LLM_PROVIDER` selects an **OpenAI-compatible** endpoint — DeepSeek by
default, at roughly a tenth of Claude's per-token price — or **Claude**, in
which case `LLM_PLATFORM` picks first-party, Bedrock, Vertex or Foundry, all
of which expose the same `messages.create` through the same SDK. Nothing else
in the codebase knows which is in use.

With nothing configured, `build_client()` returns `None` and the
deterministic path runs alone. That is a supported mode, not a degraded one:
it is what produced every number above.

DeepSeek supports `json_object` but not strict `json_schema`, so the schema
travels in the prompt and the answer is validated on our side — and then
again, more usefully, by reconciliation. Note that rung 2 needs **no vision
model** even for scans: OCR ingest has already turned the pixels into words
with coordinates, and that is what the model is shown.

The agent loop for rung 4 will be the Anthropic SDK's tool runner over domain
tools, **not** the Claude Agent SDK: `extract()` must stay an ordinary Python
function that runs after `pip install` with one credential.

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
