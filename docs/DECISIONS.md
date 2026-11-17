# Decision log

One line each: **decision** — why — alternative rejected.

1. **Offline-first, two profiles (`local` / `prod`) in one codebase** — CI and tests run with zero credentials; every cloud dependency sits behind a protocol — rejected: mocking SDKs per test (brittle, no end-to-end runs).
2. **Python 3.12 + uv with `uv.lock`** — reproducible, fast installs in CI and Docker — rejected: pip-tools/poetry (slower, extra tooling).
3. **LangGraph `StateGraph` with a TypedDict state and an `operator.add` trace reducer** — parallel branches append safely; the graph is unit-testable with fakes — rejected: hand-written orchestration (no fan-out/fan-in semantics, harder to visualise).
4. **"both" route = parallel superstep (`retrieve` ∥ `run_sql`) with fan-in at `grade`** — halves hybrid latency — rejected: sequential tool calls.
5. **Self-correction = grade → rewrite (≤2) → retrieve; then abstain** — bounded cost, measurable — rejected: unbounded agent loops.
6. **Grounding check + one regeneration with the judge's feedback** — catches unsupported claims cheaply on the fast model — rejected: no post-check (hallucinations ship) / multiple retries (latency).
7. **Hybrid retrieval with BM25 sparse vectors, IDF on the query side** (ADR 0001) — document vectors never need re-indexing when corpus stats change — rejected: TF-IDF baked into document vectors.
8. **Feature-hashed sparse space of 2^24 dims, blake2b hashing** — no vocabulary to persist; negligible collisions — rejected: a learned vocabulary (state + migrations).
9. **Vertex AI Vector Search `STREAM_UPDATE`, SHARD_SIZE_SMALL on e2-standard-2, public endpoint** — smallest supported footprint; no VPC needed — rejected: batch updates (slow sync), PSC endpoint (networking cost/complexity).
10. **Docstore in Firestore (`rag_chunks`, `rag_documents`)** — Vector Search returns IDs only; serverless, pay-per-use — rejected: storing text in restricts/metadata (size limits), Cloud SQL (always-on cost).
11. **Per-document manifest with content hash** — idempotent ingestion, stale chunk deletion, `--prune` — rejected: full re-index on every run.
12. **Chunk ID = `sha256(source_uri)[:16]-{index:04d}`** — deterministic upserts; replacing a doc overwrites by ID — rejected: random UUIDs (orphans).
13. **Structure-aware chunking (~350 tokens, ~60 overlap) that never crosses headings, plus a contextual header in the embedded text** — better precision, citations carry the section path — rejected: fixed-size windows.
14. **Reranking with the Vertex AI Ranking API (`semantic-ranker-default-004`), wrapped in a lexical fallback** — quality with graceful degradation — rejected: a self-hosted cross-encoder (GPU cost).
15. **Runtime SA gets `roles/discoveryengine.user`** — ranking needs `discoveryengine.rankingConfigs.rank`, which `viewer` is not documented to include. Verify on first live run and narrow if possible — rejected: guessing `viewer` and silently falling back.
16. **Text-to-SQL guard on the sqlglot AST** (ADR 0003) — robust against comments, casing and nesting tricks — rejected: regex filters.
17. **Dry run before execution plus `maximum_bytes_billed` (1 GB)** — defence against expensive scans; the dry-run error is fed back to the repair loop — rejected: execute and hope.
18. **DuckDB locally, loaded from the same CSVs + `schema.yaml`, with BigQuery SQL transpiled by sqlglot** — the same prompts, guard and repair loop run offline — rejected: SQLite (weaker dialect coverage).
19. **`schema.yaml` as the single source of truth for column descriptions; the prod catalog is read back from BigQuery metadata** — the model sees what is actually deployed — rejected: duplicating descriptions in prompts.
20. **IAM at Cloud Run (`--no-allow-unauthenticated`) plus in-app ID-token verification and an allowlist** (ADR 0004) — defence in depth; the app rejects tokens even if ingress changes — rejected: API keys (secret management, no identity).
21. **Accept the gcloud CLI client ID as an extra token audience** — owner-friendly `curl` with `gcloud auth print-identity-token`; still gated by IAM invoker plus the allowlist — rejected: requiring SA impersonation for every manual call.
22. **Workload Identity Federation for CI; no service-account keys anywhere** — no long-lived secrets — rejected: JSON keys in GitHub secrets.
23. **Deterministic Cloud Run URL (`rag-api-<project-number>.<region>.run.app`) computed in Terraform** — sets the token audience without a self-referential cycle — rejected: a two-pass apply.
24. **`lifecycle.ignore_changes` on the container image** — Terraform owns configuration, CI owns rollouts — rejected: Terraform-driven deploys (drift on every release).
25. **Cloud Build instead of local Docker for deploy scripts** — Docker Desktop isn't installed on the owner's laptop — rejected: requiring Docker locally.
26. **`vs_deployed` variable separate from `deploy_vector_search`** — `vs-undeploy` stops node-hour billing and keeps index data — rejected: destroying the index (requires re-ingestion).
27. **Prices and model IDs in env-overridable config; per-request ContextVar usage tracker with a lock** — cost per request is computed, not estimated by hand — rejected: post-hoc billing export analysis only.
28. **Eval: document-level recall@k before and after rerank, LLM-judge faithfulness (heuristic proxy offline), separate offline/live thresholds** — CI gates plumbing regressions; live gates quality — rejected: one threshold set for both profiles.
29. **Offline heuristic reasoner (keyword router, lexical grader, pattern SQL, extractive answers)** — exercises every graph path deterministically. Its eval numbers are a regression gate, not a quality claim, and are tuned on the golden set — rejected: skipping offline eval.
30. **Workflow files kept on branch `ci-workflows` until the GitHub token has the `workflow` scope** — GitHub rejects pushes touching `.github/workflows` without it — rejected: blocking all pushes.
