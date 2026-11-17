"""Vector store + document store protocols."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from rag.models import Chunk, DocumentRecord, SparseVector


@dataclass
class VectorRecord:
    id: str
    dense: np.ndarray
    sparse: SparseVector


@dataclass
class Neighbor:
    id: str
    score: float
    dense_score: float | None = None
    sparse_score: float | None = None


class VectorStore(Protocol):
    name: str

    def upsert(self, records: list[VectorRecord]) -> None: ...

    def remove(self, ids: list[str]) -> None: ...

    def query(self, dense: np.ndarray, sparse: SparseVector, k: int, alpha: float) -> list[Neighbor]: ...

    def count(self) -> int | None: ...

    def persist(self) -> None: ...


class DocStore(Protocol):
    name: str

    def put_chunks(self, chunks: list[Chunk]) -> None: ...

    def get_chunks(self, ids: list[str]) -> dict[str, Chunk]: ...

    def delete_chunks(self, ids: list[str]) -> None: ...

    def iter_chunks(self) -> Iterator[Chunk]: ...

    def put_document(self, record: DocumentRecord) -> None: ...

    def get_document(self, doc_id: str) -> DocumentRecord | None: ...

    def list_documents(self) -> list[DocumentRecord]: ...

    def delete_document(self, doc_id: str) -> None: ...

    def persist(self) -> None: ...


def batched[T](items: list[T], size: int) -> Iterable[list[T]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]
