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
