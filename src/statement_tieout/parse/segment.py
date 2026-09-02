"""Splitting a document into statement periods (SPEC §7.3).

Segmentation runs *before* parsing. A binder of concatenated statements has to
be cut first, because reconciliation is per period: totals from one statement
compared against rows from two is a failure with no useful diagnosis.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..layout.dates import starts_with_date
from ..pdf.model import Page
from ..schema import DateRange
from .header import read_last4, read_period
from .labels import BEGINNING_LABELS


@dataclass(frozen=True)
class _Anchors:
    beginning: bool
    last4: str | None
    period: DateRange | None


def page_lines(page: Page) -> list[str]:
    """The page's visual lines as text, which is what every anchor is matched on."""
    return [" ".join(word.text for word in line) for line in page.lines()]


def segment(pages: list[Page]) -> list[list[Page]]:
    """Group pages into periods. A document with no anchor is a single period."""
    if not pages:
        return []

    anchors = [_anchors_on(page) for page in pages]
    # SPEC §7.3: an anchor on every page is a running header, not a period marker.
    beginning_is_boilerplate = len(pages) > 1 and all(a.beginning for a in anchors)

    groups: list[list[Page]] = [[pages[0]]]
    last4 = anchors[0].last4
    period = anchors[0].period

    for page, anchor in zip(pages[1:], anchors[1:], strict=True):
        if _starts_a_period(anchor, last4, period, beginning_is_boilerplate):
            groups.append([page])
        else:
            groups[-1].append(page)
        last4 = anchor.last4 or last4
        period = anchor.period or period

    return groups


def _starts_a_period(
    anchor: _Anchors,
    last4: str | None,
    period: DateRange | None,
    beginning_is_boilerplate: bool,
) -> bool:
    if anchor.beginning and not beginning_is_boilerplate:
        return True
    if anchor.last4 is not None and last4 is not None and anchor.last4 != last4:
        return True
    return anchor.period is not None and period is not None and anchor.period != period


def _anchors_on(page: Page) -> _Anchors:
    lines = page_lines(page)
    header_lines = [line for line in lines if not starts_with_date(line)]
    found_period = read_period(header_lines)
    return _Anchors(
        beginning=any(
            label in " ".join(line.split()).casefold()
            for line in header_lines
            for label in BEGINNING_LABELS
        ),
        last4=read_last4(header_lines),
        period=found_period if found_period != DateRange() else None,
    )
