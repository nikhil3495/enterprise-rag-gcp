"""Vertex AI Vector Search (hybrid: dense + sparse, RRF) adapter.

Index: STREAM_UPDATE, 768 dims, DOT_PRODUCT_DISTANCE + UNIT_L2_NORM (see infra/terraform/vector_search.tf).
Vector Search returns datapoint IDs only; chunk text lives in the docstore.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from rag.models import SparseVector
from rag.store.base import Neighbor, VectorRecord, batched

log = logging.getLogger(__name__)


class VertexVectorStore:
    name = "vertex"

    def __init__(
        self,
        *,
        project: str,
        region: str,
        index_id: str,
        endpoint_id: str,
        deployed_index_id: str,
        batch_size: int = 100,
        index: Any = None,
        endpoint: Any = None,
    ) -> None:
        self.deployed_index_id = deployed_index_id
        self.batch_size = batch_size
        if index is None or endpoint is None:
            from google.cloud import aiplatform

            aiplatform.init(project=project, location=region)
            index = index or aiplatform.MatchingEngineIndex(index_name=index_id)
            endpoint = endpoint or aiplatform.MatchingEngineIndexEndpoint(index_endpoint_name=endpoint_id)
        self._index = index
        self._endpoint = endpoint

    @staticmethod
    def _datapoint(r: VectorRecord) -> Any:
        from google.cloud.aiplatform_v1.types import IndexDatapoint

        return IndexDatapoint(
            datapoint_id=r.id,
            feature_vector=[float(x) for x in np.asarray(r.dense).reshape(-1)],
            sparse_embedding=IndexDatapoint.SparseEmbedding(
                values=list(r.sparse.values), dimensions=list(r.sparse.dimensions)
            ),
        )

    def upsert(self, records: list[VectorRecord]) -> None:
        for batch in batched(records, self.batch_size):
            self._index.upsert_datapoints(datapoints=[self._datapoint(r) for r in batch])
        log.info("vector search upsert", extra={"datapoints": len(records)})

    def remove(self, ids: list[str]) -> None:
        for batch in batched(ids, self.batch_size):
            self._index.remove_datapoints(datapoint_ids=batch)

    def query(self, dense: np.ndarray, sparse: SparseVector, k: int, alpha: float) -> list[Neighbor]:
        from google.cloud.aiplatform.matching_engine.matching_engine_index_endpoint import HybridQuery

        kwargs: dict[str, Any] = {"dense_embedding": [float(x) for x in np.asarray(dense).reshape(-1)]}
        if sparse.dimensions:  # hybrid query: dense + sparse fused with RRF
            kwargs |= {
                "sparse_embedding_values": list(sparse.values),
                "sparse_embedding_dimensions": list(sparse.dimensions),
                "rrf_ranking_alpha": alpha,
            }
        q = HybridQuery(**kwargs)
        results = self._endpoint.find_neighbors(
            deployed_index_id=self.deployed_index_id, queries=[q], num_neighbors=k
        )
        neighbors = results[0] if results else []
        out: list[Neighbor] = []
        n = len(neighbors)
        for rank, nb in enumerate(neighbors):
            # With RRF the service returns fused order; we keep a rank-derived score for downstream use.
            out.append(
                Neighbor(
                    id=str(nb.id),
                    score=float(n - rank) / n,
                    dense_score=float(nb.distance) if nb.distance is not None else None,
                    sparse_score=float(nb.sparse_distance) if getattr(nb, "sparse_distance", None) else None,
                )
            )
        return out

    def count(self) -> int | None:
        try:
            self._index._sync_gca_resource()
            stats = self._index.gca_resource.index_stats
            return int(stats.vectors_count)
        except Exception:  # pragma: no cover - best effort
            return None

    def persist(self) -> None:
        """Streaming updates are durable on write; nothing to flush."""
