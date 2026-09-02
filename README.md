# Statement Tie-Out

Extracts account identity, period totals and transactions from a bank
statement PDF — and **reports whether the result reconciles**, per period,
with the residual when it does not.

Start with **[SPEC.md](SPEC.md)**: it was written before the code and is what
the eval harness asserts against. Decisions are in [docs/adr/](docs/adr/);
what the agent got wrong along the way is in
[docs/ai/AI_NOTES.md](docs/ai/AI_NOTES.md).

> Work in progress — accuracy tables in this README are filled from measured
> eval-harness runs, never from estimates. Sections that have no measurement
> yet say so.

## Samples

Statement PDFs are **not committed** (`samples/` is gitignored). Put the
assignment's files there to run the eval harness.
