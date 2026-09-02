# ADR-003: Decimal everywhere, zero tolerance, tri-state checks

- **Status:** Accepted
- **Date:** 2026-09-02

## Context

Summing ~300 amounts and comparing to a printed total is the core operation
of this service, and it is graded on exact equality. Binary floating point
cannot represent `0.1`; summing hundreds of such values accumulates an error
in the last cents. A reconciliation that "passes within a cent" would be
indistinguishable from a parser that dropped a rounding-sized row.

Separately: statements omit things. The tuning file prints totals but no
transaction counts, so two of the six checks have no input.

## Decision

1. **`Decimal`, constructed from strings, from parse to output.** `float`
   appears only when serializing JSON. Never `Decimal(float)`.
2. **Reconciliation tolerance is exactly `Decimal("0.00")`.** There is no
   epsilon and no configuration knob for one.
3. **Checks are tri-state:** `ok`, `fail`, `unavailable`. A check whose input
   the statement did not print reports `unavailable` and never `ok`, and the
   reconciliation result names which checks carried it. `reconciled: true`
   with every check `unavailable` is unrepresentable.

## Alternatives considered

1. **`float` with a one-cent epsilon.** Rejected: the epsilon hides exactly
   the class of error we are trying to detect, and exact-match grading gives
   no credit for being close.
2. **Integer cents.** Genuinely viable and immune to the same problem.
   Rejected for readability at the boundaries — every parse and every
   serialization would carry a ×100 conversion, and `Decimal` already gives
   exactness without that. Worth revisiting if profiling ever shows
   `Decimal` arithmetic to matter, which at 300 rows it does not.
3. **Boolean checks, skipping the ones without input.** Rejected: a skipped
   check silently becomes a pass, and "reconciled" then means "we had
   nothing to compare". Tri-state makes the absence of evidence visible in
   the output rather than in a comment.

## Consequences

- Every money value crosses module boundaries as `Decimal` or as a string,
  never as `float`; the schema enforces this.
- The output distinguishes "this reconciled" from "we could not check", and
  the eval harness can score those differently.

## Revisit when

Never for the tolerance. For the representation: only if a profile shows
`Decimal` arithmetic on the critical path, in which case integer cents is the
migration, and the tolerance stays zero.
