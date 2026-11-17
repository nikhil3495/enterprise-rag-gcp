# ADR 0005 — Offline-first testability

**Status:** accepted

## Context
Cloud-backed RAG systems are often untestable without credentials, so CI either skips most logic or
depends on live services, which is flaky, slow and costly.

## Decision
Every external dependency is behind a small protocol (`Embedder`, `Reranker`, `VectorStore`, `DocStore`,
`SqlEngine`, `Reasoner`, `StructuredLLM`). Each has an offline implementation:

| Concern | prod | local (offline) |
| --- | --- | --- |
| LLM decisions | `GeminiReasoner` (Gemini 3.5 Flash / Flash-Lite) | `HeuristicReasoner` |
| Embeddings | `gemini-embedding-001` (768-d, L2-normalised) | `HashEmbedder` (signed feature hashing) |
| Vector index | Vertex AI Vector Search (hybrid RRF) | `MemoryVectorStore` (same RRF semantics) |
| Docstore | Firestore | JSON file (local or GCS) |
| SQL | BigQuery | DuckDB loaded from the same CSVs + `schema.yaml` |
| Rerank | Vertex AI Ranking API | lexical BM25 reranker |

`Container` wires one or the other from `RAG_PROFILE`, and each component can be overridden individually.
`RAG_VECTOR_BACKEND=memory` with a GCS snapshot is also the documented low-cost production fallback.

## Consequences
* `uv run pytest` and the offline eval need no network. CI enforces ≥ 80% coverage and the offline gates.
* Production adapters are tested with stubbed clients: request shapes, retries, fallbacks and usage
  accounting.
* Offline quality numbers come from heuristics tuned on the golden set. They gate regressions in plumbing
  and retrieval, not model quality. Live numbers come only from the deployed service.
