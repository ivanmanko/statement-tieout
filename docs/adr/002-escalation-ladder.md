# ADR-002: An escalation ladder with a free verifier, not an agent in the hot path

- **Status:** Accepted
- **Date:** 2026-09-02

## Context

The assignment says "build an Agent" and hands over statements with hundreds
of transaction rows. The obvious reading — give the PDF to a model and ask
for the rows — is what the grading is built to catch: a model transcribing
300 rows will drop or duplicate a few, and the balance equation will say so
immediately. Two runs over the same file will also disagree, while `summary`
is graded on exact match.

Meanwhile the same equation is a **free, deterministic, automatic verifier**.
That changes the design question from "how good is the model at reading
tables" to "what is the cheapest producer whose output the verifier accepts".

## Decision

A ladder (SPEC §4). Each rung produces a candidate; the verifier (SPEC §5)
accepts or rejects it; we climb only on rejection.

| rung | producer | cost |
|---|---|---|
| 0 | heuristic layout profile from word coordinates | $0 |
| 1 | cached profile for a known template fingerprint | $0 |
| 2 | LLM profiles the layout from 1–2 **sample pages** | ~1 call / statement |
| 3 | model transcription of pages OCR reads poorly | N calls |
| 4 | agentic repair of a period that still will not close | bounded |

> **Amended by [ADR-005](005-ocr-is-ingest-not-a-rung.md).** Rung 3 was
> originally "vision transcription (scans only)". Reading a scan turned out
> to belong in *ingest*, not on the ladder: OCR produces the same
> words-with-coordinates the text layer does, so every rung above applies to
> scans unchanged. Rung 3 is now the narrower job of transcribing pages OCR
> reads too poorly.

Two properties make this more than a fallback chain. Rungs 0–1 are free, so a
statement the heuristics handle costs literally nothing — not "cheap", zero.
And the model's job on rung 2 is to return a **layout profile**, which is
data describing where the columns are, not the contents of the rows: it sees
a sample, never the whole table (SPEC §7.16).

Rungs 3 and 4 are declared here but out of scope for this delivery (SPEC §1).

## Alternatives considered

1. **One model call per statement, whole PDF in the prompt.** Rejected on
   all three grading axes: rows get dropped, output is not reproducible, and
   the 53.8 MB sample exceeds the API's 32 MB request limit anyway.
2. **Model transcribes rows page by page, everywhere.** Rejected as the
   default — it pays per page for pages the heuristics parse perfectly — but
   *accepted* for scanned pages (rung 3), where there is no alternative. The
   rule is not "never let the model read a table"; it is **never transcribe
   what cannot be verified**: a page is an acceptable unit precisely because
   its running-balance chain or section subtotal can accept or reject it.
3. **Pure regex parser, no model at all.** Rejected: it is the hardcoding the
   generalization criterion is designed to punish. The layout profile is the
   seam that lets an unseen template be handled at runtime.
4. **Agent in the hot path with filesystem tools, deciding everything.**
   Rejected: nondeterministic where exact match is graded, and slow and
   expensive where a coordinate scan is free. The agent earns its place only
   where the verifier has already proven something is wrong (rung 4).
5. **OCR (tesseract) for scanned pages instead of a vision model.** Not
   rejected on principle, deferred: OCR digit confusion (8/3, 5/6, 0/O) is
   exactly the failure the verifier catches, so it is a legitimate rung-3
   producer to measure later. Not built here because rung 3 is out of scope.

## Consequences

- Cost per statement is reported as measured per rung, and the answer for a
  text-layer statement is $0.00.
- The ladder's rung is part of the output (`extraction.path`) and the log
  line, so "why was this expensive" is answerable after the fact.
- Rung 2 needs the profile schema to be expressive enough for layouts we
  have not seen, which is the real risk carried by this design (see
  SPEC §7.6 — four side strategies rather than column positions alone).

## Revisit when

Rung 0 turns out to reconcile fewer than half of unseen statements — then the
heuristic profile is not pulling its weight and rung 2 should run first, with
rung 0 demoted to a verifier of the model's profile.
