"""Step 0: what are we actually dealing with?

Reports, per file: page count, text-layer density, period anchors, and whether a
running-balance column is present. The answers decide which rungs of the
escalation ladder we need to build.
"""

import re
import sys
from pathlib import Path

import pdfplumber

PERIOD_ANCHORS = [
    r"beginning\s+balance",
    r"previous\s+balance",
    r"statement\s+period",
    r"account\s+summary",
    r"summary\s+of\s+account",
]
BALANCE_COL = r"\b(running\s+)?balance\b"
AMOUNT = re.compile(r"[-(]?\$?\d{1,3}(?:,\d{3})*\.\d{2}\)?-?")


def diagnose(path: Path) -> None:
    print(f"\n{'=' * 78}\n{path.name}  ({path.stat().st_size / 1024:.1f} KiB)\n{'=' * 78}")
    with pdfplumber.open(path) as pdf:
        pages = pdf.pages
        print(f"pages: {len(pages)}")

        empty, chars, words, amounts = 0, 0, 0, 0
        anchor_hits: dict[str, list[int]] = {a: [] for a in PERIOD_ANCHORS}
        balance_pages = []

        for i, page in enumerate(pages, 1):
            text = page.extract_text() or ""
            chars += len(text)
            words += len(page.extract_words())
            amounts += len(AMOUNT.findall(text))
            if len(text.strip()) < 20:
                empty += 1
            low = text.lower()
            for a in PERIOD_ANCHORS:
                if re.search(a, low):
                    anchor_hits[a].append(i)
            if re.search(BALANCE_COL, low):
                balance_pages.append(i)

        n = len(pages)
        print(f"text layer: {chars} chars, {words} words, ~{chars // max(n, 1)} chars/page")
        print(f"pages with (almost) no text: {empty}/{n}  ->  "
              f"{'SCAN - needs vision/OCR' if empty > n / 2 else 'has text layer'}")
        print(f"amount-shaped tokens: {amounts}")
        print("period anchors:")
        for a, hits in anchor_hits.items():
            if hits:
                more = "..." if len(hits) > 12 else ""
                print(f"  {a:<26} x{len(hits):<3} pages {hits[:12]}{more}")
        print(f"'balance' column word on pages: {balance_pages[:12]}"
              f"{'...' if len(balance_pages) > 12 else ''}")


def main() -> None:
    paths = [Path(p) for p in sys.argv[1:]] or sorted(Path("samples").glob("*.pdf"))
    if not paths:
        sys.exit("no PDFs given and samples/ is empty")
    for p in paths:
        diagnose(p)


if __name__ == "__main__":
    main()
