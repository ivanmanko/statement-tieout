"""Reading a PDF into pages of words with coordinates (SPEC §4 stage 1).

Two backends behind one function: the text layer where there is one, local OCR
where there is not (SPEC §7.2). Everything downstream sees the same `Page`
either way, which is what lets a scanned statement and a digital one go
through exactly the same parser and the same verifier.
"""

from __future__ import annotations

from pathlib import Path

import pdfplumber

from .model import Page, Word

MIN_CHARS_PER_TEXT_PAGE = 20
"""Below this a page carries no usable text layer and is a scan (SPEC §7.2)."""


class ExtractionError(RuntimeError):
    """The file could not be opened or read at all (SPEC §6 edge case 1)."""


def load_pages(path: str | Path, *, ocr: bool = True) -> list[Page]:
    """Every page as words with coordinates, reading scans with OCR unless disabled."""
    try:
        with pdfplumber.open(path) as document:
            pages = [_from_text_layer(n, p) for n, p in enumerate(document.pages, start=1)]
    except ExtractionError:
        raise
    except Exception as error:  # pdfplumber raises a wide range for bad input
        raise ExtractionError(f"could not read {path}: {error}") from error

    scanned = [page.number for page in pages if page.source == "empty"]
    if scanned and ocr:
        pages = _with_ocr(path, pages, scanned)
    return pages


def _from_text_layer(number: int, source) -> Page:
    text = source.extract_text() or ""
    if len(text.strip()) < MIN_CHARS_PER_TEXT_PAGE:
        return Page(number=number, words=[], text="", source="empty")
    words = [
        Word(text=w["text"], x0=float(w["x0"]), x1=float(w["x1"]), top=float(w["top"]))
        for w in source.extract_words()
    ]
    return Page(number=number, words=words, text=text, source="text")


def _with_ocr(path: str | Path, pages: list[Page], scanned: list[int]) -> list[Page]:
    """Re-read the pages with no text layer as pixels."""
    import pypdfium2

    from .ocr import read_page

    try:
        document = pypdfium2.PdfDocument(path)
    except Exception as error:
        raise ExtractionError(f"could not rasterize {path}: {error}") from error

    try:
        for index in scanned:
            words = read_page(document[index - 1])
            pages[index - 1] = Page(
                number=index,
                words=words,
                text=_as_text(words),
                source="ocr" if words else "empty",
            )
    finally:
        document.close()
    return pages


def _as_text(words: list[Word]) -> str:
    """Reconstruct page text from OCR words, one line per visual row."""
    return "\n".join(
        " ".join(word.text for word in line)
        for line in Page(number=0, words=words).lines()
    )


def is_scanned(page: Page) -> bool:
    """SPEC §7.2: this page had no text layer, whether or not OCR then read it."""
    return page.source in ("ocr", "empty")
