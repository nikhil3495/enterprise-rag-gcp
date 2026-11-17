"""Hybrid retrieval: dense + BM25 sparse → RRF (in the vector store) → docstore fetch → rerank."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from rag import storage
from rag.models import ScoredChunk
from rag.observability import Timer
from rag.providers.base import Embedder, Reranker
from rag.retrieval.sparse import BM25SparseEncoder, SparseStats
from rag.store.base import DocStore, VectorStore

log = logging.getLogger(__name__)


@dataclass
class RetrievalResult:
    query: str
    candidates: list[ScoredChunk] = field(default_factory=list)  # fused order, before rerank
    results: list[ScoredChunk] = field(default_factory=list)  # after rerank (top_k)
    timings_ms: dict[str, float] = field(default_factory=dict)
    reranker: str = ""


class HybridRetriever:
    def __init__(
        self,
        *,
        embedder: Embedder,
        sparse: BM25SparseEncoder,
        vectors: VectorStore,
        docstore: DocStore,
        reranker: Reranker,
        candidate_k: int = 30,
        top_k: int = 6,
        alpha: float = 0.6,
        stats_uri: str | None = None,
    ) -> None:
        self.embedder = embedder
        self.sparse = sparse
        self.vectors = vectors
        self.docstore = docstore
        self.reranker = reranker
        self.candidate_k = candidate_k
        self.top_k = top_k
        self.alpha = alpha
        self.stats_uri = stats_uri
        self.reload_stats()

    def reload_stats(self) -> None:
        if self.stats_uri and storage.exists(self.stats_uri):
            self.sparse.stats = SparseStats.from_json(storage.read_text(self.stats_uri))
            log.info("sparse stats loaded", extra={"n_docs": self.sparse.stats.n_docs})

    def retrieve(
        self, query: str, top_k: int | None = None, candidate_k: int | None = None
    ) -> RetrievalResult:
        top_k = top_k or self.top_k
        candidate_k = candidate_k or self.candidate_k
        res = RetrievalResult(query=query, reranker=self.reranker.name)

        t = Timer()
        dense = self.embedder.embed_query(query)
        sparse = self.sparse.encode_query(query)
        res.timings_ms["embed"] = round(t.ms, 1)

        t = Timer()
        neighbors = self.vectors.query(dense, sparse, candidate_k, self.alpha)
        res.timings_ms["search"] = round(t.ms, 1)

        t = Timer()
        chunks = self.docstore.get_chunks([n.id for n in neighbors])
        res.timings_ms["fetch"] = round(t.ms, 1)
        res.candidates = [
            ScoredChunk(chunk=chunks[n.id], score=n.score, retrieval_score=n.score)
            for n in neighbors
            if n.id in chunks
        ]
        missing = len(neighbors) - len(res.candidates)
        if missing:
            log.warning("vector hits missing from docstore", extra={"missing": missing})

        t = Timer()
        res.results = self.reranker.rerank(query, res.candidates, top_k)
        res.timings_ms["rerank"] = round(t.ms, 1)
        return res
