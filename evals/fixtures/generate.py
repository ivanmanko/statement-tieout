"""Generating the held-out statement corpus (SPEC §12.1).

Generalization is a third of what this project is judged on, and four sample
files cannot measure it. These are written here instead: each fixture is a
statement laid out in a way no sample uses, and its ground truth is exact by
construction rather than read off a page by eye.

**The PDFs are not committed; this file is.** It is deterministic — the same
seed lays out the same statements to the point — so the corpus is regenerated
wherever it is needed, including in a checkout that has never seen a real
statement. That is what lets the accuracy harness run somewhere other than the
machine the samples happen to sit on.

    uv run python evals/fixtures/generate.py

writes the PDFs to `samples/generated/` and their ground truth to
`evals/expected/`, after which

    uv run python evals/run_evals.py --samples samples/generated

scores the parser against layouts it was never tuned on.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SAMPLES = ROOT / "samples" / "generated"
EXPECTED = ROOT / "evals" / "expected"

PAGE = (612.0, 792.0)
LEFT, RIGHT = 54.0, 558.0
CENTS = Decimal("0.01")


# --------------------------------------------------------------------------- model


@dataclass(frozen=True)
class Row:
    when: date
    description: str
    amount: Decimal
    deposit: bool
    balance: Decimal = Decimal("0.00")


@dataclass
class Statement:
    """One period of one account, and everything a renderer needs to draw it."""

    bank: str
    last4: str
    start: date
    end: date
    opening: Decimal
    rows: list[Row] = field(default_factory=list)
    currency: str = "$"

    @property
    def deposits(self) -> list[Row]:
        return [row for row in self.rows if row.deposit]

    @property
    def withdrawals(self) -> list[Row]:
        return [row for row in self.rows if not row.deposit]

    @property
    def deposits_total(self) -> Decimal:
        return sum((row.amount for row in self.deposits), Decimal("0.00"))

    @property
    def withdrawals_total(self) -> Decimal:
        return sum((row.amount for row in self.withdrawals), Decimal("0.00"))

    @property
    def closing(self) -> Decimal:
        return self.opening + self.deposits_total - self.withdrawals_total


DESCRIPTIONS = (
    "ACH CREDIT LEDGERWORKS SETTLEMENT",
    "REMOTE DEPOSIT LINK BATCH",
    "WIRE TRANSFER IN CASTLEFORD HOLDINGS",
    "CARD PURCHASE FORECOURT 4471",
    "ACH DEBIT PAYROLL SERVICES CO",
    "TRANSFER TO RESERVE ACCOUNT",
    "MONTHLY ACCOUNT MAINTENANCE",
    "ACH DEBIT UTILITIES NORTHFIELD",
    "CHEQUE 40861",
    "INTEREST PAID",
    "ACH CREDIT MARLOWE PARTS AND SERVICE",
    "POS PURCHASE HARBOURSIDE SUPPLY",
)


def build(seed: int, bank: str, last4: str, start: date, days: int, count: int,
          opening: str, currency: str = "$") -> Statement:
    """A period of plausible activity, laid out to the cent from a fixed seed."""
    rolling = random.Random(seed)
    statement = Statement(
        bank=bank,
        last4=last4,
        start=start,
        end=start + timedelta(days=days - 1),
        opening=Decimal(opening),
        currency=currency,
    )
    balance = statement.opening
    for index in range(count):
        deposit = rolling.random() < 0.42
        amount = (Decimal(rolling.randrange(2_500, 4_800_00)) / 100).quantize(CENTS)
        balance = balance + amount if deposit else balance - amount
        statement.rows.append(
            Row(
                when=start + timedelta(days=min(index * days // count, days - 1)),
                description=rolling.choice(DESCRIPTIONS),
                amount=amount,
                deposit=deposit,
                balance=balance,
            )
        )
    return statement


# --------------------------------------------------------------------------- drawing


def money(value: Decimal, currency: str = "$") -> str:
    return f"{currency}{value:,.2f}"


def _canvas(target: BytesIO):
    from reportlab.pdfgen.canvas import Canvas

    return Canvas(target, pagesize=PAGE)


def _letterhead(pen, statement: Statement, title: str, period_line: str) -> float:
    pen.setFont("Helvetica-Bold", 17)
    pen.drawString(LEFT, 740, statement.bank)
    pen.setFont("Helvetica", 9)
    pen.drawString(LEFT, 726, title)
    pen.drawString(LEFT, 714, period_line)
    pen.drawString(LEFT, 702, f"Account Number: ****{statement.last4}")
    return 676.0


def _vertical_summary(pen, statement: Statement, top: float, *, counts: bool) -> float:
    pen.setFont("Helvetica-Bold", 10)
    pen.drawString(LEFT, top, "Account Summary")
    pen.setFont("Helvetica", 9)
    deposits = "Deposits and credits"
    withdrawals = "Withdrawals and debits"
    if counts:
        deposits += f" ({len(statement.deposits)})"
        withdrawals += f" ({len(statement.withdrawals)})"
    lines = (
        ("Beginning balance", statement.opening),
        (deposits, statement.deposits_total),
        (withdrawals, statement.withdrawals_total),
        ("Ending balance", statement.closing),
    )
    y = top - 16
    for label, value in lines:
        pen.drawString(LEFT, y, label)
        pen.drawRightString(RIGHT, y, money(value, statement.currency))
        y -= 13
    return y - 14


def _horizontal_summary(pen, statement: Statement, top: float) -> float:
    columns = (
        ("Prior Statement Balance", statement.opening, 150.0),
        ("Total Deposits/Credits", statement.deposits_total, 280.0),
        ("Total Checks/Debits", statement.withdrawals_total, 420.0),
        ("Ending Statement Balance", statement.closing, RIGHT),
    )
    pen.setFont("Helvetica", 8)
    for label, _, right in columns:
        pen.drawRightString(right, top, label)
    pen.setFont("Helvetica-Bold", 9)
    for _, value, right in columns:
        pen.drawRightString(right, top - 13, money(value, statement.currency))
    return top - 40


def _rows_two_column(pen, statement: Statement, top: float, fmt: str) -> None:
    pen.setFont("Helvetica-Bold", 8)
    pen.drawString(LEFT, top, "Date")
    pen.drawString(LEFT + 58, top, "Description")
    pen.drawRightString(392.0, top, "Deposits")
    pen.drawRightString(468.0, top, "Withdrawals")
    pen.drawRightString(RIGHT, top, "Balance")
    pen.setFont("Helvetica", 8)
    y = top - 14
    for row in statement.rows:
        if y < 60:
            pen.showPage()
            pen.setFont("Helvetica", 8)
            y = 740
        pen.drawString(LEFT, y, row.when.strftime(fmt))
        pen.drawString(LEFT + 58, y, row.description)
        column = 392.0 if row.deposit else 468.0
        pen.drawRightString(column, y, f"{row.amount:,.2f}")
        pen.drawRightString(RIGHT, y, f"{row.balance:,.2f}")
        y -= 11


def _rows_signed(pen, statement: Statement, top: float, fmt: str) -> None:
    pen.setFont("Helvetica-Bold", 8)
    pen.drawString(LEFT, top, "Date")
    pen.drawString(LEFT + 58, top, "Description")
    pen.drawRightString(460.0, top, "Amount")
    pen.drawRightString(RIGHT, top, "Balance")
    pen.setFont("Helvetica", 8)
    y = top - 14
    for row in statement.rows:
        if y < 60:
            pen.showPage()
            pen.setFont("Helvetica", 8)
            y = 740
        signed = row.amount if row.deposit else -row.amount
        pen.drawString(LEFT, y, row.when.strftime(fmt))
        pen.drawString(LEFT + 58, y, row.description)
        pen.drawRightString(460.0, y, f"{signed:,.2f}")
        pen.drawRightString(RIGHT, y, f"{row.balance:,.2f}")
        y -= 11


def _rows_sectioned(pen, statement: Statement, top: float, fmt: str) -> None:
    """No side column at all: the heading above a run of rows says which side."""
    y = top
    for heading, rows in (
        ("DEPOSITS AND OTHER CREDITS", statement.deposits),
        ("WITHDRAWALS AND OTHER DEBITS", statement.withdrawals),
    ):
        pen.setFont("Helvetica-Bold", 9)
        pen.drawString(LEFT, y, heading)
        y -= 13
        pen.setFont("Helvetica", 8)
        for row in rows:
            if y < 60:
                pen.showPage()
                pen.setFont("Helvetica", 8)
                y = 740
            pen.drawString(LEFT, y, row.when.strftime(fmt))
            pen.drawString(LEFT + 58, y, row.description)
            pen.drawRightString(RIGHT, y, f"{row.amount:,.2f}")
            y -= 11
        y -= 12


def _rows_money_in_out(pen, statement: Statement, top: float) -> None:
    """The British column set, and its date notation."""
    pen.setFont("Helvetica-Bold", 8)
    pen.drawString(LEFT, top, "Date")
    pen.drawString(LEFT + 62, top, "Payment type and details")
    pen.drawRightString(392.0, top, "Money out")
    pen.drawRightString(468.0, top, "Money in")
    pen.drawRightString(RIGHT, top, "Balance")
    pen.setFont("Helvetica", 8)
    y = top - 14
    for row in statement.rows:
        if y < 60:
            pen.showPage()
            pen.setFont("Helvetica", 8)
            y = 740
        pen.drawString(LEFT, y, row.when.strftime("%d %b %y"))
        pen.drawString(LEFT + 62, y, row.description)
        pen.drawRightString(468.0 if row.deposit else 392.0, y, f"{row.amount:,.2f}")
        pen.drawRightString(RIGHT, y, f"{row.balance:,.2f}")
        y -= 11


# --------------------------------------------------------------------------- scanning


def scanned(pdf: bytes, dpi: int, angle: float, blur: float, noise: int) -> bytes:
    """The same statement as a photocopy: rasterized, set off square, degraded."""
    import numpy
    import pypdfium2
    from PIL import Image, ImageFilter

    document = pypdfium2.PdfDocument(BytesIO(pdf))
    frames = []
    try:
        for index in range(len(document)):
            image = document[index].render(scale=dpi / 72).to_pil().convert("L")
            if angle:
                image = image.rotate(angle, resample=Image.BICUBIC, fillcolor=255)
            if blur:
                image = image.filter(ImageFilter.GaussianBlur(blur))
            if noise:
                grain = numpy.random.default_rng(7).normal(0, noise, (image.height, image.width))
                array = numpy.clip(numpy.asarray(image, dtype=float) + grain, 0, 255)
                image = Image.fromarray(array.astype("uint8"))
            frames.append(image.convert("RGB"))
    finally:
        document.close()

    out = BytesIO()
    frames[0].save(out, "PDF", resolution=float(dpi), save_all=True, append_images=frames[1:])
    return out.getvalue()


# --------------------------------------------------------------------------- fixtures


def render(statement: Statement, style: str, *, counts: bool, fmt: str = "%m/%d/%Y") -> bytes:
    target = BytesIO()
    pen = _canvas(target)
    period_line = (
        f"Statement Period: {statement.start.strftime('%B %d, %Y')}"
        f" - {statement.end.strftime('%B %d, %Y')}"
    )
    if style == "money_in_out":
        period_line = (
            f"Statement Period: {statement.start.strftime('%d %b %y')}"
            f" to {statement.end.strftime('%d %b %y')}"
        )
    top = _letterhead(pen, statement, "Business Account Statement", period_line)
    if style == "signed":
        top = _horizontal_summary(pen, statement, top)
    else:
        top = _vertical_summary(pen, statement, top, counts=counts)
    if style == "two_column":
        _rows_two_column(pen, statement, top, fmt)
    elif style == "signed":
        _rows_signed(pen, statement, top, fmt)
    elif style == "sectioned":
        _rows_sectioned(pen, statement, top, fmt)
    elif style == "money_in_out":
        _rows_money_in_out(pen, statement, top)
    pen.save()
    return target.getvalue()


def truth(statement: Statement, *, counts: bool, notes: str) -> dict:
    summary: dict[str, object] = {
        "beginning_balance": float(statement.opening),
        "ending_balance": float(statement.closing),
        "deposits_total": float(statement.deposits_total),
        "withdrawals_total": float(statement.withdrawals_total),
    }
    if counts:
        summary["deposits_count"] = len(statement.deposits)
        summary["withdrawals_count"] = len(statement.withdrawals)
    return {
        "provenance": {
            "summary": "exact by construction — the generator laid these rows out",
            "generator": "evals/fixtures/generate.py, deterministic from a fixed seed",
        },
        "account": {
            "bank": statement.bank,
            "account_last4": statement.last4,
            "period": {
                "start": statement.start.isoformat(),
                "end": statement.end.isoformat(),
            },
        },
        "summary": summary,
        "periods": 1,
        "transactions_count": len(statement.rows),
        "notes": notes,
    }


FIXTURES = (
    (
        "gen_01_two_column_text",
        dict(seed=11, bank="Kestrel Commerce Bank", last4="7310",
             start=date(2025, 3, 1), days=31, count=46, opening="184203.55"),
        dict(style="two_column", counts=True),
        None,
        "Text layer, two amount columns, printed counts. The plainest shape there "
        "is: everything the parser wants is on the page, so a failure here is the "
        "parser's alone.",
    ),
    (
        "gen_02_signed_column_text",
        dict(seed=23, bank="Halloway Trust", last4="5182",
             start=date(2025, 6, 1), days=30, count=38, opening="42980.10"),
        dict(style="signed", counts=False),
        None,
        "Text layer, one signed amount column, and a HORIZONTAL summary block — "
        "labels in one row, amounts in the next. No counts are printed, so both "
        "count checks must report `unavailable` rather than `ok`.",
    ),
    (
        "gen_03_sections_text",
        dict(seed=31, bank="Pinebrook Savings", last4="9024",
             start=date(2025, 9, 1), days=30, count=34, opening="7715.42"),
        dict(style="sectioned", counts=True),
        None,
        "Text layer, NO side column at all: a heading above each run of rows is "
        "the only thing that says which side they are on (SPEC §7.6).",
    ),
    (
        "gen_04_two_column_scan",
        dict(seed=47, bank="Aldergate Bank", last4="6641",
             start=date(2025, 1, 1), days=31, count=42, opening="311544.09"),
        dict(style="two_column", counts=True),
        dict(dpi=200, angle=0.0, blur=0.0, noise=0),
        "The same two-column shape as a clean scan: no text layer, so ingest goes "
        "through OCR and every number arrives through it.",
    ),
    (
        "gen_05_two_column_scan_tilted",
        dict(seed=53, bank="Wrenfield National", last4="2288",
             start=date(2025, 2, 1), days=28, count=40, opening="96210.73"),
        dict(style="two_column", counts=True),
        dict(dpi=150, angle=-1.4, blur=0.6, noise=4),
        "A sheet fed off square: 150 DPI, 1.4 degrees, soft focus and grain. The "
        "tilt is what breaks a page into lines that do not exist (SPEC §7.2).",
    ),
    (
        "gen_06_money_in_out_text",
        dict(seed=67, bank="Thornbury Building Society", last4="4417",
             start=date(2025, 4, 1), days=30, count=30, opening="18455.20",
             currency="£"),
        dict(style="money_in_out", counts=False),
        None,
        "The British shape: `Money out` before `Money in`, dates written "
        "`01 Apr 25`, and sterling. Nothing in the parser may assume a dollar or "
        "an American date.",
    ),
)


def main() -> None:
    SAMPLES.mkdir(parents=True, exist_ok=True)
    EXPECTED.mkdir(parents=True, exist_ok=True)
    for name, spec, style, degrade, notes in FIXTURES:
        statement = build(**spec)
        pdf = render(statement, **style)
        if degrade is not None:
            pdf = scanned(pdf, **degrade)
        (SAMPLES / f"{name}.pdf").write_bytes(pdf)
        expected = truth(statement, counts=style["counts"], notes=notes)
        (EXPECTED / f"{name}.json").write_text(json.dumps(expected, indent=2) + "\n")
        print(
            f"{name:32} {len(statement.rows):3} rows  "
            f"{'scan' if degrade else 'text':4}  {len(pdf) // 1024:5} KB"
        )


if __name__ == "__main__":
    main()
