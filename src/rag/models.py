"""Core domain models shared across ingestion, retrieval, SQL and the graph."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Route = Literal["vector", "sql", "both"]


class Chunk(BaseModel):
    chunk_id: str
    doc_id: str
    source_uri: str
    title: str
    section: str = ""
    text: str
    page: int | None = None
    index: int = 0
    token_count: int = 0

    @property
    def contextual_text(self) -> str:
        """Text that is embedded: contextual header + body."""
        header = f"{self.title} > {self.section}" if self.section else self.title
        return f"{header}\n{self.text}"

    @property
    def source_name(self) -> str:
        return self.source_uri.rstrip("/").rsplit("/", 1)[-1]


class ScoredChunk(BaseModel):
    chunk: Chunk
    score: float = 0.0
    retrieval_score: float = 0.0
    rerank_score: float | None = None


class DocumentRecord(BaseModel):
    """Per-document manifest entry used for idempotent sync."""

    doc_id: str
    source_uri: str
    title: str
    content_hash: str
    chunk_ids: list[str] = Field(default_factory=list)
    ingested_at: str = ""


class SparseVector(BaseModel):
    dimensions: list[int] = Field(default_factory=list)
    values: list[float] = Field(default_factory=list)


class SqlResult(BaseModel):
    ok: bool = False
    sql: str = ""
    explanation: str = ""
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0
    bytes_processed: int = 0
    attempts: int = 0
    error: str | None = None

    def as_markdown(self, max_rows: int = 50) -> str:
        if not self.columns:
            return "(no rows)"
        head = "| " + " | ".join(self.columns) + " |"
        sep = "| " + " | ".join("---" for _ in self.columns) + " |"
        body = ["| " + " | ".join(_fmt(v) for v in row) + " |" for row in self.rows[:max_rows]]
        more = [f"... ({self.row_count - max_rows} more rows)"] if self.row_count > max_rows else []
        return "\n".join([head, sep, *body, *more])


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


class Citation(BaseModel):
    label: str
    kind: Literal["document", "sql"] = "document"
    source_uri: str | None = None
    title: str | None = None
    section: str | None = None
    page: int | None = None
    snippet: str | None = None
    score: float | None = None
    sql: str | None = None
    chunk_id: str | None = None
