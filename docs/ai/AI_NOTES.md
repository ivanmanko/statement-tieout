# AI usage notes

This project was built with Claude Code. Kept from the first session onward,
not reconstructed at the end: what was planned, what the data contradicted,
and what had to be rewritten.

## Hypotheses the data killed

**"53.8 MB means a scan; the small files have text."** Wrong, and in the
direction that mattered. Diagnosis (`scripts/diagnose.py`) on the two files
available at the time:

| file | size | pages | text layer |
|---|---|---|---|
| Great Lakes Commerce Bank - x4071 - 2025-01 | 13.7 KiB | 6 | full — 17,177 chars, 588 amount tokens |
| S2.6.1.1-2 Account 6426 01.31.2025 | 565 KiB | 4 | **none — 0 characters on all 4 pages** |

So the scan is the 565 KB file, not the 53.8 MB one. Size predicts nothing;
the character count per page does. This moved the vision rung from "build if
needed" to "needed for half the samples I have".

**"The printed summary block carries transaction counts."** It does not, at
least not here. The tuning file prints four numbers — beginning, deposits and
credits, withdrawals and debits, ending — and no counts. Two of the five
reconciliation checks (D and E) are therefore `unavailable` on it, and the
counts in the output are derived. That is why checks are tri-state in
SPEC §5 rather than boolean: a check that silently passes because its input
was missing would be the same defect as an undeclared heuristic.

## What the tuning file actually looks like

One period, `Date | Description | Amount | Balance`, a single **signed**
amount column, and a running-balance column with an unbroken chain across all
292 rows. Deposits sum to the printed total exactly; so do withdrawals; the
balance equation closes to the cent. It is the easy case by construction, and
it is the only file heuristics are tuned on (SPEC §10.1).

## Corrections the tests forced

**The reconciliation rule as first written was too weak.** SPEC §5 originally
said a period reconciles when "no check fails and at least A or (B and C) is
ok", where A was the printed block's own arithmetic. Writing the tests
exposed that A alone can be `ok` on a period where **zero transactions were
parsed** — the block agrees with itself, nothing was compared to the rows,
and the result would report `reconciled: true`. That is exactly the failure
mode tri-state checks exist to prevent, reintroduced one level up. The rule
now requires evidence *about the rows*, and the check set grew from five to
six so that the balance equation against parsed rows is separate from the
block's self-consistency — the latter is the only check available on the many
statements that print opening and closing balances and nothing else.

**Three diagnosis fixtures were ambiguous and I had written them wrong.**
With counts unavailable, a residual of −200 against rows of 100, 200 and 50
satisfies both "twice a row of 100" and "equals a row of 200". My tests
asserted one and my SPEC ordered the other. The fixtures were the thing at
fault, not the ordering: they were not testing what they claimed to. Fixed by
constructing residuals that only one signature can explain, which is also the
honest statement of what the code can do — with no counts printed, some
residuals are genuinely ambiguous and are reported as such.

**A test that would have enshrined a bug.** The date-format test originally
asserted that `01/28` parsed under `%m/%d` yields `date(1900, 1, 28)`, which
is just `strptime`'s default leaking into the contract. Real statements print
yearless dates constantly. Replaced with year inference from the statement
period, including the December-to-January rollover, and declared in SPEC
§7.12. The same change removed 4,108 deprecation warnings, because parsing
against a fixed leap year is also what CPython is asking for.

**pdfplumber splits words on whitespace, so a date can be three of them.**
`Jan 28, 2025` arrives as `Jan`, `28,`, `2025`. The first row parser looked at
single words and silently found no date, which would have produced zero
transactions on any statement using a written-out month — and zero
transactions still "reconciles" against a summary derived from zero rows if
the evidence rule is weak, which is the second reason that rule was
tightened.

## What is not measured

No cost or latency number exists for the model-backed rungs, because they are
not built. Every number in the README comes from a harness run; there are no
estimates in it, and the sections that have no measurement say so rather than
quoting a target as if it had been met.

## What the third and fourth statements changed

The samples arrived one at a time, and each one falsified something that had
looked settled. That is the useful record here, so it is kept in order.

**Two of three files are scans, and not the ones I expected.** The 53.8 MB
file was the obvious candidate; the actual scans are 565 KB and 6.2 MB, while
the 13.7 KB file is digital. Size predicts nothing. See ADR-005 — this is
what moved OCR out of the ladder and into ingest.

**The share threshold for money columns was wrong, and measurement said so
before any test did.** SPEC §7.21 originally discarded a money cluster
appearing on under 30% of rows, as a way of rejecting amounts embedded in
descriptions. Fulton's statement is mostly checks: its deposits column sits on
16% of rows. Printing the actual clusters settled it —

| cluster | share | right-edge spread |
|---|---|---|
| deposits | 16.1% | 0.25 |
| debits | 83.2% | 0.33 |
| balance | 100% | 0.31 |

Frequency separates nothing; **alignment separates everything**. Amounts in a
table are aligned by typesetting, an amount inside a sentence is wherever the
sentence put it. The rule is now edge alignment plus a floor of two rows, and
a column seen only *once* is left unclaimed, because at n=1 neither signal can
tell it from prose.

**A test I wrote asserted the wrong thing, twice.** The lopsided-column
fixture had a single deposit row, which the honest rule cannot recover; I
changed the fixture rather than weaken the rule. And three residual-diagnosis
fixtures were ambiguous — a residual of −200 against rows of 100, 200 and 50
fits both "twice a row of 100" and "equals a row of 200" — so they were not
testing what they claimed. Both are cases of the test being wrong, which is
worth more attention than it usually gets: a green suite around a bad fixture
is a worse position than a red one.

**Four rules that looked obviously right, each broken by one real page:**

- the summary block is a *vertical* label-and-amount line — Fulton prints a
  row of labels above a row of amounts, matched by column;
- the letterhead is a *line* — Fulton sets `Fulton Bank` at 18.4 pt beside
  `Lancaster, PA 17604` at 10.4 pt on the *same* line, so the unit is the
  word;
- a masked account is `x` followed by four digits — `P.O.Box 4887` ends in
  `x`, and OCR of the same line on another page gives `P.O.B 0 x 4887`;
- a differing statement period means a new period — a continuation page
  prints only the end date, which split one statement into three.

Each was found by running the thing, not by reasoning about it.

**Raising the OCR resolution does not fix OCR errors.** Renasant's
`32,537.69` reads as `32/537.69` at render scales 3, 4 and 5 alike. Measuring
that took two minutes and replaced a plausible fix with a correct one: a
single declared token repair, safe because a wrong repair still has to pass
reconciliation.

**Reading a scan is worth more than reporting that you cannot.** Before
ADR-005 this project returned nothing at all for two of three files and said
so honestly. Honest and nearly worthless. It now reads 284 transactions off
fifteen scanned pages and reconciles them to the cent, for $0.00.

## What is still wrong, and known

The Renasant statement prints **two transactions per line** in its `CHECKS`
section. The row model reads one, so four of ten rows are missed and the
period does not reconcile — reported with its residual rather than smoothed
over. This is the next thing to build, and notably it is *not* a model
problem: it is a change to the row model.

## What is not measured

Rung 2 — the model-derived layout profile — is built and unit-tested against
a stub client, but no sample has failed in a way that triggers it, so there is
no real run to quote a cost or a latency from. Every number in the README came
from a harness run with no API key configured at all. There are no estimates
in it, and the sections with no measurement say so rather than quoting a
target as though it had been met.

## The binder, and what a 99-page scan costs to learn

The last file was 99 scanned pages holding eleven Ixonia Bank statements with
redacted descriptions. It is the only sample that exercises period
segmentation, the first that **prints transaction counts** — `+Deposits and
Credits (81)` — and by a distance the hardest thing here.

It also killed six more assumptions, in the order they surfaced.

**A single bad row killed the whole file.** One row carried a zero amount, the
output contract requires a strictly positive side, and the resulting
`ValidationError` aborted a six-minute run. Two rules came out of it: a
zero-amount row is not a transaction (SPEC §7.13), and a row the contract
rejects for any other reason costs that row and not the document (§7.15). A
project whose entire thesis is "a diagnosable partial result beats silence"
had been returning silence.

**The transaction table's own header was read as a summary block.** `Date
Description Deposits Withdrawals Balance` carries two summary labels and no
money, so the horizontal-block reader treated it as a label row and the first
transaction as its totals — `deposits_total` came back as 1,611.95 instead of
1,214,254.05. A *wrong* summary is worse than a missing one, and only a
guard on the value row (it must not open with a date) and on the search scope
(header only) closed it.

**Segmentation split the file into 26 periods.** The beginning-balance anchor
fires exactly eleven times, once per statement, which is right. What ruined it
was the account-number anchor: OCR reads a four-digit number off a scanned
cheque image, and out of a description reading `TRNSFR TO CHECKING ACCT ENDING
IN 4623`. Anchors are now ranked — when a beginning-balance line exists
anywhere in the document, the weaker anchors are ignored (§7.3).

**Pooling every page's amounts destroyed the column statistics.** A binder
holds cheque images whose money sits nowhere near the statement's columns.
With them in the same clusters, eleven statements yielded *no usable profile
at all*. The profile now comes from the single densest table page and is
applied to the rest (§7.21).

**A daily-balance table was parsed as transactions.** `Apr 01 607,330.75
Apr 11 521,451.70 …` across the page added **2,920,908.79** of deposits that
never happened. The rule that catches it needs no vocabulary: date → amount →
date again is a multi-column summary, while a date *inside* a description has
no amount separating it from the row's own date (§7.14). It then failed on the
real page anyway, because `Apr 11` is two words and I had scanned one word at
a time — the same mistake I had already made and fixed once in the leading-date
parser.

Residual on the first statement across those fixes: **2,852,036.58 →
2,852,036.58 → −68,872.21**, with 186 of 192 rows found. It still does not
reconcile.

## What the model rung actually cost, measured

DeepSeek, `deepseek-v4-flash`, ~$0.05 all in for everything below.

**It did not work at all at first, and the reason was not the prompt.** Three
calls, $0.011, and an empty answer every time. The model is a *reasoning*
model: `finish_reason: length`, `reasoning_content` 14,941 characters,
`content` empty. `max_tokens` covers reasoning and answer together, so raising
the cap only bought more reasoning — at 2048, 4096 and 8192 alike. With
`thinking: {"type": "disabled"}`: **84 tokens, 2 seconds, ~$0.0012**. Ten
times cheaper and fifty times faster, from one parameter.

**The schema was never being sent.** DeepSeek has no strict `json_schema`
mode, and my client passed only `json_object` — so the model invented its own
property names (`columns: [...]`). The schema now travels in the prompt, as
this repository's sibling project already knew and I had not carried over.

**And it answered `MM/DD/YYYY`,** which `strptime` cannot use. A profile that
validates but parses no dates is worse than a rejected one, so the contract
now translates human notation or refuses the profile.

**Then it worked, and did not help.** On the Renasant statement and on the
binder's first period the model returned a valid profile in 2 calls for
$0.004, the rows it produced did not reconcile, and the escalation **discarded
it** — exactly as designed. That is the honest measured answer to "does the
model rung earn its keep here": no, because neither failure is a
misunderstanding of the layout. Finding that out cost less than half a cent
and could not corrupt the result, which is the entire argument for putting a
free verifier in front of a model instead of behind it.

**What I would not have learned any other way:** every one of these six is a
thing the code did wrong while looking right, and every one was found by
running it against a file I had not seen. None came from review.

## Rung 4, and the temptation not to take

The repair loop is the piece the assignment names outright ("build an Agent"),
and the only place in this project where an agentic loop is defensible: the
verifier is free, deterministic and automatic, so every tool answers with the
new verdict and the model is *told* whether its last move helped instead of
being asked to judge its own work.

**The ADR was wrong and had to be amended.** ADR-004 specified the Anthropic
SDK's `tool_runner`, chosen partly for its per-turn hooks. That was written
before the configured provider was DeepSeek, and a loop tied to one vendor's
SDK cannot serve a provider selected by an env var — which is the whole claim
the same ADR makes two paragraphs later. The loop now keeps a
provider-neutral transcript and each client renders it into its own wire
format. The ceilings the hooks were supposed to justify turned out to be two
comparisons at the top of the loop.

**Measured, it does not fix anything.** On the Renasant statement: 5–6 calls,
$0.006–0.008, 22 seconds. Traced, it called `state`, then `list_rows`, then
read both pages — and stopped **without making a single edit**. It inspected
the evidence and declined to guess.

**And here is the temptation I did not take.** I could have kept editing the
system prompt until that file passed. It is a *held-out* sample (SPEC §10.1).
Tuning against it would have bought a number in the README and destroyed the
only thing that number was worth: evidence about statements nobody has tuned
for. The whole reason to declare a tuning file on day one is to have something
to refuse on day two.

What the exercise does establish is the shape, and the shape is the part that
generalizes: the model is reached only after a free check has refused the free
answer; it is bounded before each turn rather than asked to be brief; and its
work is discarded wholesale unless the period closes. It cost under a cent and
could not have corrupted the output — which is the argument for putting the
verifier in front of the model rather than behind it, made in numbers instead
of prose.
