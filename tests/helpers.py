"""Synthetic pages of words-with-coordinates, shared by the parsing tests.

Parsing is tested on fixtures rather than on sample PDFs on purpose: the
parser has to be correct for layouts we do not have, and a test that needs a
real file can only ever check the ones we do.
"""

from statement_tieout.pdf.model import Page, Word

DATE_X, DESC_X, LEFT_X, RIGHT_X, BALANCE_X = 30.0, 90.0, 330.0, 410.0, 490.0
CHAR_WIDTH = 6.0


def word(text: str, x0: float, top: float) -> Word:
    return Word(text=text, x0=x0, x1=x0 + len(text) * CHAR_WIDTH, top=top)


def line(top: float, *cells: tuple[float, str]) -> list[Word]:
    """Lay out one visual line: each cell is (x0, text); text is split on spaces."""
    words = []
    for x0, text in cells:
        x = x0
        for token in text.split():
            words.append(word(token, x, top))
            x += len(token) * CHAR_WIDTH + 4.0
    return words


def page(*lines: list[Word], number: int = 1) -> Page:
    words = [w for group in lines for w in group]
    return Page(number=number, words=words, text=" ".join(w.text for w in words))


def rows_page(*specs: tuple[str, str, str, str | None], top: float = 100.0) -> Page:
    """A page of transaction rows: (date, description, amount, balance-or-None)."""
    lines = []
    for index, (when, description, amount, balance) in enumerate(specs):
        cells = [(DATE_X, when), (DESC_X, description), (LEFT_X, amount)]
        if balance is not None:
            cells.append((BALANCE_X, balance))
        lines.append(line(top + index * 12.0, *cells))
    return page(*lines)


def right_aligned(top: float, right: float, text: str) -> list[Word]:
    """One cell whose *right* edge sits at `right` — how statements set amounts."""
    width = len(text) * CHAR_WIDTH
    return [word(text, right - width, top)]


def two_column_row(
    top: float,
    when: str,
    description: str,
    *,
    deposit: str | None = None,
    withdrawal: str | None = None,
    balance: str | None = None,
    deposit_right: float = 380.0,
    withdrawal_right: float = 470.0,
    balance_right: float = 560.0,
) -> list[Word]:
    """A row in the two-amount-column layout real statements use."""
    words = line(top, (DATE_X, when), (DESC_X, description))
    for amount, right in (
        (deposit, deposit_right),
        (withdrawal, withdrawal_right),
        (balance, balance_right),
    ):
        if amount is not None:
            words.extend(right_aligned(top, right, amount))
    return words
