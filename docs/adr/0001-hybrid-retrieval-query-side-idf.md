# ADR 0001 — Hybrid retrieval with BM25 sparse vectors and query-side IDF

**Status:** accepted

## Context
Dense embeddings miss exact identifiers ("SEV2", "FIDO2", "Helios OS 4.4", policy IDs) that enterprise users
type. Vertex AI Vector Search supports hybrid queries that combine a dense and a sparse vector, fused with
Reciprocal Rank Fusion (`rrf_ranking_alpha`). We need a sparse representation that can be updated
incrementally with a `STREAM_UPDATE` index.

## Decision
* **Sparse space:** terms (lower-cased, stop-worded, lightly stemmed) are hashed with blake2b into
  2^24 dimensions. There is no vocabulary to persist or migrate.
* **Document side:** store only BM25's saturated, length-normalised term frequency,
  `tf·(k1+1) / (tf + k1·(1 − b + b·|d|/avgdl_ref))`, using a fixed reference length `avgdl_ref`
  (~70% of the target chunk size). A document's vector depends only on that document.
* **Query side:** each query dimension carries `idf(t) = ln(1 + (N − df + 0.5)/(df + 0.5))`, computed from
  document-frequency statistics in `sparse_stats.json` next to the index. The ingestion job recomputes them
  after every sync.
* The store's dot product is then exactly BM25. Dense and sparse rankings are fused with RRF
  (`alpha` = dense weight, default 0.6, tunable via `RAG_RRF_ALPHA`).

## Consequences
* Adding or removing documents changes IDF, but **no existing vector has to be re-upserted**. Only the
  small stats file changes, and the API reloads it at startup.
* `avgdl_ref` is a constant rather than the live corpus mean. With chunks bounded at ~350 tokens the
  difference is small, and it is the price of corpus-independent document vectors.
* Hash collisions are possible but negligible: about 10^4 distinct terms in a 1.6×10^7 space.
* The same encoder runs offline in `MemoryVectorStore`, so CI exercises the exact sparse math.
