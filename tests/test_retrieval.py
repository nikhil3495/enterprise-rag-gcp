from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from rag.models import SparseVector
from rag.providers.offline import HashEmbedder, LexicalReranker, PassthroughReranker
from rag.retrieval.sparse import SPACE, BM25SparseEncoder, SparseStats, analyze, term_dim
from rag.store.base import VectorRecord
from rag.store.memory import MemoryVectorStore, rrf_fuse

from .conftest import scored


def test_sparse_doc_vector_is_bm25_tf_part() -> None:
    enc = BM25SparseEncoder(k1=1.2, b=0.75, ref_doc_len=10)
    v = enc.encode_document("password password length")
    terms = analyze("password password length")
    dl = len(terms)
    norm = 1.2 * (1 - 0.75 + 0.75 * dl / 10)
    expected_pw = 2 * 2.2 / (2 + norm)
    got = dict(zip(v.dimensions, v.values, strict=True))
    assert got[term_dim("password")] == pytest.approx(expected_pw, rel=1e-5)
    assert all(0 <= d < SPACE for d in v.dimensions)
    assert v.dimensions == sorted(v.dimensions)


def test_query_side_idf_and_dot_product_equals_bm25() -> None:
    enc = BM25SparseEncoder()
    docs = ["mfa required for admins", "password length fourteen", "password manager required"]
    vecs = [enc.encode_document(d) for d in docs]
    enc.stats = BM25SparseEncoder.compute_stats(vecs)
    assert enc.stats.n_docs == 3
    q = enc.encode_query("password length")
    idf_len = math.log(1 + (3 - 1 + 0.5) / (1 + 0.5))
    qd = dict(zip(q.dimensions, q.values, strict=True))
    assert qd[term_dim("length")] == pytest.approx(idf_len, rel=1e-5)
    scores = [sum(qd.get(d, 0) * v for d, v in zip(x.dimensions, x.values, strict=True)) for x in vecs]
    assert int(np.argmax(scores)) == 1  # rarer term "length" dominates


def test_stats_roundtrip_and_empty() -> None:
    s = SparseStats(n_docs=2, df={5: 1, 7: 2})
    assert SparseStats.from_json(s.to_json()) == s
    assert BM25SparseEncoder().encode_document("the and of") == SparseVector()


def test_hash_embedder_normalised_and_semantic_overlap() -> None:
    e = HashEmbedder(dim=256)
    m = e.embed_documents(["paid time off accrual", "network firewall rules"])
    assert m.shape == (2, 256)
    assert np.allclose(np.linalg.norm(m, axis=1), 1.0, atol=1e-5)
    q = e.embed_query("time off accrual rate")
    assert float(m[0] @ q) > float(m[1] @ q)
    assert e.embed_documents([]).shape == (0, 256)


def test_rrf_alpha_semantics() -> None:
    dense_only = rrf_fuse(["a", "b"], ["b", "a"], alpha=1.0)
    assert max(dense_only, key=dense_only.__getitem__) == "a"
    sparse_only = rrf_fuse(["a", "b"], ["b", "a"], alpha=0.0)
    assert max(sparse_only, key=sparse_only.__getitem__) == "b"


def _store(tmp: Path | None = None) -> tuple[MemoryVectorStore, BM25SparseEncoder, HashEmbedder]:
    emb = HashEmbedder(dim=64)
    enc = BM25SparseEncoder()
    store = MemoryVectorStore(dim=64, snapshot_uri=str(tmp / "idx.json") if tmp else None)
    texts = {
        "a": "multi factor authentication hardware keys",
        "b": "per diem tier one meals",
        "c": "postmortem within five business days",
    }
    vecs = {k: enc.encode_document(t) for k, t in texts.items()}
    enc.stats = BM25SparseEncoder.compute_stats(list(vecs.values()))
    store.upsert([VectorRecord(k, emb.embed_query(t), vecs[k]) for k, t in texts.items()])
    return store, enc, emb


def test_memory_store_hybrid_query_upsert_remove(tmp_path: Path) -> None:
    store, enc, emb = _store(tmp_path)
    q = "postmortem deadline business days"
    hits = store.query(emb.embed_query(q), enc.encode_query(q), k=3, alpha=0.5)
    assert hits[0].id == "c" and hits[0].sparse_score and hits[0].sparse_score > 0
    # upsert existing id replaces in place; duplicates in a batch keep the last one
    store.upsert(
        [
            VectorRecord("c", emb.embed_query("x"), SparseVector()),
            VectorRecord("d", emb.embed_query("y"), SparseVector()),
            VectorRecord("d", emb.embed_query("per diem"), SparseVector()),
        ]
    )
    assert store.count() == 4
    store.remove(["a", "zzz"])
    assert store.count() == 3
    store.persist()
    reloaded = MemoryVectorStore(dim=64, snapshot_uri=str(tmp_path / "idx.json"))
    assert reloaded.count() == 3
    assert {n.id for n in reloaded.query(emb.embed_query("per diem"), SparseVector(), 5, 1.0)} == {
        "b",
        "c",
        "d",
    }
    with pytest.raises(ValueError):
        store.upsert([VectorRecord("bad", np.zeros(3), SparseVector())])
    with pytest.raises(ValueError):
        MemoryVectorStore(dim=32, snapshot_uri=str(tmp_path / "idx.json"))


def test_memory_store_empty_and_remove_all() -> None:
    s = MemoryVectorStore(dim=4)
    assert s.query(np.ones(4), SparseVector(), 3, 0.5) == []
    s.upsert([VectorRecord("a", np.ones(4), SparseVector())])
    s.remove(["a"])
    assert s.count() == 0
    s.persist()  # no snapshot uri: no-op


def test_lexical_reranker_orders_by_query_overlap() -> None:
    cands = [
        scored("x-0001", "travel per diem tier", 0.9),
        scored("x-0002", "minimum password length is 14", 0.1),
    ]
    out = LexicalReranker().rerank("password length", cands, top_n=1)
    assert out[0].chunk.chunk_id == "x-0002" and out[0].rerank_score is not None
    assert LexicalReranker().rerank("q", [], 3) == []
    assert PassthroughReranker().rerank("q", cands, 1) == cands[:1]


def test_hybrid_retriever_end_to_end(ingested) -> None:  # type: ignore[no-untyped-def]
    res = ingested.retriever.retrieve("minimum password length", top_k=3)
    assert res.results and res.results[0].chunk.source_name == "information_security_policy.md"
    assert set(res.timings_ms) == {"embed", "search", "fetch", "rerank"}
    assert len(res.candidates) >= len(res.results)
