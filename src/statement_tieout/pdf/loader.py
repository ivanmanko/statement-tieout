"""Reading a PDF into pages of words with coordinates (SPEC §4 stage 1).

The only module that knows pdfplumber exists. Everything downstream works on
`Page` objects, which is what lets the parser be tested on layouts we do not
have a file for.
"""

from __future__ import annotations

from pathlib import Path

import pdfplumber

from .model import Page, Word

MIN_CHARS_PER_TEXT_PAGE = 20
"""Below this a page carries no usable text layer and is a scan (SPEC §7.2)."""


class ExtractionError(RuntimeError):
    """The file could not be opened or read at all (SPEC §6 edge case 1)."""


def load_pages(path: str | Path) -> list[Page]:
    """Every page as words with coordinates. Scanned pages come back empty."""
    try:
        with pdfplumber.open(path) as document:
            return [_page(index, source) for index, source in enumerate(document.pages, start=1)]
    except ExtractionError:
        raise
    except Exception as error:  # pdfplumber raises a wide range for bad input
        raise ExtractionError(f"could not read {path}: {error}") from error


def _page(number: int, source) -> Page:
    text = source.extract_text() or ""
    words = [
        Word(text=w["text"], x0=float(w["x0"]), x1=float(w["x1"]), top=float(w["top"]))
        for w in source.extract_words()
    ]
    return Page(number=number, words=words, text=text)


def is_scanned(page: Page) -> bool:
    """SPEC §7.2: a page yielding almost no text has no text layer."""
    return len(page.text.strip()) < MIN_CHARS_PER_TEXT_PAGE
