"""Vertex AI Ranking API reranker (``semantic-ranker-default-004``) with a lexical fallback."""

from __future__ import annotations

import logging
from typing import Any

from rag.models import ScoredChunk
from rag.observability import Timer
from rag.providers.base import Reranker
from rag.telemetry import current_tracker

log = logging.getLogger(__name__)


class VertexRanker:
    name = "vertex"

    def __init__(
        self, *, project: str, model: str = "semantic-ranker-default-004", client: Any = None
    ) -> None:
        if client is None:
            from google.cloud import discoveryengine_v1

            client = discoveryengine_v1.RankServiceClient()
        self.client = client
        self.model = model
        self.ranking_config = client.ranking_config_path(project, "global", "default_ranking_config")

    def rerank(self, query: str, candidates: list[ScoredChunk], top_n: int) -> list[ScoredChunk]:
        if not candidates:
            return []
        from google.cloud import discoveryengine_v1

        records = [
            discoveryengine_v1.RankingRecord(
                id=c.chunk.chunk_id,
                title=f"{c.chunk.title} > {c.chunk.section}" if c.chunk.section else c.chunk.title,
                content=c.chunk.text[:4000],
            )
            for c in candidates
        ]
        timer = Timer()
        resp = self.client.rank(
            request=discoveryengine_v1.RankRequest(
                ranking_config=self.ranking_config,
                model=self.model,
                top_n=top_n,
                query=query,
                records=records,
            ),
            timeout=15.0,
        )
        tracker = current_tracker()
        if tracker is not None:
            tracker.add_rerank(model=self.model, queries=1, latency_ms=timer.ms)
        by_id = {c.chunk.chunk_id: c for c in candidates}
        out = [
            by_id[r.id].model_copy(update={"rerank_score": float(r.score), "score": float(r.score)})
            for r in resp.records
            if r.id in by_id
        ]
        out.sort(key=lambda x: x.score, reverse=True)
        return out[:top_n]


class FallbackReranker:
    """Try the primary reranker; on any error degrade to the fallback (and log it)."""

    def __init__(self, primary: Reranker, fallback: Reranker) -> None:
        self.primary = primary
        self.fallback = fallback
        self.name = f"{primary.name}+fallback:{fallback.name}"
        self.last_used = primary.name

    def rerank(self, query: str, candidates: list[ScoredChunk], top_n: int) -> list[ScoredChunk]:
        try:
            out = self.primary.rerank(query, candidates, top_n)
            self.last_used = self.primary.name
            return out
        except Exception as e:
            log.warning("primary reranker failed; using fallback", extra={"error": str(e)[:300]})
            self.last_used = self.fallback.name
            return self.fallback.rerank(query, candidates, top_n)
