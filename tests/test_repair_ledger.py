"""The editable ledger rung 4 works on (SPEC §4 stage 9, §7.18).

Pure and offline: no model here. This is the surface an agent is given, and
its whole job is that every edit is answered with a fresh verdict — the agent
is told after each move whether it helped, which is what makes an agentic
loop worth running at all.
"""

from datetime import date
from decimal import Decimal

import pytest

from statement_tieout.pdf.model import Page, Word
from statement_tieout.reconcile.repair import RepairLedger
from statement_tieout.schema import Summary, Transaction

PRINTED = {
    "beginning_balance", "ending_balance", "deposits_total",
    "deposits_count", "withdrawals_total", "withdrawals_count",
}


def summary(dep="300.00", dep_n=2, wd="50.00", wd_n=1):
    return Summary(
        beginning_balance=Decimal("1000.00"),
        ending_balance=Decimal("1250.00"),
        deposits_total=Decimal(dep),
        deposits_count=dep_n,
        withdrawals_total=Decimal(wd),
        withdrawals_count=wd_n,
        printed_fields=PRINTED,
    )


def rows(*specs):
    return [
        Transaction(
            date=date(2025, 1, i + 1),
            description=f"ROW {i}",
            deposit=Decimal(amount) if side == "d" else None,
            withdrawal=Decimal(amount) if side == "w" else None,
        )
        for i, (side, amount) in enumerate(specs)
    ]


def pages(*texts):
    return [
        Page(number=i + 1, words=[Word(text=t, x0=0.0, x1=10.0, top=0.0)], text=t)
        for i, t in enumerate(texts)
    ]


def ledger(transactions=None, page_texts=("01/02 SOMETHING 100.00",)):
    return RepairLedger(
        summary=summary(),
        transactions=transactions if transactions is not None else rows(("d", "100.00")),
        pages=pages(*page_texts),
    )


class TestState:
    def test_state_reports_the_residual_and_the_checks(self):
        report = ledger().state()
        assert "-150.00" in report  # 1000 + 100 - 0 - 1250
        assert "deposits_total" in report

    def test_a_reconciled_ledger_says_so(self):
        book = ledger(rows(("d", "100.00"), ("d", "200.00"), ("w", "50.00")))
        assert book.reconciled is True
        assert "reconciled" in book.state()


class TestInspection:
    def test_find_amount_reports_the_page_it_appears_on(self):
        book = ledger(page_texts=("nothing here", "01/03 AETNA 200.00 1,876.00"))
        assert "page 2" in book.find_amount("200.00")

    def test_find_amount_says_when_a_row_already_carries_it(self):
        book = ledger(rows(("d", "100.00")), page_texts=("01/02 SOMETHING 100.00",))
        assert "already" in book.find_amount("100.00")

    def test_find_amount_on_something_absent(self):
        assert "no page" in ledger().find_amount("999.99")

    def test_read_page_returns_its_lines(self):
        book = ledger(page_texts=("first line", "second line"))
        assert "second line" in book.read_page(2)

    def test_read_page_out_of_range_is_an_error_not_a_crash(self):
        assert "no page" in ledger().read_page(99)

    def test_list_rows_shows_index_side_and_amount(self):
        book = ledger(rows(("d", "100.00"), ("w", "50.00")))
        listing = book.list_rows(0, 2)
        assert "[0]" in listing and "deposit" in listing
        assert "[1]" in listing and "withdrawal" in listing


class TestEditing:
    def test_inserting_the_missing_row_reconciles(self):
        book = ledger(rows(("d", "100.00"), ("w", "50.00")))
        assert book.reconciled is False
        book.insert_row("2025-01-05", "RECOVERED", "deposit", "200.00")
        assert book.reconciled is True

    def test_every_edit_answers_with_the_new_state(self):
        book = ledger(rows(("d", "100.00"), ("w", "50.00")))
        assert "residual" in book.insert_row("2025-01-05", "X", "deposit", "1.00")

    def test_dropping_a_duplicate_reconciles(self):
        book = ledger(rows(("d", "100.00"), ("d", "200.00"), ("d", "75.00"), ("w", "50.00")))
        book.drop_row(2)
        assert book.reconciled is True

    def test_flipping_a_side_reconciles(self):
        book = ledger(rows(("w", "100.00"), ("d", "200.00"), ("w", "50.00")))
        book.set_side(0, "deposit")
        assert book.reconciled is True

    def test_an_out_of_range_index_is_an_error_not_a_crash(self):
        assert "no row" in ledger().drop_row(99)

    def test_a_bad_side_is_refused(self):
        assert "side" in ledger().set_side(0, "sideways")

    def test_a_bad_amount_is_refused(self):
        assert "amount" in ledger().insert_row("2025-01-05", "X", "deposit", "not money")

    def test_a_bad_date_is_refused(self):
        assert "date" in ledger().insert_row("yesterday", "X", "deposit", "1.00")


class TestDiscarding:
    """SPEC §4 stage 9 — a repair that does not close the period is thrown away."""

    def test_the_original_rows_survive_a_failed_repair(self):
        original = rows(("d", "100.00"), ("w", "50.00"))
        book = RepairLedger(summary=summary(), transactions=original, pages=pages("x"))
        book.insert_row("2025-01-05", "WRONG", "deposit", "7.00")
        assert book.reconciled is False
        assert [t.description for t in book.result()] == ["ROW 0", "ROW 1"]

    def test_a_successful_repair_is_returned(self):
        book = ledger(rows(("d", "100.00"), ("w", "50.00")))
        book.insert_row("2025-01-05", "RECOVERED", "deposit", "200.00")
        assert "RECOVERED" in [t.description for t in book.result()]

    def test_the_caller_is_never_handed_a_half_repair(self):
        book = ledger(rows(("d", "100.00"), ("w", "50.00")))
        book.insert_row("2025-01-05", "A", "deposit", "100.00")
        book.insert_row("2025-01-06", "B", "deposit", "50.00")
        assert book.reconciled is False
        assert len(book.result()) == 2

    @pytest.mark.parametrize("edits", [0, 1, 5])
    def test_edit_count_is_reported(self, edits):
        book = ledger()
        for i in range(edits):
            book.insert_row("2025-01-05", f"X{i}", "deposit", "1.00")
        assert book.edits == edits
