"""Rung 2: the model derives a layout profile (SPEC §4 stage 7).

What the model is asked for is the thing it is actually good at — *where the
columns are* — and never the thing it is bad at, which is reading three
hundred amounts without dropping one. It sees a sample of the page as words
with coordinates, and returns a `LayoutProfile`; the rows are then read by the
same deterministic parser as always.

Its answer is checked twice: against the schema here, and against arithmetic
by the caller. A profile that parses into rows which do not reconcile is
rejected exactly like a heuristic one — which is why letting a model near this
stage is safe at all.
"""

from __future__ import annotations

import json
import re

from pydantic import ValidationError

from ..llm.client import LLMClient, Usage
from ..pdf.model import Page
from .profile import LayoutProfile

MAX_SAMPLE_PAGES = 2
"""SPEC §7.13: a sample, never the table."""

MAX_SAMPLE_LINES = 25
"""Enough lines to show the shape of a row; far fewer than a page holds."""

MAX_ATTEMPTS = 3
"""SPEC §7.14."""

SYSTEM = """You are given a sample of one bank statement page as words with \
their horizontal positions. Describe the LAYOUT so a deterministic parser can \
read every row.

Return ONLY a JSON object matching the schema. Never return transactions: \
rows are parsed from your description, not from your reading of them.

side_strategy tells the parser how a row becomes a deposit or a withdrawal:
  two_columns   - two separate amount columns; the left one is deposits
  signed        - one amount column carrying -, (), a trailing -, or CR/DR
  sections      - headings such as "Deposits and Additions" partition the rows
  balance_delta - a running-balance column; the step decides the direction
Choose the one the sample actually supports. x0/x1 are the horizontal bounds \
of a column, generous enough to hold every value in it."""

_FENCED = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def profile_from_pages(
    pages: list[Page],
    client: LLMClient,
    *,
    max_attempts: int = MAX_ATTEMPTS,
    feedback: str | None = None,
) -> tuple[LayoutProfile | None, Usage]:
    """Ask the model for a profile, retrying with the validation error it caused."""
    usage = Usage()
    if not pages:
        return None, usage

    schema = LayoutProfile.model_json_schema()
    prompt = _prompt(pages, feedback)

    for _ in range(max_attempts):
        completion = client.complete_json(SYSTEM, prompt, schema)
        usage.add(completion, getattr(client, "price", None))
        profile, problem = _parse(completion.content)
        if profile is not None:
            return profile, usage
        prompt = f"{prompt}\n\nYour previous answer was rejected: {problem}\nTry again."

    return None, usage


def _parse(content: str) -> tuple[LayoutProfile | None, str]:
    """A profile, or the reason it was refused — which becomes the next prompt."""
    fenced = _FENCED.search(content)
    text = fenced.group(1) if fenced else content
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        return None, f"not valid JSON ({error})"
    try:
        return LayoutProfile.model_validate(payload), ""
    except ValidationError as error:
        return None, error.json(include_url=False)


def _prompt(pages: list[Page], feedback: str | None) -> str:
    parts = []
    if feedback:
        parts.append(
            "The previous layout produced rows that did not reconcile: "
            f"{feedback}\nLook for a column or a side rule that was missed.\n"
        )
    for page in _sample(pages):
        parts.append(f"--- page {page.number} ---")
        for line in page.lines()[:MAX_SAMPLE_LINES]:
            parts.append(
                " ".join(f"[x0={w.x0:.0f},x1={w.x1:.0f}]{w.text}" for w in line)
            )
    return "\n".join(parts)


def _sample(pages: list[Page]) -> list[Page]:
    """The first page, which carries the header, plus the densest table page."""
    if len(pages) <= MAX_SAMPLE_PAGES:
        return pages
    densest = max(pages[1:], key=lambda page: len(page.words))
    return [pages[0], densest]
