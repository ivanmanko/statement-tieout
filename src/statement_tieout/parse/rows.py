"""Transaction row parsing under a layout profile (SPEC §7.6, §7.11–7.12).

No model runs here on any rung of the ladder. Given a profile, reading rows is
arithmetic over coordinates — and it has to be, because the reconciliation
check downstream is only meaningful if this stage is reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from ..layout import Column, LayoutProfile, SideStrategy
from ..layout.dates import MAX_DATE_WORDS, STAND_IN_YEAR, parse_date
from ..money import parse_money
from ..pdf.model import Page, Word
from ..schema import DateRange, Transaction

DEPOSIT, WITHDRAWAL = "deposit", "withdrawal"


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
        for line in page.lines():
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

    def consume(self, line: list[Word], parsed: ParsedRows) -> None:
        when = self._date_on(line)
        amounts = self._money_on(line)

        if when is not None and any(m.column is not None for m in amounts):
            self._emit(when, line, amounts, parsed)
            return

        if not amounts and self._is_description_only(line):
            self._continuation(line, parsed)

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

        side = self._side_of(amount, balance)
        if side is None:
            self.unsectioned_rows += 1
            return

        parsed.transactions.append(
            Transaction(
                date=when,
                description=self._description_on(line),
                deposit=abs(amount.value) if side == DEPOSIT else None,
                withdrawal=abs(amount.value) if side == WITHDRAWAL else None,
            )
        )
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
        """The date is whatever the words in the date column spell, joined.

        `extract_words` splits on whitespace, so `Jan 28, 2025` arrives as
        three words; longest join first, so a wider format wins over a
        prefix of it.
        """
        in_column = [w.text for w in line if self.profile.date_column.holds(w.center)]
        for length in range(min(len(in_column), MAX_DATE_WORDS), 0, -1):
            when = self._parse_date(" ".join(in_column[:length]))
            if when is not None:
                return when
        return None

    def _parse_date(self, text: str) -> date | None:
        parsed = parse_date(text, self.profile.date_formats)
        if parsed is None:
            return None
        return self._infer_year(parsed) if parsed.year == STAND_IN_YEAR else parsed

    def _infer_year(self, parsed: date) -> date:
        """SPEC §7.11: a yearless row takes its year from the period."""
        start = self.period.start if self.period else None
        if start is None:
            self.yearless_rows += 1
            return parsed
        candidate = parsed.replace(year=start.year)
        return candidate if candidate >= start else candidate.replace(year=start.year + 1)

    def _money_on(self, line: list[Word]) -> list[_Money]:
        found = []
        for word in line:
            value = _as_money(word.text)
            if value is None:
                continue
            column = _column_index(self.profile.amount_columns, word.center)
            is_balance = (
                self.profile.balance_column is not None
                and self.profile.balance_column.holds(word.center)
            )
            if column is None and not is_balance:
                continue  # money inside the description band is text, not an amount
            found.append(_Money(value=value, column=column, is_balance=is_balance))
        return found

    def _is_description_only(self, line: list[Word]) -> bool:
        """SPEC §7.13: a continuation carries words only in the description band."""
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
        if self.yearless_rows:
            parsed.warnings.append(
                f"{self.yearless_rows} row(s) used a date format carrying no year and no "
                "statement period was known, so the year is a placeholder"
            )


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
