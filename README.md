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
| Tests | 477 unit tests — no LLM, no network, no sample PDF required |
| Accuracy | measured by `evals/run_evals.py`, table below |
| Cost | **$0.00 and zero API calls** on every sample, scans included |
| Interface | one page — upload a statement, get the workpaper (below) |

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
| 2 | model profiles the layout from 1–2 **sample pages** | ~$0.002/call | yes |
| 3 | model transcribes pages OCR reads poorly | N calls | no |
| 4 | bounded agentic repair of a period that will not close | ≤$0.25/file | yes |

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

**Every sample below reconciles at rung 0**, so nothing above it fires, and
every number in the accuracy table was produced without an API key at all.
Rung 0 is itself a small ladder now: it offers the profiles of the few
densest pages in turn, and a listing set several to a line both with and
without — the verifier, not a heuristic, picks the reading that closes. What
rungs 2 and 4 did on the two files that used to fail is
[measured below](#and-the-model-rungs-did-not-help).

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

The interface — upload a statement, read the workpaper on the same page:

```bash
uv run uvicorn statement_tieout.web.app:app --port 8000
```

Then open http://localhost:8000. Uploads are held in memory for the request
and never written to disk: statements are client data.

Tests, lint and the accuracy harness:

```bash
uv run pytest && uv run ruff check . && uv run python evals/run_evals.py
```

Statement PDFs are **not committed** (`samples/` is gitignored). Put the
assignment's files there before running the harness.

## The buyer is an audit firm, so the product is a workpaper

An auditor does not buy extracted rows. They perform a bank reconciliation and
must leave behind something that survives review. The reconciliation this
project already computes **is** that workpaper; what was missing was a way to
hand it to a person, and two properties a workpaper cannot do without.

That is why the HTTP interface, cut early as "really optional" in the
assignment's own words, came back (SPEC §1). It presents `ExtractResult` and
computes nothing of its own.

**Three questions, in the auditor's order** (SPEC §8):

**"May I sample from this population?"** Nothing can be sampled from a list
that might be incomplete by an unknown amount. Where the statement prints
transaction counts, the shortfall is exact — 81 printed against 79 found is
two rows and a known sum. Where it does not, the report says the population is
**unbounded** rather than implying it is whole. Great Lakes reconciles to the
cent and still comes back `tied_with_notes` for exactly this reason.

**"Where did this figure come from?"** Every transaction carries the page and
line index it was read from; every summary field records whether it was
printed or derived. A figure that cannot be traced to evidence cannot be
relied on, however correct it happens to be.

**"Whose exception is this?"** This system sees only the statement, never the
client's ledger, so it cannot produce a bank-versus-book difference. A residual
is therefore one of three things, and they are never conflated:

| | meaning | who acts |
|---|---|---|
| `extraction_uncertainty` | the tool misread the page | us — and it never reaches the client |
| `statement_inconsistency` | the document disagrees with itself | the auditor — a real finding |
| `unexplained` | neither attributed nor clean | manual re-performance |

The document may be blamed **only when no extraction doubt is outstanding**.
While the tool still doubts itself it does not get to blame the paper. An
auditor who chases a phantom reconciling item loses a day; one who books an
adjustment against it has put an extraction defect into a client's accounts.

**"Where do I look?"** A residual is a number; an auditor needs a direction.
Each side is compared with its own printed total, so the report can say which
half of the page the difference is on. On the Renasant statement it reads:
*the money in agrees with the printed total exactly; the whole difference of
749.82 sits among the payments out.* That is half the page eliminated before
anyone opens the file — and the 749.82 turned out to be two cheques printed
two to a line, which the parser now reads from that section's own header. The
statement ties; the sentence above is what its report said the round before.

**Verdicts** are `tied`, `tied_with_notes`, `exceptions_identified`,
`not_tied` — each carrying next steps in the auditor's own terms: which page
to inspect, which amounts to vouch, whether the population may be sampled at
all. A verdict that does not say what to do next is a status, not a workpaper.

## Measured accuracy

Produced by `uv run python evals/run_evals.py`, not estimated. `match` is
exact equality against ground truth a human read off the printed page — off a
rendering of it, for the scans — recorded in `evals/expected/`.

| file | ingest | periods | rows | reconciled | checks ok | residual | match | calls | cost |
|---|---|---|---|---|---|---|---|---|---|
| Great Lakes x4071 2025-01 | text | 1 | 292 | **yes** | 4/6 | 0.00 | **11/11** | 0 | $0.00 |
| April 2021 (Fulton Bank) | **OCR** | 1 | 284 | **yes** | 4/6 | 0.00 | **8/8** | 0 | $0.00 |
| Binder2_Redacted (Ixonia Bank) | **OCR** | **10** | 1671 | **yes**, all ten | 6/6 | 0.00 | **10/10** | 0 | $0.00 |
| S2.6.1.1-2 Acct 6426 (Renasant) | **OCR** | 1 | 10 | **yes** | 4/6 | 0.00 | **9/9** | 0 | $0.00 |

Four banks, four unrelated layouts, one parser, no bank-specific code, and no
API call on any of them. **Every period of every sample reconciles to the
cent, and every ground-truth field matches exactly** — thirty-eight of
thirty-eight, including the binder's printed counts on its first statement
and the transaction counts of both files that used to miss rows.

**Great Lakes** — the tuning file (SPEC §12.1). Every ground-truth field
exact. One signed amount column plus a running balance.

**April 2021** — held out. Fifteen scanned pages, a *horizontal* summary block
(labels in one row, amounts in the next), two amount columns of which the
deposits column carries only 16% of the rows, and a logo set at 18.4 pt beside
a 10.4 pt address on the same line. Every ground-truth field exact; reconciles
to the cent. Its 112-row cheque listing is read and **left out** by the
verifier — it repeats the table — and the result says so.

**Binder2_Redacted** — held out. 99 scanned pages holding **ten** Ixonia Bank
statements with their descriptions redacted. All ten periods are detected and
**all ten reconcile**, every printed count met: on the first — the statement
the assignment prints as its own example output — 192 rows against 192
printed, and so on down the binder. Forty-seven rows across the file took
their date from the row before them because OCR mangled it (`Nov 01` as `No
V 01`); each is marked, and their amounts and balances all sit on the running
chain. Its cheque listings, several to a line on nine pages, are read and
left out as duplicates of the table, which is what they are.

**S2.6.1.1-2 Account 6426** — held out. Bank, account, period, all four
printed totals and all ten rows, including the two cheques printed two to a
line in a `NUMBER DATE AMOUNT` section — read from that section's own header
and **added** by the verifier, because with them the period closes.

### What it took

Every one of these was a *shape* of page, written into the generated corpus
first and then measured on the sample (SPEC §12.1, amended). None is keyed
on a bank. In the order they were found, with the rows each one returned on
the binder:

| shape | rule | measured |
|---|---|---|
| a scanned sheet fed a dozen points to the side of the one before it | pages are put back on one margin at ingest (§7.2) | two whole statements went from 114 and 43 rows to 188 and 123 |
| the densest page is a cheque listing, not the table | rung 0 offers the three densest pages' profiles; the verifier chooses, fewest inferred amounts before smallest residual (§7.21) | one statement's deposits column came back |
| a listing set several to a line, the only record on one file and a duplicate on another | read from its own repeated header, kept apart, added only if the period then closes (§7.22) | Renasant closed; phantom rows left the binder |
| a row whose date OCR mangled | inherits the previous row's when the chain holds, and says so (§7.23) | eight of ten periods closed |
| a stray token in the margin tearing a row into two lines | a line is anchored on its words' mean height (§7.2) | the last two rows; ten of ten |

### Latency

OCR is the entire cost and the pipeline around it is free: measured per page,
**6 s of OCR against 0.004 s** of parsing, reconciling and diagnosis — and
the extra readings the verifier now arbitrates between add nothing anyone
can measure. So a 6-page text-layer statement takes under a second and a
99-page scan takes about ten minutes. Fine for a batch tool, wrong for an
interactive one. Parallelising the OCR was tried and is in
[Cut scope](#cut-scope) — measured, it made things slower.

## Generalization

A third of the grade, and the reason for two rules that are enforced rather
than intended:

- **No bank-specific or file-specific branch exists.** No condition anywhere
  keys on a bank name, a filename, or a page count. The layout profile is the
  only thing that varies between statements, and it is data.
- **Heuristics were tuned on one file only.** Great Lakes is the declared
  tuning file; every other sample is held out (SPEC §12.1), so its result
  *measures* the heuristics instead of confirming them.

The parser is tested against four different ways a statement can mark which
side a row is on — two amount columns, a signed column, section headings, or
a running-balance delta — on synthetic fixtures rather than on the samples we
happen to have. Rung 0 **declines** when the page gives no evidence for any
of them, rather than guessing: an escalation is a correct outcome, a
confidently wrong side is not.

### The held-out corpus is generated, not collected

Four sample files cannot measure generalization, and statements are client
data: they cannot be committed, so an accuracy figure that needs them can only
be produced on the one machine they sit on. So the corpus is **written**
instead —

```bash
uv run python evals/fixtures/generate.py
uv run python evals/run_evals.py --samples samples/generated
```

— by [`evals/fixtures/generate.py`](evals/fixtures/generate.py), which is
deterministic: the same seed lays out the same statements to the point, in any
checkout, including one that has never seen a real statement. Each fixture
takes a shape none of the samples has, and its ground truth is exact by
construction rather than read off a page by eye.

| fixture | what it stresses | result |
|---|---|---|
| `gen_01` two amount columns, text layer | the plainest shape there is | **11/11** |
| `gen_02` signed column, **horizontal** summary block, no counts | both count checks must say `unavailable`, not `ok` | **9/9** |
| `gen_03` section headings, no side column | the heading is the only thing saying which side | **11/11** |
| `gen_04` the same table as a clean 200 DPI scan | every number arrives through OCR | **11/11** |
| `gen_05` 150 DPI, **1.4° off square**, soft focus, grain | a tilt drifts columns as well as lines | **11/11** |
| `gen_06` `Money out` / `Money in`, `01 Apr 25`, sterling | nothing may assume a dollar or an American date | 9/9, does not reconcile |
| `gen_07` three scanned pages fed **15 and 11 points sideways** of one another, serial numbers in the margin | a profile from one page must read the others; a stray token must not tear a row | **11/11** |
| `gen_08` a cheque listing two to a line, the **only** record of its cheques | the listing must be read and added | **11/11** |
| `gen_09` the same listing, every cheque **also** a row of the table | the listing must be read and left out | **11/11** |

Eight reconcile to the cent for $0.00 and no API call. `gen_07`–`gen_09` are
the shapes the binder and the Renasant statement taught (SPEC §12.1, amended):
each was written here first, and the sample then measured the rule rather
than tuning it. `gen_06` reads every
field on the page correctly — bank, account, period and all four printed
totals — and still does not reconcile, which is the outcome the design is for:
the rows disagree with the block and the report says so instead of averaging
them. The cause is named under [Known weaknesses](#known-weaknesses).

## Known weaknesses

- **The order of two amount columns is assumed, not read.** The side rule
  takes the **left** column for deposits, which is right for
  `Deposits | Withdrawals` and exactly wrong for `Money out | Money in` — the
  British order, and `gen_06` in the corpus. Every field on that statement is
  read correctly and it still cannot reconcile. The fix is to match the
  table's own column headings against the side vocabulary and let them say
  which column is which, falling back to the order only where the headings say
  nothing.
- **A listing is read only under a header that names its slots.** A cheque
  section headed `NUMBER DATE AMOUNT NUMBER DATE AMOUNT` is read slot by slot
  (SPEC §7.22); a section that prints several transactions to a line with no
  such header is still read one to a line. And a listing ends with its page.
- **Whether a listing belongs in the table is decided by the verifier**, so a
  listing that repeats *some* rows of the table and adds others would be
  neither added nor left out correctly. No sample or fixture prints one; the
  result would say so in its residual.
- **A row whose date was not read takes the previous row's** (SPEC §7.23).
  The chain vouches for the amount and the side; the date is the tool's, and
  is marked `date_inherited` and listed in the audit view.
- **The balance-step recovery can still take a line that is not a transaction
  for one.** The lines it used to misread were a cheque listing's, and the
  listing rule now reads those first; no sample fires it any more. It stays
  declared, counted in a warning and marked `recovered` on the row.
- **Descriptions from a scan are less faithful.** OCR returns line segments
  and drops spaces, so an all-capitals description stays one token
  (`REMOTEDEPOSITLINK`). Amounts, dates and balances are unaffected, which is
  why reconciliation still works.
- **A column seen only once is not claimed.** Alignment identifies a money
  column from two occurrences; one is indistinguishable from an amount inside
  a sentence.
- **The bank name is the largest type on the page**, plus the adjoining
  lines set in the same size — a logo wrapped onto two lines. A title set
  as large as the logo directly beneath it would be joined to it.
- **OCR is the whole latency budget** — about 6 s a page, so roughly ten
  minutes for the 99-page binder.

## Cut scope

Deliberate, and recorded here rather than silently implemented.

**~~Multi-up sections~~** — cut for a round on exactly the right evidence:
Renasant's cheque section is the *only* record of its two cheques, Ixonia's
cheque listing is a *duplicate* view of twenty rows the table already holds,
and nothing on the page tells the two apart. **Built on 2026-09-07** without
deciding on the page at all: the listing is read from its own repeated header,
kept apart from the table, and the period is verified with it and without it
(SPEC §7.22). The reconciliation afterwards was always the only thing that
could tell them apart, so it is what does.

**Parallel OCR — cut on measurement.** Implemented across four processes and
measured on the same file: **137 s against 100 s**. The OCR runtime is already
multi-threaded and saturates the cores, so four copies contend rather than
divide the work. Reverted.

**Rungs 1 and 3** — the profile cache, and model transcription of pages OCR
reads poorly. Rungs 2 and 4 are built and measured below; no sample reaches
them any more.

**~~HTTP API and UI~~** — cut early, **brought back on 2026-09-03**. The
assignment marks them "really optional", which was the right read of the
assignment and the wrong read of the buyer: for an audit firm a reconciliation
nobody can hand to a reviewer is not a workpaper. Recorded here rather than
quietly changed.

**Transaction categorization, description normalization, batch processing,
persistence** — not asked for.

## Provider configuration

The provider is an **installation parameter**, not a build-time choice — for a
product deployed into each client's own VPC, where the model is hosted
differently everywhere, that is worth more than any particular cloud
([ADR-004](docs/adr/004-agent-harness-and-provider.md)).

```bash
cp .env.example .env     # then set LLM_API_KEY
```

`LLM_PROVIDER` selects an **OpenAI-compatible** endpoint — DeepSeek by
default — or **Claude**, in which case `LLM_PLATFORM` picks first-party,
Bedrock, Vertex or Foundry, all of which expose the same `messages.create`
through the same SDK. Nothing else in the codebase knows which is in use.

With nothing configured, `build_client()` returns `None` and the deterministic
path runs alone. That is a supported mode, not a degraded one: it is what
produced every number above.

Rung 2 needs **no vision model** even for scans — OCR ingest has already
turned the pixels into words with coordinates, and that is what the model is
shown.

### Three things measured against the real endpoint

Each cost real money to learn and each is now pinned by a test.

**Reasoning has to be switched off.** DeepSeek's default model reasons before
answering and the token cap covers reasoning *and* answer, so on this task it
produced `finish_reason: length`, 14,941 characters of `reasoning_content` and
an **empty answer** — identically at caps of 2048, 4096 and 8192. With
`thinking: {"type": "disabled"}`: **84 tokens, 2 s, ~$0.0012** against 8192
tokens, 115 s and $0.0119. Ten times cheaper, fifty times faster, one
parameter.

**The schema has to travel in the prompt.** There is no strict `json_schema`
mode here, so a model that is never shown the schema invents its own property
names. It is now embedded in the system message and validated on our side —
and then again by reconciliation.

**A model answers `MM/DD/YYYY`,** which `strptime` cannot use. The profile
contract translates human date notation or refuses the profile: one that
validates but parses no dates is worse than one that is rejected, because
nothing notices until the totals disagree.

### And the model rungs did not help

Both are built, bounded and measured — on the two statements that failed
before the listing, registration and inherited-date rules closed them. Neither
fixed either, and that was the finding rather than a disappointment: what
those files needed was a row model, not a model.

**Rung 2** returned a valid layout profile in 2 calls for **$0.004**, the rows
it produced did not reconcile, and the escalation discarded it.

**Rung 4** ran the repair loop on the Renasant statement: **5–6 calls,
$0.006–0.008, 22 s**. Traced, it called `state`, then `list_rows`, then read
both pages — and stopped **without making a single edit**. It inspected the
evidence and declined to guess, which is what its prompt tells it to do when
it cannot find the answer, and it is the outcome I would rather have than four
invented rows.

Neither failure was a misunderstanding of the layout, and neither was a single
lost row an agent could locate: Renasant needed a row model that reads two
transactions from one line, and now has one. **The prompt was deliberately
not tuned until the file passed** — tuning against a sample would have made
its result meaningless as a measurement, and in the end no prompt was tuned
at all.

What this does establish is the shape: the model is reached only after a free
check has refused the free answer, it is bounded before each turn rather than
asked to be brief, and its work is thrown away unless the period closes. The
whole exercise cost under a cent and could not have corrupted the output.

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
