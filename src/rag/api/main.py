"""FastAPI application factory.

Endpoints: ``GET /healthz`` (liveness), ``GET /readyz`` (components loaded), ``GET /v1/info``,
``POST /v1/query``. The graph runs synchronously in Starlette's threadpool. Upstream failures map to 503
with the request ID; stack traces are never returned.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from rag import __version__
from rag.api.auth import Authenticator, Principal, TokenVerifier
from rag.api.middleware import RequestContextMiddleware
from rag.api.schemas import QueryRequest, QueryResponse, RetrievedPassage, SqlInfo, Usage
from rag.config import Settings
from rag.container import Container
from rag.graph.service import QueryResult, RAGService
from rag.models import ScoredChunk
from rag.observability import configure_logging, request_id_var

log = logging.getLogger("rag.api")


def _passage(sc: ScoredChunk) -> RetrievedPassage:
    c = sc.chunk
    return RetrievedPassage(
        chunk_id=c.chunk_id,
        source_uri=c.source_uri,
        title=c.title,
        section=c.section or None,
        page=c.page,
        score=round(sc.score, 6),
        retrieval_score=round(sc.retrieval_score, 6),
        rerank_score=round(sc.rerank_score, 6) if sc.rerank_score is not None else None,
        text=c.text,
    )


def to_response(r: QueryResult, debug: bool) -> QueryResponse:
    sql = None
    if r.sql is not None and (r.sql.sql or r.sql.error):
        sql = SqlInfo(
            query=r.sql.sql,
            ok=r.sql.ok,
            columns=r.sql.columns,
            rows=r.sql.rows[:50],
            row_count=r.sql.row_count,
            attempts=r.sql.attempts,
            error=r.sql.error,
        )
    return QueryResponse(
        answer=r.answer,
        citations=r.citations,
        route=r.route,
        sql=sql,
        grounded=r.grounded,
        insufficient_context=r.insufficient_context,
        usage=Usage(**{k: r.usage[k] for k in Usage.model_fields}),
        latency_ms=r.latency_ms,
        request_id=r.request_id,
        retrieved=[_passage(x) for x in r.retrieved] if debug else None,
        candidates=[_passage(x) for x in r.candidates] if debug else None,
        trace=r.trace if debug else None,
        context=r.context if debug else None,
    )


def _error(status: int, error: str, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status, content={"error": error, "detail": detail, "request_id": request_id_var.get()}
    )


_ERROR_CODES = {401: "unauthenticated", 403: "forbidden", 404: "not_found", 503: "upstream_unavailable"}


def create_app(
    settings: Settings | None = None,
    *,
    service: RAGService | None = None,
    container: Container | None = None,
    verifier: TokenVerifier | None = None,
) -> FastAPI:
    settings = settings or Settings()
    configure_logging(settings.log_level)
    authenticator = Authenticator(settings, verifier)
    state: dict[str, Any] = {"service": service, "container": container, "ready": service is not None}

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if state["service"] is None:
            c = state["container"] or Container(settings)
            state["container"] = c
            state["service"] = await run_in_threadpool(lambda: c.service)
            state["ready"] = True
            log.info("service ready", extra=c.info())
        yield

    app = FastAPI(
        title="Enterprise RAG Assistant",
        version=__version__,
        lifespan=lifespan,
        description="LangGraph RAG over Vertex AI Vector Search + BigQuery text-to-SQL with Gemini.",
    )
    app.add_middleware(RequestContextMiddleware, project=settings.gcp_project)

    @app.exception_handler(HTTPException)
    async def http_exc(_: Request, exc: HTTPException) -> JSONResponse:
        resp = _error(exc.status_code, _ERROR_CODES.get(exc.status_code, "error"), str(exc.detail))
        if exc.headers:
            resp.headers.update(exc.headers)
        return resp

    @app.exception_handler(RequestValidationError)
    async def validation_exc(_: Request, exc: RequestValidationError) -> JSONResponse:
        errs = [f"{'.'.join(str(p) for p in e['loc'][1:])}: {e['msg']}" for e in exc.errors()]
        return _error(422, "invalid_request", "; ".join(errs))

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:  # backstop; endpoints map their own
        log.exception("unhandled error", extra={"error_type": type(exc).__name__})
        return _error(503, "upstream_unavailable", "The assistant is temporarily unavailable. Retry later.")

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    async def readyz() -> JSONResponse:
        if not state["ready"]:
            return JSONResponse({"status": "starting"}, status_code=503)
        return JSONResponse({"status": "ready"})

    @app.get("/v1/info")
    async def info(_: Principal = Depends(authenticator)) -> dict[str, Any]:
        c: Container | None = state["container"]
        return {"version": __version__, **(c.info() if c else {"profile": settings.profile})}

    @app.post("/v1/query", response_model=QueryResponse, response_model_exclude_none=True)
    async def query(body: QueryRequest, principal: Principal = Depends(authenticator)) -> QueryResponse:
        svc: RAGService | None = state["service"]
        if svc is None:
            raise HTTPException(503, "service is starting")
        rid = request_id_var.get()
        try:
            result = await run_in_threadpool(
                lambda: svc.answer(body.question, top_k=body.top_k, request_id=rid)
            )
        except Exception as e:  # upstream (LLM / Vector Search / BigQuery) failure -> 503, no stack trace
            log.exception("query failed", extra={"error_type": type(e).__name__})
            raise HTTPException(503, "The assistant is temporarily unavailable. Retry later.") from e
        return to_response(result, body.debug)

    return app


# `uvicorn rag.api.main:app` — cheap to construct: heavy components load in the lifespan hook.
app = create_app()
