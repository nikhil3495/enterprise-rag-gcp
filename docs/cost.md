# Cost

All per-request numbers in the README come from the live eval report: the usage tracker multiplies
measured tokens by the configured price table. The monthly figures below are **list-price estimates** for
planning. Confirm them on the Google Cloud pricing pages for your region and currency.

## Price table used by the code (env-overridable, `src/rag/config.py`)
| Item | Price (USD) | Env var |
| --- | --- | --- |
| Gemini 3.5 Flash input / output (thinking billed as output) | $1.50 / $9.00 per 1M tokens | `RAG_PRICE_GENERATION_*` |
| Gemini 3.5 Flash-Lite input / output | $0.30 / $2.50 per 1M tokens | `RAG_PRICE_FAST_*` |
| gemini-embedding-001 | $0.15 per 1M input tokens | `RAG_PRICE_EMBEDDING_INPUT_PER_M` |
| Vertex AI Ranking API | $1.00 per 1,000 queries | `RAG_PRICE_RERANK_PER_1K_QUERIES` |
| BigQuery on-demand | $6.25 per TiB billed (10 MB minimum per table) | `RAG_PRICE_BIGQUERY_PER_TIB` |

## Fixed / idle cost (estimate)
| Resource | Configuration | Idle cost |
| --- | --- | --- |
| **Vertex AI Vector Search deployed index** | 1 × e2-standard-2, SHARD_SIZE_SMALL, min=max=1 | **≈ $0.09–0.10 / node-hour ⇒ ≈ $2.3/day ⇒ ≈ $68/month.** This dominates the bill |
| Cloud Run service | min instances 0, 1 vCPU / 1 GiB, CPU only during requests | ≈ $0 idle |
| Cloud Run job | runs only on ingestion | cents per run |
| Firestore | ~100 small documents | free tier |
| BigQuery | ~1 MB of tables | free tier storage |
| GCS + Artifact Registry | a few MB of docs/state; images ≈ 0.3 GB with keep-10 cleanup | < $0.10/month |
| Cloud Logging | low volume | free tier |

### Stop Vector Search billing when idle
```powershell
.\tasks.ps1 vs-undeploy        # or: make vs-undeploy  (keeps the index data; redeploy: .\scripts\deploy.ps1 -Stage infra)
```
The cheapest always-on alternative is `RAG_VECTOR_BACKEND=memory`: an in-memory hybrid index loaded from a
GCS snapshot. It has the same RRF semantics and suits corpora up to roughly 10^5 chunks per instance.
Deploy it with `-ExtraTfVars "-var=deploy_vector_search=false"`.

## Per-request cost (estimate before live measurement)
A vector question makes ~5 model calls: route, grade, generate, grounding check, and one query embedding.
With ~6 passages of ~350 tokens, expect roughly 6–8k input tokens and under 1k output tokens, mostly on
Flash-Lite, plus one rank call ($0.001). That is on the order of **$0.002–0.006 per request**. The
measured value replaces this estimate in `README.md` after the live eval.

## Budget
`.\scripts\create_budget.ps1` creates a ₹2,000/month budget with alerts at 50/90/100%. Terraform can also
manage it when `billing_account` is set.
