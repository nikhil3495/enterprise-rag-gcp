"""Request / response schemas for the public API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from rag.models import Citation


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000, description="Natural-language question")
    top_k: int | None = Field(None, ge=1, le=20, description="Number of reranked passages to use")
    debug: bool = Field(False, description="Include retrieved passages and the graph trace")


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    cost_usd: float = 0.0
    output_tokens_per_s: float | None = None


class SqlInfo(BaseModel):
    query: str
    ok: bool
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0
    attempts: int = 0
    error: str | None = None


class RetrievedPassage(BaseModel):
    chunk_id: str
    source_uri: str
    title: str
    section: str | None = None
    page: int | None = None
    score: float
    retrieval_score: float | None = None
    rerank_score: float | None = None
    text: str


class QueryResponse(BaseModel):
    answer: str
    citations: list[Citation]
    route: str
    sql: SqlInfo | None = None
    grounded: bool
    insufficient_context: bool
    usage: Usage
    latency_ms: float
    request_id: str
    retrieved: list[RetrievedPassage] | None = None
    candidates: list[RetrievedPassage] | None = None
    trace: list[dict[str, Any]] | None = None
    context: str | None = None


class ErrorResponse(BaseModel):
    error: str
    detail: str
    request_id: str | None = None
