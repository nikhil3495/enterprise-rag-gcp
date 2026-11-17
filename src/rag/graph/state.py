"""Graph state (TypedDict). ``trace`` uses an ``operator.add`` reducer so parallel branches append."""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from rag.models import Citation, Route, ScoredChunk, SqlResult


class RAGState(TypedDict, total=False):
    question: str
    top_k: int
    route: Route
    route_reason: str
    query: str  # current retrieval query (rewritten by the self-correction loop)
    rewrites: int
    candidates: list[ScoredChunk]  # fused hybrid candidates before rerank
    retrieved: list[ScoredChunk]  # after rerank
    relevant: list[ScoredChunk]  # after relevance grading
    retrieval_timings: dict[str, float]
    sql_result: SqlResult | None
    answer: str
    insufficient_context: bool
    citations: list[Citation]
    sources_text: str
    grounded: bool
    unsupported_claims: list[str]
    grounding_feedback: str | None
    generations: int
    trace: Annotated[list[dict[str, Any]], operator.add]
