"""Graph nodes. Each node is a pure function of (state, deps) → partial state update + a trace entry."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Protocol

from rag.graph.reasoner import Reasoner
from rag.graph.state import RAGState
from rag.models import Citation, ScoredChunk, SqlResult
from rag.observability import Timer
from rag.retrieval.hybrid import RetrievalResult

log = logging.getLogger(__name__)

ABSTAIN_ANSWER = "I don't have enough information in the available sources to answer that question."
_CITE_RE = re.compile(r"\[((?:S|SQL)\d+)\]")


class Retriever(Protocol):
    def retrieve(
        self, query: str, top_k: int | None = None, candidate_k: int | None = None
    ) -> RetrievalResult: ...


class SqlTool(Protocol):
    def run(self, question: str) -> SqlResult: ...


@dataclass
class GraphDeps:
    reasoner: Reasoner
    retriever: Retriever
    sql_tool: SqlTool
    catalog_summary: str
    max_rewrites: int = 2
    max_regenerations: int = 1
    default_top_k: int = 6


def _trace(node: str, timer: Timer, **info: Any) -> list[dict[str, Any]]:
    return [{"node": node, "ms": round(timer.ms, 1), **info}]


# ---- nodes ------------------------------------------------------------------------------------------
def route(state: RAGState, deps: GraphDeps) -> dict[str, Any]:
    t = Timer()
    decision = deps.reasoner.route(state["question"], deps.catalog_summary)
    return {
        "route": decision.route,
        "route_reason": decision.reason,
        "query": state["question"],
        "rewrites": 0,
        "generations": 0,
        "sql_result": None,
        "candidates": [],
        "retrieved": [],
        "trace": _trace("route", t, route=decision.route, reason=decision.reason),
    }


def retrieve(state: RAGState, deps: GraphDeps) -> dict[str, Any]:
    t = Timer()
    query = state.get("query") or state["question"]
    res = deps.retriever.retrieve(query, top_k=state.get("top_k") or deps.default_top_k)
    return {
        "candidates": res.candidates,
        "retrieved": res.results,
        "retrieval_timings": res.timings_ms,
        "trace": _trace(
            "retrieve",
            t,
            query=query,
            candidates=len(res.candidates),
            results=len(res.results),
            timings_ms=res.timings_ms,
            reranker=res.reranker,
        ),
    }


def run_sql(state: RAGState, deps: GraphDeps) -> dict[str, Any]:
    t = Timer()
    result = deps.sql_tool.run(state["question"])
    return {
        "sql_result": result,
        "trace": _trace(
            "run_sql", t, ok=result.ok, attempts=result.attempts, rows=result.row_count, error=result.error
        ),
    }


def grade(state: RAGState, deps: GraphDeps) -> dict[str, Any]:
    t = Timer()
    retrieved = state.get("retrieved") or []
    if not retrieved:
        return {"relevant": [], "trace": _trace("grade", t, graded=0, relevant=0)}
    flags = deps.reasoner.grade(state["question"], retrieved)
    relevant = [c for c, ok in zip(retrieved, flags, strict=False) if ok]
    return {"relevant": relevant, "trace": _trace("grade", t, graded=len(retrieved), relevant=len(relevant))}


def rewrite(state: RAGState, deps: GraphDeps) -> dict[str, Any]:
    t = Timer()
    attempt = state.get("rewrites", 0) + 1
    new_query = deps.reasoner.rewrite(state["question"], state.get("query") or state["question"], attempt)
    return {
        "query": new_query,
        "rewrites": attempt,
        "trace": _trace("rewrite", t, attempt=attempt, query=new_query),
    }


def build_sources(relevant: list[ScoredChunk], sql: SqlResult | None) -> tuple[str, dict[str, Citation]]:
    blocks: list[str] = []
    cites: dict[str, Citation] = {}
    for i, sc in enumerate(relevant, start=1):
        c = sc.chunk
        label = f"S{i}"
        where = (
            f"{c.title}" + (f" > {c.section}" if c.section else "") + (f" (page {c.page})" if c.page else "")
        )
        blocks.append(f"[{label}] {where}\n{c.text}")
        cites[label] = Citation(
            label=label,
            kind="document",
            source_uri=c.source_uri,
            title=c.title,
            section=c.section or None,
            page=c.page,
            snippet=c.text[:300],
            score=round(sc.score, 4),
            chunk_id=c.chunk_id,
        )
    if sql is not None and sql.ok:
        blocks.append(f"[SQL1] BigQuery result for: {sql.sql}\n{sql.as_markdown()}")
        cites["SQL1"] = Citation(label="SQL1", kind="sql", sql=sql.sql, snippet=sql.as_markdown(10)[:300])
    return "\n\n".join(blocks), cites


def validate_citations(answer: str, available: dict[str, Citation]) -> tuple[str, list[Citation]]:
    """Strip markers that don't refer to provided sources; return cited sources in first-use order."""
    used: list[str] = []

    def keep(m: re.Match[str]) -> str:
        label = m.group(1)
        if label in available:
            if label not in used:
                used.append(label)
            return m.group(0)
        return ""

    cleaned = _CITE_RE.sub(keep, answer)
    cleaned = re.sub(r"[ \t]+([.,;:])", r"\1", re.sub(r"[ \t]{2,}", " ", cleaned)).strip()
    return cleaned, [available[label] for label in used]


def generate(state: RAGState, deps: GraphDeps) -> dict[str, Any]:
    t = Timer()
    relevant = state.get("relevant") or []
    sql = state.get("sql_result")
    sources, available = build_sources(relevant, sql)
    generations = state.get("generations", 0) + 1
    if not available:
        return {
            "answer": ABSTAIN_ANSWER,
            "insufficient_context": True,
            "citations": [],
            "sources_text": "",
            "generations": generations,
            "trace": _trace("generate", t, mode="abstain"),
        }
    out = deps.reasoner.generate(state["question"], sources, state.get("grounding_feedback"))
    answer, citations = validate_citations(out.answer, available)
    insufficient = out.insufficient_context or not answer
    if not answer:
        answer = ABSTAIN_ANSWER
    return {
        "answer": answer,
        "insufficient_context": insufficient,
        "citations": citations,
        "sources_text": sources,
        "generations": generations,
        "trace": _trace(
            "generate",
            t,
            mode="answer",
            sources=len(available),
            cited=len(citations),
            insufficient=insufficient,
        ),
    }


def check_grounding(state: RAGState, deps: GraphDeps) -> dict[str, Any]:
    t = Timer()
    if state.get("insufficient_context") or not state.get("sources_text"):
        return {
            "grounded": True,
            "unsupported_claims": [],
            "grounding_feedback": None,
            "trace": _trace("check_grounding", t, skipped=True),
        }
    verdict = deps.reasoner.check_grounding(state["question"], state["answer"], state["sources_text"])
    feedback = "\n".join(f"- {c}" for c in verdict.unsupported_claims) if not verdict.grounded else None
    return {
        "grounded": verdict.grounded,
        "unsupported_claims": verdict.unsupported_claims,
        "grounding_feedback": feedback or ("- (unspecified)" if not verdict.grounded else None),
        "trace": _trace(
            "check_grounding", t, grounded=verdict.grounded, unsupported=len(verdict.unsupported_claims)
        ),
    }


# ---- conditional edges ----------------------------------------------------------------------------
def after_route(state: RAGState) -> list[str]:
    r = state["route"]
    if r == "sql":
        return ["run_sql"]
    if r == "both":
        return ["retrieve", "run_sql"]  # same superstep → parallel; fan-in at grade
    return ["retrieve"]


def after_grade(state: RAGState, deps: GraphDeps) -> str:
    sql = state.get("sql_result")
    if state.get("relevant") or (sql is not None and sql.ok):
        return "generate"
    if state["route"] in ("vector", "both") and state.get("rewrites", 0) < deps.max_rewrites:
        return "rewrite"
    return "generate"  # abstain mode (no sources)


def after_grounding(state: RAGState, deps: GraphDeps) -> str:
    if not state.get("grounded", True) and state.get("generations", 0) <= deps.max_regenerations:
        return "generate"
    return "end"
