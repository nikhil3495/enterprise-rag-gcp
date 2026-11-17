"""In-memory hybrid vector index (numpy) with Reciprocal Rank Fusion.

Mirrors Vertex AI Vector Search hybrid semantics: dense and sparse candidate lists are fused with
RRF where ``alpha`` weights the dense ranking (1.0 = dense only, 0.0 = sparse only).
Snapshots to a local path or GCS so it doubles as the low-cost production fallback
(``RAG_VECTOR_BACKEND=memory``).
"""

from __future__ import annotations

import json
import logging
import threading

import numpy as np

from rag import storage
from rag.models import SparseVector
from rag.store.base import Neighbor, VectorRecord

log = logging.getLogger(__name__)

RRF_K = 60


def rrf_fuse(dense: list[str], sparse: list[str], alpha: float, k: int = RRF_K) -> dict[str, float]:
    scores: dict[str, float] = {}
    for rank, doc_id in enumerate(dense, start=1):
        scores[doc_id] = scores.get(doc_id, 0.0) + alpha / (k + rank)
    for rank, doc_id in enumerate(sparse, start=1):
        scores[doc_id] = scores.get(doc_id, 0.0) + (1.0 - alpha) / (k + rank)
    return scores


class MemoryVectorStore:
    name = "memory"

    def __init__(self, dim: int, snapshot_uri: str | None = None) -> None:
        self.dim = dim
        self.snapshot_uri = snapshot_uri
        self._lock = threading.RLock()
        self._ids: list[str] = []
        self._pos: dict[str, int] = {}
        self._dense = np.zeros((0, dim), dtype=np.float32)
        self._sparse: list[dict[int, float]] = []
        if snapshot_uri and storage.exists(snapshot_uri):
            self._load(snapshot_uri)

    # ---- mutation -------------------------------------------------------------------------
    def upsert(self, records: list[VectorRecord]) -> None:
        with self._lock:
            base = len(self._ids)
            new_rows: list[np.ndarray] = []
            for r in records:
                vec = np.asarray(r.dense, dtype=np.float32).reshape(-1)
                if vec.shape[0] != self.dim:
                    raise ValueError(f"dense dim {vec.shape[0]} != index dim {self.dim}")
                sparse = dict(zip(r.sparse.dimensions, r.sparse.values, strict=True))
                i = self._pos.get(r.id)
                if i is None:
                    self._pos[r.id] = len(self._ids)
                    self._ids.append(r.id)
                    self._sparse.append(sparse)
                    new_rows.append(vec)
                elif i >= base:  # duplicate id within this batch
                    new_rows[i - base] = vec
                    self._sparse[i] = sparse
                else:
                    self._dense[i] = vec
                    self._sparse[i] = sparse
            if new_rows:
                self._dense = np.vstack([self._dense, np.stack(new_rows)])

    def remove(self, ids: list[str]) -> None:
        with self._lock:
            drop = {self._pos[i] for i in ids if i in self._pos}
            if not drop:
                return
            keep = [i for i in range(len(self._ids)) if i not in drop]
            self._ids = [self._ids[i] for i in keep]
            self._dense = self._dense[keep] if keep else np.zeros((0, self.dim), dtype=np.float32)
            self._sparse = [self._sparse[i] for i in keep]
            self._pos = {cid: i for i, cid in enumerate(self._ids)}

    # ---- query ------------------------------------------------------------------------------
    def query(self, dense: np.ndarray, sparse: SparseVector, k: int, alpha: float) -> list[Neighbor]:
        with self._lock:
            n = len(self._ids)
            if n == 0:
                return []
            dense_scores = self._dense @ np.asarray(dense, dtype=np.float32).reshape(-1)
            q = dict(zip(sparse.dimensions, sparse.values, strict=True))
            sparse_scores = np.array(
                [sum(v * row.get(d, 0.0) for d, v in q.items()) for row in self._sparse], dtype=np.float32
            )
            kk = min(k, n)
            dense_rank = [self._ids[i] for i in np.argsort(-dense_scores, kind="stable")[:kk]]
            sparse_order = [i for i in np.argsort(-sparse_scores, kind="stable")[:kk] if sparse_scores[i] > 0]
            sparse_rank = [self._ids[i] for i in sparse_order]
            fused = rrf_fuse(dense_rank, sparse_rank, alpha)
            ranked = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:k]
            return [
                Neighbor(
                    id=cid,
                    score=score,
                    dense_score=float(dense_scores[self._pos[cid]]),
                    sparse_score=float(sparse_scores[self._pos[cid]]),
                )
                for cid, score in ranked
            ]

    def count(self) -> int:
        return len(self._ids)

    # ---- persistence -------------------------------------------------------------------------
    def persist(self) -> None:
        if not self.snapshot_uri:
            return
        with self._lock:
            payload = {
                "dim": self.dim,
                "ids": self._ids,
                "dense": np.round(self._dense, 6).tolist(),
                "sparse": [[list(s.keys()), list(s.values())] for s in self._sparse],
            }
        storage.write_text(self.snapshot_uri, json.dumps(payload), "application/json")
        log.info("memory index snapshot written", extra={"uri": self.snapshot_uri, "vectors": len(self._ids)})

    def _load(self, uri: str) -> None:
        data = json.loads(storage.read_text(uri))
        if int(data["dim"]) != self.dim:
            raise ValueError(f"snapshot dim {data['dim']} != configured {self.dim}")
        self._ids = list(data["ids"])
        self._pos = {cid: i for i, cid in enumerate(self._ids)}
        self._dense = (
            np.asarray(data["dense"], dtype=np.float32).reshape(-1, self.dim)
            if self._ids
            else np.zeros((0, self.dim), dtype=np.float32)
        )
        self._sparse = [dict(zip(d, v, strict=True)) for d, v in data["sparse"]]
