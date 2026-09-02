# ADR-005: OCR is an ingest backend, not a rung of the ladder

- **Status:** Accepted
- **Date:** 2026-09-02
- **Amends:** [ADR-002](002-escalation-ladder.md), whose rung 3 was "vision
  transcription, one page per call"

## Context

Diagnosis of the samples killed the assumption the ladder was designed around.
Two of the three statements available have **no text layer at all** — not the
53.8 MB one, but a 565 KB one and a 6.2 MB one:

| file | size | pages | text |
|---|---|---|---|
| Great Lakes x4071 | 13.7 KiB | 6 | 17,177 chars |
| S2.6.1.1-2 Account 6426 | 565 KiB | 4 | **0 chars** |
| April 2021 | 6.2 MiB | 15 | **103 chars, all private-use glyphs from a barcode font** |

So reading scans is not an optional rung for exotic inputs; it is most of the
assignment. And both scans turn out to be *rasterized digital documents* —
crisp, machine-set type, not photographs of paper.

That last fact matters. A crisp raster is exactly what OCR is good at, and an
OCR engine returns **text with bounding boxes** — which is precisely the
words-with-coordinates structure the parser already consumes.

## Decision

OCR is part of **ingest**, alongside the text layer, not a rung above the
deterministic parser. A page with no text layer is rendered at scale 3.0 and
read locally (ONNX, pip-installable, no system dependency, no network); the
words it produces go into the same `Page` objects, and the same profile
derivation, the same row parser and the same reconciliation run over them
unchanged.

The ladder keeps its shape but is now about **interpretation** only: rung 0
heuristic profile, rung 1 cached profile, rung 2 model-derived profile, rung 3
model transcription of pages OCR reads too poorly, rung 4 bounded repair.

**Measured on the two scans:** every money value read correctly on the Fulton
statement — 284 transactions across 15 scanned pages reconciling to the cent
against the printed totals, in 51 seconds, for **$0.00 and zero API calls**.

## Alternatives considered

1. **Vision-model transcription as the primary path for scans** (the original
   rung 3). Rejected as the default: N calls per statement, nondeterministic
   where `summary` is graded on exact match, and it discards a parser that
   already works. Kept as a rung *above* OCR, for pages OCR reads poorly —
   which is where a model earns its cost, because by then the verifier has
   proved something is wrong.
2. **Tesseract.** Same idea, better-known engine, rejected on deployment: it
   is a system binary the reviewers would have to install, and the
   deliverable is a function that should run after `pip install`.
3. **Raising the render scale to fix OCR errors.** Measured, and it does not:
   the one systematic misread on the Renasant statement (`32,537.69` read as
   `32/537.69`) is identical at scales 3, 4 and 5. Fixed instead by a single
   declared token repair (SPEC §7.2), which is safe because a wrong repair
   still has to pass reconciliation.
4. **Treating a scan as unreadable and reporting that** — what this project
   did before this ADR. Honest, and worth far less than reading the file.

## Consequences

- Scans cost nothing and are reproducible, which matters because `summary` is
  graded on exact equality and two runs of a model are not equal.
- Latency is now dominated by OCR: ~3.4 s per scanned page, so 51 s for a
  15-page statement against 0.2 s for a text-layer one. Acceptable for a
  batch tool, and the number is reported rather than hidden.
- OCR loses spaces inside all-capital descriptions, so descriptions from a
  scan are less faithful than descriptions from a text layer. Amounts, dates
  and balances are unaffected — and those are what reconciliation and the
  graded fields depend on.
- The engine's models add ~80 MB to an install.

## Revisit when

A statement arrives as a photograph rather than a rasterized document, where
OCR quality drops enough that the verifier rejects most pages. That is the
case rung 3 exists for, and the trigger to build it.
