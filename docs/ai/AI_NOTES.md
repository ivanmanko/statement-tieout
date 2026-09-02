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
