"""One page, one upload, one workpaper (SPEC §9).

This layer **presents** `ExtractResult` and computes nothing of its own: every
figure it shows comes from the same structure the CLI prints, and a second
source of truth for any number would be the same defect as a hidden heuristic.

Uploaded statements are client data. They are held in memory for the duration
of the request and never written to disk.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from ..api import extract_result
from ..audit import audit
from ..pdf.loader import ExtractionError
from ..schema import ExtractResult

MAX_UPLOAD_BYTES = 80 * 1024 * 1024
"""Comfortably above the largest sample, which is a 53.8 MB scanned binder."""

PDF_MAGIC = b"%PDF"

PAGE = Path(__file__).with_name("page.html")


def analyse_pdf() -> Callable[[bytes, str], ExtractResult]:
    """The extractor, as a dependency so tests can supply their own."""

    def run(data: bytes, name: str) -> ExtractResult:
        return extract_result(data, name=name)

    return run


def create_app() -> FastAPI:
    app = FastAPI(
        title="Statement Tie-Out",
        description="Reconciles a bank statement against its own printed totals.",
        docs_url="/docs",
    )

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse(PAGE.read_text(encoding="utf-8"))

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/analyse")
    def analyse(
        file: UploadFile = File(...),
        extract: Callable[[bytes, str], ExtractResult] = Depends(analyse_pdf),
    ) -> JSONResponse:
        data = file.file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                413,
                f"The file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
            )
        if not data.startswith(PDF_MAGIC):
            raise HTTPException(
                400, "That is not a PDF — the file does not begin with %PDF."
            )

        try:
            result = extract(data, file.filename or "uploaded.pdf")
        except ExtractionError as error:
            raise HTTPException(422, str(error)) from error

        return JSONResponse(
            {
                "filename": file.filename,
                "result": result.model_dump(mode="json"),
                "audit": audit(result).model_dump(mode="json"),
            }
        )

    return app


app = create_app()
