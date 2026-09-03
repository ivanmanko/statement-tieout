"""The HTTP layer (SPEC §9).

It presents `ExtractResult` and computes nothing of its own, so what is tested
here is the contract around it: what comes back, what is refused, and that the
uploaded statement never reaches disk.

No sample PDF: the extractor is injected.
"""

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from statement_tieout.schema import (
    Account,
    DateRange,
    Extraction,
    ExtractResult,
    PeriodResult,
    Reconciliation,
    Summary,
    Transaction,
)
from statement_tieout.web.app import ExtractionError, analyse_pdf, create_app


def a_result():
    period = PeriodResult(
        account=Account(bank="ACME BANK", account_last4="4071",
                        period=DateRange(start=date(2025, 1, 1), end=date(2025, 1, 31))),
        summary=Summary(
            beginning_balance=Decimal("1000.00"), ending_balance=Decimal("1250.00"),
            deposits_total=Decimal("300.00"), deposits_count=2,
            withdrawals_total=Decimal("50.00"), withdrawals_count=1,
            printed_fields={"beginning_balance", "ending_balance", "deposits_total",
                            "deposits_count", "withdrawals_total", "withdrawals_count"},
        ),
        transactions=[
            Transaction(date=date(2025, 1, 1), description="A", deposit=Decimal("100.00"),
                        page=1, line=0),
            Transaction(date=date(2025, 1, 2), description="B", deposit=Decimal("200.00"),
                        page=1, line=1),
            Transaction(date=date(2025, 1, 3), description="C", withdrawal=Decimal("50.00"),
                        page=2, line=0),
        ],
        reconciliation=Reconciliation.reconciled_on({"balance_equation"}),
    )
    return ExtractResult.from_periods([period], Extraction())


@pytest.fixture
def client():
    app = create_app()
    app.dependency_overrides[analyse_pdf] = lambda: lambda data, name: a_result()
    return TestClient(app)


class TestThePage:
    def test_the_root_serves_the_interface(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_it_names_the_product(self, client):
        assert "Statement Tie-Out" in client.get("/").text

    def test_health_answers(self, client):
        assert client.get("/health").json() == {"status": "ok"}


class TestAnalyse:
    def test_it_returns_both_the_extraction_and_the_audit_view(self, client):
        response = client.post("/analyse", files={"file": ("s.pdf", b"%PDF-1.4", "application/pdf")})
        body = response.json()
        assert response.status_code == 200
        assert body["result"]["summary"]["beginning_balance"] == 1000.0
        assert body["audit"]["verdict"] == "tied"

    def test_the_audit_view_carries_page_references(self, client):
        body = client.post("/analyse",
                           files={"file": ("s.pdf", b"%PDF-1.4", "application/pdf")}).json()
        assert body["result"]["transactions"][2]["page"] == 2

    def test_the_file_name_comes_back(self, client):
        body = client.post("/analyse",
                           files={"file": ("April.pdf", b"%PDF-1.4", "application/pdf")}).json()
        assert body["filename"] == "April.pdf"


class TestRefusals:
    def test_a_missing_file_is_a_422(self, client):
        assert client.post("/analyse").status_code == 422

    def test_something_that_is_not_a_pdf_is_refused_with_a_reason(self, client):
        response = client.post("/analyse",
                               files={"file": ("notes.txt", b"hello", "text/plain")})
        assert response.status_code == 400
        assert "PDF" in response.json()["detail"]

    def test_an_unreadable_pdf_reports_what_went_wrong(self):
        app = create_app()

        def broken():
            def _raise(data, name):
                raise ExtractionError("could not read it")
            return _raise

        app.dependency_overrides[analyse_pdf] = broken
        response = TestClient(app).post(
            "/analyse", files={"file": ("s.pdf", b"%PDF-1.4", "application/pdf")}
        )
        assert response.status_code == 422
        assert "could not read it" in response.json()["detail"]

    def test_an_oversized_upload_is_refused(self, client):
        from statement_tieout.web.app import MAX_UPLOAD_BYTES

        response = client.post(
            "/analyse",
            files={"file": ("big.pdf", b"%PDF" + b"0" * MAX_UPLOAD_BYTES, "application/pdf")},
        )
        assert response.status_code == 413


class TestClientData:
    """SPEC §9 — statements are client data and never touch disk."""

    def test_the_upload_is_never_written_to_a_file(self, client, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        client.post("/analyse", files={"file": ("s.pdf", b"%PDF-1.4", "application/pdf")})
        assert list(tmp_path.iterdir()) == []
