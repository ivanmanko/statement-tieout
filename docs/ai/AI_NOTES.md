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
§7.11. The same change removed 4,108 deprecation warnings, because parsing
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
before any test did.** SPEC §7.19 originally discarded a money cluster
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
