"""RAGService: runs the compiled graph for one question with usage tracking and emits the
``rag_request`` structured log line."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from rag.config import Settings
from rag.graph.builder import build_graph
from rag.graph.nodes import GraphDeps
from rag.models import Citation, ScoredChunk, SqlResult
from rag.observability import Timer, request_id_var
from rag.telemetry import track_usage

log = logging.getLogger("rag.request")


@dataclass
class QueryResult:
    request_id: str
    question: str
    answer: str
    route: str
    grounded: bool
    insufficient_context: bool
    citations: list[Citation]
    sql: SqlResult | None
    usage: dict[str, Any]
    latency_ms: float
    retrieved: list[ScoredChunk] = field(default_factory=list)
    candidates: list[ScoredChunk] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)
    unsupported_claims: list[str] = field(default_factory=list)
    context: str = ""


class RAGService:
    def __init__(self, deps: GraphDeps, settings: Settings) -> None:
        self.deps = deps
        self.settings = settings
        self.graph = build_graph(deps)

    def answer(
        self, question: str, *, top_k: int | None = None, request_id: str | None = None
    ) -> QueryResult:
        rid = request_id or request_id_var.get() or uuid.uuid4().hex
        timer = Timer()
        with track_usage(self.settings) as tracker:
            state = self.graph.invoke(
                {"question": question, "top_k": top_k or self.settings.top_k, "trace": []},
                config={"recursion_limit": 40},
            )
        latency = round(timer.ms, 1)
        usage = tracker.summary().as_dict()
        sql = state.get("sql_result")
        result = QueryResult(
            request_id=rid,
            question=question,
            answer=state.get("answer", ""),
            route=state.get("route", "vector"),
            grounded=bool(state.get("grounded", False)),
            insufficient_context=bool(state.get("insufficient_context", False)),
            citations=state.get("citations", []),
            sql=sql,
            usage=usage,
            latency_ms=latency,
            retrieved=state.get("retrieved", []),
            candidates=state.get("candidates", []),
            trace=state.get("trace", []),
            unsupported_claims=state.get("unsupported_claims", []),
            context=state.get("sources_text", ""),
        )
        log.info(
            "rag_request",
            extra={
                "event": "rag_request",
                "request_id": rid,
                "latency_ms": latency,
                "route": result.route,
                "grounded": result.grounded,
                "insufficient_context": result.insufficient_context,
                "input_tokens": usage["input_tokens"],
                "output_tokens": usage["output_tokens"],
                "thinking_tokens": usage["thinking_tokens"],
                "cost_usd": usage["cost_usd"],
                "output_tokens_per_s": usage["output_tokens_per_s"],
                "rewrites": state.get("rewrites", 0),
                "sql_ok": bool(sql and sql.ok),
                "citations": len(result.citations),
            },
        )
        return result
