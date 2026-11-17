# syntax=docker/dockerfile:1.7
# ---- build stage: resolve the locked environment with uv ----------------------------------------
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.8 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /src
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

# ---- runtime stage: slim, non-root ----------------------------------------------------------------
FROM python:3.12-slim
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
# schema.yaml + CSVs: catalog fallback and `rag seed-bigquery` (documents are ingested from GCS).
COPY data/structured /app/data/structured
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080 \
    RAG_STRUCTURED_DIR=/app/data/structured
USER app
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn rag.api.main:app --host 0.0.0.0 --port ${PORT} --no-access-log --timeout-graceful-shutdown 10"]
