# One image, built from this repository, running the interface of SPEC §9.
# There is no frontend build step: the page ships inside the package.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

# The wheels are needed once, to install them. Kept, uv's cache is a second
# copy of every dependency inside the image — 375 MB of it, measured.
ENV UV_NO_CACHE=1

# ONNX Runtime sizes its thread pool from the *host's* core count rather than
# the container's CPU limit, with a separate allocation arena per thread, so
# an unpinned OCR run costs memory in proportion to whichever machine it
# landed on. One thread per container: it changes timing, never a reading.
ENV OMP_NUM_THREADS=1

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY src/ src/
RUN uv sync --frozen --no-dev

# The OCR weights ship inside rapidocr-onnxruntime, so a scanned statement is
# read with no network call at run time (ADR-005). Loading the engine here
# proves that at build time rather than on a reviewer's first upload.
RUN uv run --no-sync python -c "from statement_tieout.pdf.ocr import _engine; _engine()"

EXPOSE 8000
CMD ["uv", "run", "--no-sync", "uvicorn", "statement_tieout.web.app:app", "--host", "0.0.0.0", "--port", "8000"]
