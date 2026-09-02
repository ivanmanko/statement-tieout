"""PDF ingest: pages as words with coordinates."""

from .loader import ExtractionError, is_scanned, load_pages
from .model import Page, Word

__all__ = ["ExtractionError", "Page", "Word", "is_scanned", "load_pages"]
