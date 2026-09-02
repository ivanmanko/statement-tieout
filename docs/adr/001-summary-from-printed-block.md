# ADR-001: The summary comes from the printed block, not from the transactions

- **Status:** Accepted
- **Date:** 2026-09-02

## Context

The assignment grades `summary` on **exact match** and separately requires
that transactions "be reconciled with totals". Those are two different
sentences, and reading them as one is the trap: if `summary` is computed by
summing the parsed transactions, then a summary that matches and a set of
transactions that reconciles are the same event, and the reconciliation
check proves nothing at all.

Almost every US statement prints its own summary block — beginning balance,
period totals, ending balance, sometimes counts. It is small, sits on the
first page of a period, and survives a scan far better than a 300-row table.

## Decision

`summary` is **read from the printed block** (SPEC §7.5). The aggregate over
parsed transactions is computed **separately** and never merged into it.
Reconciliation (SPEC §5) compares the two.

When a statement prints no counts — the tuning file does exactly this — the
count fields are derived from the transactions, the two count checks report
`unavailable`, and the result records which fields were printed and which
were derived (SPEC §7.8). Derived numbers are never presented as printed.

When a statement prints no summary block at all, the summary is derived
wholesale, all six checks are `unavailable`, and `reconciled` is `false`
with reason `no_printed_summary` — not `true` for lack of evidence.

## Alternatives considered

1. **Compute the summary from the transactions.** Rejected: it collapses the
   two independent signals into one and forfeits the only free oracle the
   assignment hands us. It also fails exact-match whenever the bank's own
   totals include something our row parser did not classify as a transaction.
2. **Compute it, then "correct" it against the printed block.** Rejected:
   this is the same collapse with an audit trail, and it makes the failure
   mode silent — the number gets fixed while the parser stays broken.
3. **Print both and let the caller choose.** Rejected: the assignment
   specifies one `summary` shape. Both views *are* exposed, but as
   `summary` (printed) and `reconciliation` (the comparison), which is the
   information without the ambiguity.

## Consequences

- Exact-match scoring depends on reading a small block correctly, which is
  tractable even on a scan — the highest-value, lowest-risk target.
- Reconciliation becomes a genuine test of the row parser, because the two
  sides come from different places on the page.
- A bank that prints a wrong or unconventional total (fees excluded from
  "withdrawals", say) will now show as a reconciliation failure. That is
  correct behavior — it is a real discrepancy — and the diagnosis names it.

## Revisit when

A statement is found whose printed block is systematically inconsistent with
its own transactions in a way the diagnosis cannot name. At that point the
question becomes which view the grader wants, and it must be asked, not
guessed.
