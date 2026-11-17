# Architecture

## System

```mermaid
flowchart LR
  subgraph Clients
    U[Owner / service account<br/>Google ID token]
    GH[GitHub Actions<br/>OIDC → WIF]
  end
  subgraph GCP["Google Cloud (us-central1)"]
    CR["Cloud Run service rag-api<br/>FastAPI + LangGraph<br/>IAM invoker only"]
    JOB["Cloud Run job rag-ingest<br/>ingest / seed-bigquery"]
    GCS[("GCS bucket<br/>docs/ + state/sparse_stats.json")]
    FS[("Firestore<br/>rag_chunks · rag_documents")]
    VS[("Vertex AI Vector Search<br/>hybrid dense+sparse, RRF")]
    BQ[("BigQuery<br/>enterprise_analytics")]
    AR[("Artifact Registry")]
    LOG["Cloud Logging<br/>rag_request metrics"]
  end
  subgraph Vertex["Vertex AI (global)"]
    GEM["Gemini 3.5 Flash / Flash-Lite"]
    EMB["gemini-embedding-001"]
    RANK["Ranking API<br/>semantic-ranker-default-004"]
  end
  U -->|HTTPS + Bearer| CR
  GH -->|build/push| AR
  GH -->|deploy, smoke, live eval| CR
  CR --> EMB & VS & FS & RANK & GEM & BQ
  JOB --> GCS & EMB & VS & FS & BQ
  CR -.stdout JSON.-> LOG
```

## Request flow (LangGraph)

Exported from the compiled graph (`uv run rag export-graph`):

```mermaid
graph TD;
	__start__([<p>__start__</p>]):::first
	route(route)
	retrieve(retrieve)
	run_sql(run_sql)
	grade(grade)
	rewrite(rewrite)
	generate(generate)
	check_grounding(check_grounding)
	__end__([<p>__end__</p>]):::last
	__start__ --> route;
	check_grounding -. &nbsp;end&nbsp; .-> __end__;
	check_grounding -.-> generate;
	generate --> check_grounding;
	grade -.-> generate;
	grade -.-> rewrite;
	retrieve --> grade;
	rewrite --> retrieve;
	route -.-> retrieve;
	route -.-> run_sql;
	run_sql --> grade;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```

| Node | Model / service | Output |
| --- | --- | --- |
| `route` | Flash-Lite, structured JSON | `{route: vector\|sql\|both, reason}` |
| `retrieve` | gemini-embedding-001 → Vector Search hybrid (k=30, α=0.6) → Firestore → Ranking API (top 6) | candidates + reranked passages, per-stage timings |
| `run_sql` | Flash-Lite drafts `{sql, explanation}` → guard → dry run → BigQuery, repair ≤ 2 | `SqlResult` |
| `grade` | Flash-Lite, one batched call | relevant passages |
| `rewrite` | Flash-Lite | new retrieval query (≤ 2 loops) |
| `generate` | Flash (thinking LOW) | cited answer or abstention |
| `check_grounding` | Flash-Lite judge | `{grounded, unsupported_claims}`; one regeneration |

## Ingestion

```mermaid
flowchart LR
  S["gs://bucket/docs<br/>md · txt · html · pdf"] --> L[loaders<br/>Markdown-normalised, PDF pages]
  L --> H{content hash<br/>changed?}
  H -- no --> SKIP[skip]
  H -- yes --> C["structure-aware chunker<br/>headings → ~350 tok, 60 overlap"]
  C --> E["dense: gemini-embedding-001<br/>'title > section\ntext'"]
  C --> SP["sparse: hashed BM25 TF<br/>(2^24 dims)"]
  E & SP --> UP["Vector Search upsert<br/>batches of 100"]
  C --> FS[Firestore chunks]
  UP --> M[manifest: rag_documents]
  M --> ST["recompute DF stats → state/sparse_stats.json<br/>(query-side IDF)"]
```

Removed documents are deleted with `--prune`. Changed documents delete their stale chunk IDs from both stores
first.

## Data model
* `Chunk {chunk_id = sha256(uri)[:16]-NNNN, doc_id, source_uri, title, section ("A > B"), text, page, index}`
* `DocumentRecord {doc_id, source_uri, title, content_hash, chunk_ids[], ingested_at}`
* BigQuery `enterprise_analytics.{sales_orders, support_tickets, headcount}`, with descriptions taken from
  `data/structured/schema.yaml`.

## Observability
* One structured `rag_request` log line per query: latency, route, tokens, cost, grounded, rewrites, sql_ok.
* `logging.googleapis.com/trace` correlation from `X-Cloud-Trace-Context`, and an `X-Request-ID` echo.
* Log-based metrics `rag_request_latency_ms` (by route) and `rag_request_cost_usd`.
