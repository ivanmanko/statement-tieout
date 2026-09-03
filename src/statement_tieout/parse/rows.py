"""Transaction row parsing under a layout profile (SPEC §7.6, §7.12–7.12).

No model runs here on any rung of the ladder. Given a profile, reading rows is
arithmetic over coordinates — and it has to be, because the reconciliation
check downstream is only meaningful if this stage is reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from pydantic import ValidationError

from ..layout import Column, LayoutProfile, SideStrategy
from ..layout.dates import MAX_DATE_WORDS, STAND_IN_YEAR, parse_date
from ..money import ZERO, parse_money
from ..pdf.model import Page, Word
from ..schema import DateRange, Transaction

DEPOSIT, WITHDRAWAL = "deposit", "withdrawal"

MAX_FRAGMENT_GAP = 4.0
"""Horizontal gap within which two boxes are one value split by OCR (SPEC §7.2)."""

MAX_FRAGMENTS = 3
"""How many boxes one printed value may have been split across."""


@dataclass
class ParsedRows:
    transactions: list[Transaction] = field(default_factory=list)
    balances: list[Decimal] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class _Money:
    value: Decimal
    column: int | None  # index into profile.amount_columns
    is_balance: bool


def parse_rows(
    pages: list[Page],
    profile: LayoutProfile,
    *,
    period: DateRange | None = None,
    opening_balance: Decimal | None = None,
) -> ParsedRows:
    """Read every transaction row on these pages under this profile."""
    parsed = ParsedRows()
    state = _State(profile=profile, period=period, previous_balance=opening_balance)

    for page in pages:
        state.page = page.number
        for index, line in enumerate(page.lines()):
            state.line_index = index
            state.consume(line, parsed)

    state.finish(parsed)
    return parsed


@dataclass
class _State:
    profile: LayoutProfile
    period: DateRange | None
    previous_balance: Decimal | None
    section: str | None = None
    pending: list[Word] = field(default_factory=list)
    unsectioned_rows: int = 0
    yearless_rows: int = 0
    zero_rows: int = 0
    rejected_rows: int = 0
    summary_rows: int = 0
    recovered_rows: int = 0
    page: int | None = None
    line_index: int | None = None

    def consume(self, line: list[Word], parsed: ParsedRows) -> None:
        when = self._date_on(line)
        amounts = self._money_on(line)

        if when is not None and any(m.column is not None for m in amounts):
            if self._is_multi_column_summary(line):
                self.summary_rows += 1
                return
            self._emit(when, line, amounts, parsed)
            return

        if when is not None and self._recoverable(amounts):
            self._emit_from_balance(when, line, amounts, parsed)
            return

        if not amounts and self._is_description_only(line):
            self._continuation(line, parsed)

    def _recoverable(self, amounts: list[_Money]) -> bool:
        """SPEC §7.10: a balance with no amount beside it, and a chain to measure from."""
        if self.profile.balance_column is None or self.previous_balance is None:
            return False
        return not any(m.column is not None for m in amounts) and any(
            m.is_balance for m in amounts
        )

    def _emit_from_balance(
        self, when: date, line: list[Word], amounts: list[_Money], parsed: ParsedRows
    ) -> None:
        """Take the amount from the balance step, and let the zero rule filter the rest."""
        balance = next(m.value for m in amounts if m.is_balance)
        assert self.previous_balance is not None
        step = balance - self.previous_balance
        self.previous_balance = balance
        if step == ZERO:
            return  # a balance-summary line, not a transaction (SPEC §7.13)

        parsed.transactions.append(
            Transaction(
                date=when,
                description=self._description_on(line),
                deposit=step if step > ZERO else None,
                withdrawal=-step if step < ZERO else None,
                page=self.page,
                line=self.line_index,
                recovered=True,
            )
        )
        parsed.balances.append(balance)
        self.recovered_rows += 1

    def _is_multi_column_summary(self, line: list[Word]) -> bool:
        """SPEC §7.14: date -> amount -> date again is a summary table, not a row.

        Dates are joined across words the same way the leading date is, because
        a statement writes `Apr 11`, not `Apr11`. A date inside a description is
        unaffected: no amount separates it from the row's own date.
        """
        words = sorted(line, key=lambda word: word.x0)
        index, seen_date, seen_amount_after_date = 0, False, False
        while index < len(words):
            span = self._date_span_at(words, index)
            if span:
                if seen_amount_after_date:
                    return True
                seen_date, index = True, index + span
                continue
            if seen_date and _as_money(words[index].text) is not None:
                seen_amount_after_date = True
            index += 1
        return False

    def _date_span_at(self, words: list[Word], index: int) -> int:
        """How many words from `index` spell a date, longest first; 0 if none."""
        for length in range(min(MAX_DATE_WORDS, len(words) - index), 0, -1):
            if self._parse_join(words[index : index + length]) is not None:
                return length
        return 0

    def _continuation(self, line: list[Word], parsed: ParsedRows) -> None:
        text = " ".join(word.text for word in line)
        if self._matches_section(text):
            return
        if parsed.transactions:
            last = parsed.transactions[-1]
            parsed.transactions[-1] = last.model_copy(
                update={"description": f"{last.description} {text}".strip()}
            )

    def _matches_section(self, text: str) -> bool:
        normalized = " ".join(text.split()).casefold()
        for name in self.profile.deposit_sections:
            if normalized == name.casefold():
                self.section = DEPOSIT
                return True
        for name in self.profile.withdrawal_sections:
            if normalized == name.casefold():
                self.section = WITHDRAWAL
                return True
        return False

    def _emit(
        self, when: date, line: list[Word], amounts: list[_Money], parsed: ParsedRows
    ) -> None:
        amount = next(m for m in amounts if m.column is not None)
        balance = next((m.value for m in amounts if m.is_balance), None)

        if amount.value == ZERO:
            # SPEC §7.13: it moves no money and the contract cannot hold it.
            self.zero_rows += 1
            if balance is not None:
                self.previous_balance = balance
            return

        side = self._side_of(amount, balance)
        if side is None:
            self.unsectioned_rows += 1
            return

        try:
            transaction = Transaction(
                date=when,
                description=self._description_on(line),
                deposit=abs(amount.value) if side == DEPOSIT else None,
                withdrawal=abs(amount.value) if side == WITHDRAWAL else None,
                page=self.page,
                line=self.line_index,
            )
        except ValidationError:
            # SPEC §7.15: one malformed row must not cost the whole document.
            self.rejected_rows += 1
            return

        parsed.transactions.append(transaction)
        if balance is not None:
            parsed.balances.append(balance)
            self.previous_balance = balance
        if self.period is not None and self._outside_period(when):
            parsed.warnings.append(
                f"transaction dated {when.isoformat()} falls outside the statement period"
            )

    def _side_of(self, amount: _Money, balance: Decimal | None) -> str | None:
        strategy = self.profile.side_strategy
        if strategy is SideStrategy.SIGNED:
            return WITHDRAWAL if amount.value < 0 else DEPOSIT
        if strategy is SideStrategy.TWO_COLUMNS:
            return DEPOSIT if amount.column == 0 else WITHDRAWAL
        if strategy is SideStrategy.SECTIONS:
            return self.section
        if balance is None or self.previous_balance is None:
            return None
        return DEPOSIT if balance > self.previous_balance else WITHDRAWAL

    def _outside_period(self, when: date) -> bool:
        assert self.period is not None
        start, end = self.period.start, self.period.end
        return (start is not None and when < start) or (end is not None and when > end)

    def _date_on(self, line: list[Word]) -> date | None:
        """The date the words in the date column spell, joined longest-first.

        Two joins are tried: space-separated, because `extract_words` splits
        `Jan 28, 2025` into three; and bare, because OCR splits one printed
        date across touching boxes (`01-21` as `0`, `1-2`, `1`).
        """
        in_column = [w for w in line if self.profile.date_column.holds(w.center)]
        for length in range(min(len(in_column), MAX_DATE_WORDS), 0, -1):
            when = self._parse_join(in_column[:length])
            if when is not None:
                return when
        return None

    def _parse_join(self, words: list[Word]) -> date | None:
        for text in _joins(words):
            when = self._parse_date(text)
            if when is not None:
                return when
        return None

    def _parse_date(self, text: str) -> date | None:
        parsed = parse_date(text, self.profile.date_formats)
        if parsed is None:
            return None
        return self._infer_year(parsed) if parsed.year == STAND_IN_YEAR else parsed

    def _infer_year(self, parsed: date) -> date:
        """SPEC §7.12: a yearless row takes its year from the period."""
        start = self.period.start if self.period else None
        if start is None:
            self.yearless_rows += 1
            return parsed
        candidate = parsed.replace(year=start.year)
        return candidate if candidate >= start else candidate.replace(year=start.year + 1)

    def _money_on(self, line: list[Word]) -> list[_Money]:
        """Every amount on the line, rejoining values OCR split across boxes.

        A column holds one value per row, so two touching fragments inside one
        are one number (SPEC §7.2). Joining is tried only when the word alone
        is not already an amount.
        """
        words = sorted(line, key=lambda word: word.x0)
        found: list[_Money] = []
        index = 0
        while index < len(words):
            span, value = self._amount_at(words, index)
            if span == 0:
                index += 1
                continue
            group = words[index : index + span]
            placed = self._place(group, value)
            if placed is not None:
                found.append(placed)
            index += span
        return found

    def _amount_at(self, words: list[Word], index: int) -> tuple[int, Decimal | None]:
        """How many boxes from `index` spell one amount, and its value."""
        value = _as_money(words[index].text)
        if value is not None:
            return 1, value
        for length in range(min(MAX_FRAGMENTS, len(words) - index), 1, -1):
            group = words[index : index + length]
            if not _touching(group):
                continue
            for text in _joins(group, decimal=True):
                joined = _as_money(text)
                if joined is not None:
                    return length, joined
        return 0, None

    def _place(self, group: list[Word], value: Decimal | None) -> _Money | None:
        """Assign an amount to its column, or drop it as text inside a description."""
        if value is None:
            return None
        center = (group[0].x0 + group[-1].x1) / 2
        column = _column_index(self.profile.amount_columns, center)
        is_balance = (
            self.profile.balance_column is not None
            and self.profile.balance_column.holds(center)
        )
        if column is None and not is_balance:
            return None  # money inside the description band is text, not an amount
        return _Money(value=value, column=column, is_balance=is_balance)

    def _is_description_only(self, line: list[Word]) -> bool:
        """SPEC §7.16: a continuation carries words only in the description band."""
        return all(
            self.profile.date_column.x1 <= word.center <= self.profile.description_x1
            for word in line
        )

    def _description_on(self, line: list[Word]) -> str:
        return " ".join(
            word.text
            for word in line
            if self.profile.date_column.x1 <= word.center <= self.profile.description_x1
        )

    def finish(self, parsed: ParsedRows) -> None:
        if self.unsectioned_rows:
            parsed.warnings.append(
                f"{self.unsectioned_rows} row(s) appeared before any known section heading, "
                "so their side could not be determined"
            )
        if self.zero_rows:
            parsed.warnings.append(
                f"{self.zero_rows} row(s) carried a zero amount and were skipped: they move "
                "no money, and every transaction must carry one positive side"
            )
        if self.recovered_rows:
            parsed.warnings.append(
                f"{self.recovered_rows} row(s) lost their amount to OCR and had it recovered "
                "from the balance step"
            )
        if self.summary_rows:
            parsed.warnings.append(
                f"{self.summary_rows} line(s) held several date-and-amount pairs and were "
                "read as a multi-column summary table rather than as transactions"
            )
        if self.rejected_rows:
            parsed.warnings.append(
                f"{self.rejected_rows} row(s) were rejected by the output contract and "
                "skipped; reconciliation is what reports the loss"
            )
        if self.yearless_rows:
            parsed.warnings.append(
                f"{self.yearless_rows} row(s) used a date format carrying no year and no "
                "statement period was known, so the year is a placeholder"
            )


def _touching(words: list[Word]) -> bool:
    """Boxes that sit flush against one another — one value, split by OCR."""
    return all(
        later.x0 - earlier.x1 <= MAX_FRAGMENT_GAP
        for earlier, later in zip(words, words[1:], strict=False)
    )


def _joins(words: list[Word], decimal: bool = False) -> list[str]:
    """The ways these boxes might spell one value, most likely first."""
    texts = [word.text for word in words]
    forms = [" ".join(texts)]
    if len(texts) > 1 and _touching(words):
        forms.append("".join(texts))
        if decimal and len(texts[-1]) == 2 and texts[-1].isdigit():
            forms.append("".join(texts[:-1]) + "." + texts[-1])
    return forms


def _as_money(text: str) -> Decimal | None:
    try:
        return parse_money(text)
    except ValueError:
        return None


def _column_index(columns: list[Column], center: float) -> int | None:
    for index, column in enumerate(columns):
        if column.holds(center):
            return index
    return None
