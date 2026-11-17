# Evaluation

`rag eval` runs the golden set (`eval/golden_set.jsonl`) against a target and writes
`eval/reports/<timestamp>-<target>.{json,md}`. It exits non-zero when a threshold in
`eval/thresholds.yaml` regresses.

```bash
uv run rag eval --target local                          # in-process, offline profile (CI gate)
uv run rag eval --target http --url "$SERVICE_URL"      # deployed Cloud Run service (ID token auth)
uv run rag eval --target http --url "$SERVICE_URL" --limit 10   # stratified subset (CD)
```

## Golden set
35 items, generated deterministically by `scripts/generate_sample_data.py` (fixed seed):

| Category | n | What it checks |
| --- | --- | --- |
| document | 19 | facts in the 8 documents (md, txt, html, pdf), one question per checkable fact |
| sql | 10 | aggregates over BigQuery tables; **ground truth computed from the generated rows**, never by hand |
| hybrid | 3 | one document fact plus one computed number in the same question (`route=both`) |
| out_of_scope | 3 | general-knowledge or unavailable facts; the assistant must abstain |

Each item has `id, question, expected_route, relevant_sources[], must_contain[], reference_answer, category`.

## Metrics
| Metric | Definition |
| --- | --- |
| recall@k (k = 1, 3, 5, 10) | document-level: fraction of `relevant_sources` among the top-k unique source documents, both **before** (fused hybrid candidates) and **after** reranking |
| MRR, hit-rate@5 | reciprocal rank of the first relevant document; any relevant document in the top 5 |
| route accuracy | `route == expected_route` |
| answer correctness | fraction of `must_contain` items present. Numbers match with tolerance (`$136.79 million` ≈ `136791603.60`); small integers must match exactly |
| faithfulness | supported claims / total claims, judged against the exact context the generator saw. Live: Gemini judge (Flash-Lite). Offline: a lexical proxy (≥ 70% content-word coverage, and every number present in the sources) |
| abstention accuracy | out-of-scope items answered with `insufficient_context=true` |
| false abstention rate | in-scope items that abstained |
| grounded rate | responses whose final `check_grounding` verdict was grounded |
| latency p50/p95/p99 | in-process graph latency (local), or client-observed end-to-end latency (http) |
| output tokens/s | generation-model output tokens ÷ generation-call latency |
| cost per request | Σ(tokens × price) + ranking ($1/1k queries) + BigQuery bytes billed ($6.25/TiB), from the per-request usage tracker. Prices are env-overridable |

## Results

### Offline (CI gate) — `eval/reports/20261117T183540Z-local.md`
Heuristic reasoner, hash embeddings, in-memory hybrid index, DuckDB, lexical reranker. No model calls, so
cost and tokens/s are 0 and n/a by construction.

| recall@5 | MRR | route acc. | correctness | faithfulness (proxy) | abstention | grounded | p95 latency |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1.00 | 1.00 | 1.00 | 0.875 | 1.00 | 1.00 | 1.00 | 27.4 ms |

> **How to read these numbers:** the offline heuristics (keyword router, lexical grader, pattern SQL) were
> written with the golden set in view, and the corpus has only 8 documents. These numbers prove the
> plumbing (chunking, sparse/dense fusion, guard, repair loop, graph paths, citations, abstention) and gate
> regressions in CI. They are **not** a model-quality claim.

### Live (Cloud Run + Gemini + Vector Search + BigQuery)
**Pending billing.** The deployment is ready but waits on an active billing account (see `BLOCKERS.md`). After deployment:
`.\tasks.ps1 eval-live` (or `make eval-live`). The live report is committed under `eval/reports/` and the
README results table is filled from it verbatim.

## Tuning log
At most 2 iterations, recorded here with the report each one produced.

| # | Change | Report | recall@5 | faithfulness | p95 | cost/req |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | baseline: candidate_k=30, top_k=6, α=0.6, 350/60 chunks | pending live run | — | — | — | — |
