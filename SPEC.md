# SPEC — Bank Statement Extraction

Behavior specification, written **before implementation**. This document is the
contract the eval harness asserts against. Every heuristic, threshold and
assumption the extractor relies on is declared in §7 — if observed behavior
deviates from this spec, either the code or this document has a bug, and the
fix is explicit. There is no hidden hardcoded behavior, and in particular
**no rule keyed to a specific bank or a specific sample file** (§7.1).

## 1. Purpose and scope

Extract, from one bank-statement PDF, the account identity, the printed
period totals, and every transaction — and **report whether the result
reconciles**, per period, with the residual when it does not.

**In scope:** `extract(pdf_path: str) -> dict`; a CLI around it; multi-period
statements (several statements concatenated in one file); a reconciliation
report; an eval harness.

**Out of scope** (cut consciously, recorded in README): HTTP API and UI,
transaction categorization, description normalization, batch directory
processing, persistence, non-PDF inputs.

## 2. Input

An arbitrary US-style bank statement PDF. Two structural cases, distinguished
at runtime (§7.2), never by filename:

- **text-layer PDF** — characters and their coordinates are recoverable;
- **scan** — no text layer; pixels only.

The extractor must not assume a known bank, a known template, or a page count.

## 3. Output contract

The Pydantic model `ExtractResult` in `schema.py` is the single source of
truth for this shape. Nothing else may define or duplicate these fields.

```jsonc
{
  "account": { "bank": "string|null", "account_last4": "string|null",
               "period": { "start": "YYYY-MM-DD|null", "end": "YYYY-MM-DD|null" } },
  "summary": { "beginning_balance": 0.00, "ending_balance": 0.00,
               "deposits_total": 0.00, "deposits_count": 0,
               "withdrawals_total": 0.00, "withdrawals_count": 0 },
  "transactions": [ { "date": "YYYY-MM-DD", "description": "string",
                      "deposit": 0.00, "withdrawal": null } ],
  "periods": [ { "account": {...}, "summary": {...},
                 "transactions": [...], "reconciliation": {...} } ],
  "reconciliation": {...},
  "extraction": { "path": "deterministic|llm_profile|vision",
                  "llm_calls": 0, "cost_usd": 0.0, "latency_s": 0.0,
                  "warnings": ["string"] }
}
```

**Invariants** (asserted by the eval harness on every result):

1. Exactly one of `deposit` / `withdrawal` is non-null on every transaction,
   and the non-null one is `> 0`. Sign lives in the field choice, never in
   the value.
2. `len(periods) >= 1`. With exactly one period, top-level
   `account` / `summary` / `transactions` **are** that period's, so the
   assignment's single-period example holds unchanged.
3. With several periods, top-level `summary` is the aggregate:
   `beginning_balance` of the first period, `ending_balance` of the last,
   totals and counts summed; `transactions` is the concatenation in document
   order; `account.period` spans first start to last end. Top-level
   `reconciliation.reconciled` is the AND over periods, each
   `checks[x]` is the **worst** state across periods
   (`fail` > `unavailable` > `ok`), and `residual` is the sum of the period
   residuals. Per-period diagnoses stay in `periods[]` — the aggregate names
   no single cause, because it may have several.
4. `summary.deposits_count == len([t for t in transactions if t.deposit])`
   and likewise for withdrawals — **within a period**. This is the
   `deposits_count` / `withdrawals_count` check (§5) and it is an assertion,
   not a coincidence: when the statement prints
   counts they must agree, and when it does not (§7.8) the field is derived
   from the transactions and therefore holds by construction.
5. Every `date` lies within `[period.start, period.end]`, or the transaction
   carries a warning (§7.11).
6. Money is serialized with exactly 2 decimal places.

`null` in any field means **unknown**, never zero and never "matches
anything".

## 4. Extraction pipeline (normative)

Stages run in order. The pipeline is an **escalation ladder**: each rung
produces a candidate result, the same free verifier (§5) accepts or rejects
it, and we climb only on rejection.

1. **Ingest.** Open the PDF, extract words with coordinates per page.
   Classify each page as text-bearing or scanned (§7.2).
2. **Period segmentation.** Split pages into periods using the anchors in
   §7.3, *before* any row parsing. A file with no anchor is one period.
3. **Rung 0 — heuristic layout profile.** Derive a `LayoutProfile` (§7.4)
   from word coordinates alone: no model, no network, $0.
4. **Rung 1 — cached profile.** If this template fingerprint (§7.7) was seen
   before, load its stored profile instead of deriving one. Still $0.
5. **Parse.** Read the printed summary block (§7.5) and parse transaction
   rows deterministically under the profile (§7.6). No model in this stage,
   on any rung.
6. **Reconcile** (§5). If the period reconciles, stop — this is the answer.
7. **Rung 2 — LLM layout profile.** On failure, send **1–2 sample pages**
   (never the whole table, §7.13) to the model and ask for a `LayoutProfile`
   under a strict schema. Re-parse, re-reconcile. At most
   `max_profile_attempts` (§7.14) attempts, each fed the previous residual.
8. **Rung 3 — vision transcription** (scanned pages only): transcribe
   **one page at a time**, each page verified locally (§7.13).
9. **Rung 4 — agentic repair** (bounded): out of scope for this delivery;
   the interface exists and the README says so.
10. **Assembly.** Emit the result plus one structured JSON log line (§8).
    A period that never reconciled is still returned, with its residual and
    diagnosis — **a failure is reported, never hidden or silently repaired.**

## 5. Reconciliation (normative)

Two independently obtained views of the same period — the **printed summary
block** and the **parsed transactions** — are compared. Six checks, named in
the output rather than lettered:

| id | check | what it evidences |
|---|---|---|
| `printed_block_closes` | `beginning + deposits_total − withdrawals_total == ending`, all printed | the block is internally consistent |
| `balance_equation` | `beginning + Σparsed_deposits − Σparsed_withdrawals == ending` | **the parsed rows close the printed balances** |
| `deposits_total` | `Σ parsed deposits == printed deposits_total` | the deposit rows are complete and correct |
| `withdrawals_total` | `Σ parsed withdrawals == printed withdrawals_total` | likewise for withdrawals |
| `deposits_count` | `count parsed deposits == printed deposits_count` | no deposit row lost or doubled |
| `withdrawals_count` | `count parsed withdrawals == printed withdrawals_count` | likewise |

Each check is **tri-state**: `ok`, `fail`, or `unavailable`. `unavailable`
means the statement did not print the input the check needs (§7.8) — it is
never reported as `ok`. The checks that passed are reported, so "reconciled"
can never mean "we had nothing to check".

**A period is `reconciled` when no check is `fail` and either
`balance_equation` is `ok`, or `deposits_total` and `withdrawals_total` are
both `ok`.** Those are the two ways the parsed rows can be evidenced against
the page. `printed_block_closes` is deliberately **not** sufficient on its
own: it compares the block with itself and says nothing about whether the
rows were read correctly — a period where it is the only `ok` check reports
`no_transaction_evidence` and is not reconciled.

Note the redundancy this creates on purpose: when the block prints totals,
`balance_equation` follows arithmetically from the other checks and adds
nothing. Its value is on the many statements that print only opening and
closing balances, where it is the *only* check available against the rows.

**Tolerance is exactly zero.** All money is `Decimal`; a one-cent residual is
a failure, not a rounding artifact (§7.9).

**Residual** = `beginning + Σparsed_deposits − Σparsed_withdrawals − ending`.
It is `0.00` exactly when `balance_equation` is `ok`.

### 5.1 Residual diagnosis

The residual is an address, not just a verdict. Writing `r` for the residual
and `Δdep` / `Δwd` for parsed count minus printed count, the arithmetic of a
single-row error is forced:

| what went wrong | Δdep | Δwd | residual |
|---|---|---|---|
| a deposit of `X` was not parsed | −1 | 0 | `−X` |
| a withdrawal of `X` was not parsed | 0 | −1 | `+X` |
| a deposit of `X` was parsed twice | +1 | 0 | `+X` |
| a withdrawal of `X` was parsed twice | 0 | +1 | `−X` |
| a deposit of `X` landed on the withdrawal side | −1 | +1 | `−2X` |
| a withdrawal of `X` landed on the deposit side | +1 | −1 | `+2X` |

So when counts are printed, one failing row is **identified**, not guessed:
its side, its amount, and what happened to it all follow from two integers
and one Decimal.

Signatures are applied in this order; the first match wins:

1. `r == 0` and counts are short — `zero_amount_rows`; rows with no amount
   were skipped. Benign.
2. A running-balance column is present and its chain first breaks at row *i*
   (§7.10) — `row_level_break`, reported with *i* and its page. This
   outranks the count arithmetic because it localizes to a row rather than
   to a period.
3. The count deltas match a row of the table above — `dropped_row`,
   `duplicated_row` or `side_flip`, with the exact amount. For
   `dropped_row`, the amount is additionally searched for in the raw page
   text and the page reported when found.
4. Counts are `unavailable` (§7.8), so only `r` is known. Then, in order:
   `abs(r) == 2 × amount` of some parsed row — `side_flip` *candidate*, that
   row named; `abs(r) ==` the amount of some parsed row — ambiguous between
   `duplicated_row` and a dropped row of equal value, reported as
   `amount_matches_row` with the row named; `abs(r)` occurs as a money token
   in the raw page text but among no parsed row — `dropped_row` candidate
   with the page.
5. Otherwise `unknown`, and the residual is reported bare. More than one row
   is wrong, and separating them is what the repair rung (§4 rung 4) exists
   for.

A diagnosis is **reported, never auto-applied**, in this delivery. Turning a
diagnosis into an edit is rung 4, which is out of scope (§1). The distinction
matters: a wrong diagnosis that is only printed costs a reviewer a minute,
while a wrong diagnosis that silently rewrites a transaction is a corrupted
result that still reconciles.

## 6. Edge cases (normative)

| # | input | required behavior |
|---|---|---|
| 1 | Encrypted or unopenable PDF | raise `ExtractionError`; never return a half-filled result |
| 2 | Scanned page, vision disabled or unavailable | period returned with `transactions: []`, summary if readable, `reconciliation` `fail`/`unavailable`, warning naming the pages |
| 3 | Statement prints no transaction counts | the two count checks `unavailable`; counts derived from transactions (§7.8) |
| 4 | Statement prints no summary block | all six checks `unavailable`; summary derived from transactions; `reconciled: false` with reason `no_printed_summary` |
| 5 | Several statements concatenated | one entry per period in `periods[]`, each reconciled separately |
| 6 | Transaction table continues across a page break | rows joined without duplicating any; §7.10 detects duplication |
| 7 | A row whose description wraps to a second line | joined into one transaction (§7.6) |
| 8 | Period with zero transactions | valid; reconciles iff `beginning == ending` |
| 9 | Date outside the stated period | kept, flagged with a warning (§7.11) |

## 7. Declared heuristics and assumptions

Everything a developer would otherwise decide silently in code.

1. **No file-specific or bank-specific behavior.** No branch may key on a
   bank name, a filename, or a page count. Heuristics are tuned on the
   declared tuning file only (§10.1); every other sample is held out.
2. **Text layer vs scan:** a page is *scanned* when it yields fewer than
   `min_chars_per_text_page = 20` characters. A document is scanned when
   more than half its pages are.
3. **Period anchors**, matched case-insensitively, in priority order:
   a line matching `beginning|previous balance`; a change in the account
   number found on the page; a `statement period` / `statement date` line
   whose parsed dates differ from the current period's. A new period starts
   at the page where an anchor fires. A file with no anchor is one period
   spanning all pages.
4. **`LayoutProfile`** is the only thing that varies between statements. It
   declares: the x-ranges of the date, description, amount(s) and balance
   columns; the date format; which **side strategy** applies (§7.6); and the
   summary-block labels found (§7.5). It is data, never code. How rung 0
   derives one without a model is §7.17.
5. **Summary block labels** are matched by normalized substring against a
   declared vocabulary — beginning: `beginning balance`, `previous balance`,
   `opening balance`, `balance forward`; ending: `ending balance`,
   `new balance`, `closing balance`; deposits: `deposits and credits`,
   `total deposits`, `deposits`, `credits`, `additions`; withdrawals:
   `withdrawals and debits`, `total withdrawals`, `withdrawals`, `debits`,
   `subtractions`. Longer labels are tried first, so `deposits and credits`
   is not consumed by `deposits`. The vocabulary lives in one module
   constant and this section mirrors it; extending it requires editing both
   in one commit.

   **Scope:** the block is searched in the lines *preceding* the period's
   first transaction row, which is where every statement seen so far prints
   it; only if a label is not found there is the rest of the period
   searched. This is what keeps a description like `LOCKBOX DEPOSITS` from
   being read as a total.

   **Amount:** the last money-shaped token on the matching line. A candidate
   line that *begins* with a date is a transaction row, not a summary line,
   and is skipped — otherwise a description such as `LOCKBOX DEPOSITS AND
   CREDITS 8,193.03` would be read as a printed total.

   **Counts** are read only from a matching line carrying exactly one money
   token and exactly one bare integer (`Deposits and credits 81
   $1,214,254.05`); anything less clear-cut leaves the count `unavailable`
   rather than guessed. A wrong count would make the reconciliation lie in
   the one direction that matters — reporting a failure where the rows are
   in fact complete.
6. **Side strategy** — how a row becomes a deposit or a withdrawal. The
   profile picks exactly one, in this priority:
   1. `two_columns` — two distinct amount x-ranges: left/right decides;
   2. `signed` — one amount column carrying `-`, `(...)`, trailing `-`,
      or `CR`/`DR` markers: the sign decides;
   3. `sections` — section headings partition the rows (e.g. "Deposits and
      Additions", "Checks Paid", "Other Withdrawals"): the heading decides;
   4. `balance_delta` — a running-balance column: the sign of
      `balance[i] − balance[i−1]` decides.
   When several are available the earliest applicable wins, and the others
   become *verifiers* rather than deciders.
7. **Template fingerprint** (profile cache key): SHA-256 over the normalized
   text of the first page with all digits replaced by `0` — so two
   statements of the same bank and template collide, and two different
   months of the same account collide. Cache is on-disk, opt-in via
   `--cache-dir`, and never consulted for correctness decisions.
8. **Printed counts are frequently absent.** When a statement prints totals
   but no counts (the tuning file does exactly this), `deposits_count` /
   `withdrawals_count` are **derived from the parsed transactions**, checks
   `deposits_count` / `withdrawals_count` report `unavailable`, and the
   result records which summary fields
   were printed vs derived. Derived numbers are never presented as printed.
9. **Money is `Decimal`, parsed from strings, never `float`.** Accepted
   forms: `1,234.56`, `$1,234.56`, `-1,234.56`, `(1,234.56)`, `1,234.56-`,
   `1,234.56 CR`, `1,234.56 DR`. Parentheses, a leading or trailing minus,
   and `DR` mean negative; `CR` means positive. A money token has **exactly
   two decimal places**: `1234` and `1,234.5` are not money. That rule is what
   keeps years, page numbers and account digits out of the amount scan.
   `float` appears only at JSON serialization. Reconciliation tolerance is
   exactly `Decimal("0.00")`.
10. **Running-balance chain** is used as a row-level verifier whenever a
    balance column is present: `balance[i−1] ± amount[i] == balance[i]`. A
    break localizes the error to row *i*. It is a verifier, not a parser —
    a statement without the column loses this check and nothing else.
11. **Dates** are parsed with the profile's declared format. When a
    statement is ambiguous between `MM/DD` and `DD/MM`, the format that
    yields all dates inside the stated period wins; if both do, `MM/DD` is
    assumed (US statements) and a warning is recorded. When the format
    carries **no year** — common on transaction lines — the year comes from
    the period, rolling back one year for a date that would otherwise fall
    before `period.start` (so a December row in a mid-December-to-January
    statement lands in the right year). With no period known, the row keeps
    `strptime`'s default year and a warning is recorded: an obviously wrong
    date is preferable to a plausible wrong one. A transaction date outside
    the period is kept and warned about, never dropped: dropping it would
    break reconciliation silently.
12. **Description** is the text between the date and the first amount
    column, whitespace-normalized. A row whose next line has no date and no
    amount is a wrapped continuation and is appended to the previous
    description.
13. **The model never transcribes what cannot be verified.** Rung 2 sees at
    most `max_sample_pages = 2` pages and returns a *profile*, not rows.
    Rung 3 transcribes at most one page per call, and each page's output
    must satisfy either its running-balance chain or a section subtotal
    before it is accepted.
14. **Bounds:** `max_profile_attempts = 3`; `max_llm_calls_per_statement`
    and `max_cost_usd_per_statement` are config, enforced in the client, and
    exceeding either aborts the ladder and returns the best result so far
    with a warning. Default model `claude-opus-5`, `temperature` unset,
    structured output enforced server-side by re-validating against the
    `LayoutProfile` schema.
15. **Account identity** is read from the first page of the period:
    - **bank** — the first line carrying no money token and no run of four or
      more digits, i.e. the letterhead. A statement whose letterhead is an
      image yields `null`, not a guess.
    - **account_last4** — the last run of exactly four digits on a line
      containing `account`, or the trailing four digits of a masked token
      (`****4071`, `xxxx4071`, `x4071`) anywhere on the page.
    - **period start/end** — the two dates on a line containing
      `statement period`, `statement date`, `for the period` or
      `period covered`. One date alone fills `end` and leaves `start` null.
16. **Provider is an installation parameter.** `LLM_PROVIDER` selects
    `anthropic` (default) / `bedrock` / `vertex` / `foundry`; all four expose
    the same `messages.create`. No code path depends on which is chosen.

17. **Heuristic profile derivation (rung 0)**, from word coordinates alone:
    1. **Candidate rows** are lines whose leading words parse as a date under
       any candidate format (§7.15, plus the yearless formats of §7.11) and
       that carry at least one money token. Fewer than
       `min_candidate_rows = 3` of them means no profile: rung 0 declines
       rather than guessing, and the ladder escalates.
    2. **Date format and column:** the candidate format parsing the most
       leading tokens wins; the column spans those tokens' extent.
    3. **Money columns:** the horizontal midpoints of all money tokens on
       candidate rows are sorted and split wherever the gap exceeds
       `column_gap = 20.0` points. A cluster appearing on fewer than
       `min_column_share = 25%` of candidate rows is discarded as an amount
       embedded in a description.
    4. **Balance column:** the rightmost surviving cluster, *if* it behaves
       like a running balance — `b[i] − b[i−1]` equals ± the row's amount on
       a majority of consecutive rows. Otherwise there is no balance column
       and the cluster is treated as an amount. This is a measurement, not
       an assumption about where banks put things.
    5. **Amount columns:** the remaining clusters, rightmost two if more
       survive.
    6. **Side strategy**, in the §7.6 priority order: two amount columns →
       `two_columns`; any negative or `CR`/`DR`-marked amount → `signed`;
       at least two section headings recognised → `sections`; a balance
       column → `balance_delta`; none of these → no profile, escalate.
       A **section heading** is a line with no date and no money whose text
       matches the §7.5 deposit or withdrawal label vocabulary.

## 8. Observability

One structured JSON log line per `extract()` call: `pdf`, `pages`,
`scanned_pages`, `periods`, per period `{rows, side_strategy, checks,
residual, diagnosis}`, `rung` reached, `llm_calls`, `prompt_tokens`,
`completion_tokens`, `cost_usd`, `latency_ms` by stage, `warnings`. This line
is the debugging story for "this statement came out wrong".

## 9. Non-functional targets

- Text-layer statement, rung 0: **< 5 s** and **$0.00** — no network call.
- Rung 2 adds one LLM call per statement, not per row or per page.
- Memory: pages are processed one at a time; a 50 MB PDF must not be loaded
  as a whole into memory beyond what `pdfplumber` requires per page.
- Targets are restated in the README **as measured**, including misses.

## 10. Acceptance criteria

### 10.1 Tuning vs held-out

**Tuning file (heuristics may be tuned on it):**
`Great Lakes Commerce Bank - x4071 - 2025-01.pdf`.

**Held out (never inspected while tuning a threshold):** every other sample,
including `S2.6.1.1-2 Account 6426`, `Binder2_Redacted`, `April 2021`, and any
public statement used for the generalization check. Their pass rate therefore
*measures* the heuristics instead of confirming them. This split exists
because generalization to unseen statements is a third of the grade; a
parser tuned on all four files would score itself.

### 10.2 Definition of done

`uv run python evals/run_evals.py` runs every file in `evals/expected/`,
validates each result against `ExtractResult` and the §3 invariants, compares
summary fields for **exact** equality, prints a pass/fail table with residuals
and cost, writes a JSON report, and exits non-zero on any failure.

Done means: the tuning file passes fully (all summary fields exact, period
reconciled, the total checks `ok`); every other file either passes or
returns a
**named, quantified** failure — residual, failing checks, diagnosis, and the
page it points at. An unexplained failure is not done; an explained one is a
result.
