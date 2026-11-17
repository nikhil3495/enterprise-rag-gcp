"""Provider protocols — every external dependency is behind one of these so the graph and pipeline
can run fully offline with fakes."""

from __future__ import annotations

from typing import Protocol, TypeVar

import numpy as np
from pydantic import BaseModel

from rag.models import ScoredChunk

T = TypeVar("T", bound=BaseModel)


class Embedder(Protocol):
    dim: int

    def embed_documents(self, texts: list[str]) -> np.ndarray: ...

    def embed_query(self, text: str) -> np.ndarray: ...


class Reranker(Protocol):
    name: str

    def rerank(self, query: str, candidates: list[ScoredChunk], top_n: int) -> list[ScoredChunk]: ...


class StructuredLLM(Protocol):
    """Minimal LLM surface used by the Gemini reasoner and the eval judge."""

    def generate_structured(
        self, *, role: str, name: str, system: str, prompt: str, schema: type[T], temperature: float = 0.0
    ) -> T: ...


def l2_normalize(m: np.ndarray) -> np.ndarray:
    m = np.asarray(m, dtype=np.float32)
    if m.ndim == 1:
        n = float(np.linalg.norm(m))
        return m / n if n > 0 else m
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return m / norms
