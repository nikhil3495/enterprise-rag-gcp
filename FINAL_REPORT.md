# Final report — Enterprise RAG Assistant on Google Cloud

**Status: built, tested and published; CI green on GitHub; cloud deployment blocked on a closed billing account.**
Repository: https://github.com/nikhil3495/enterprise-rag-gcp (public)

The full system is implemented and verified offline: code, tests, data, eval harness, Terraform, deploy
scripts, CI/CD workflows and docs. The cloud phases need one human action, an interactive `gcloud auth
login`, plus a GitHub token scope refresh for the workflow files. No live metric below was invented; live
fields stay *pending* until a committed live report exists.

## Definition of done

| Item | Status |
| --- | --- |
| Public repo, clean history, no secrets | ✅ secret scan clean (`scripts/secret_scan.sh`); no keys, `.env` or tfstate tracked |
| CI ✅ and CD ✅ green on `main` | ◑ **CI green** on GitHub (run 44158117092: tests, offline eval gate, Docker build + container smoke, Terraform validate). CD ran and its gate skipped the deploy (no GCP repo variables yet), so the WIF deploy path is still unproven |
| Cloud Run live, IAM-protected (unauthenticated → 403), answering doc / SQL / hybrid with citations | ⛔ blocked (BLOCKERS #1-2). Terraform has no `allUsers` binding; `scripts/smoke_test.{sh,ps1}` asserts the 403 and all three routes |
| Vector Search deployed and populated (or documented fallback); BigQuery seeded; ingestion job succeeded | ⛔ blocked. Terraform validated; `deploy.ps1 -Stage seed/ingest` ready; memory/GCS fallback documented |
| Offline and live eval reports committed; README matches them | ◑ offline report committed and quoted verbatim; live report pending |
| Teardown / vs-undeploy documented and tested with `terraform plan -destroy` | ◑ scripts and docs done; `plan -destroy` needs credentials and state |

## Service URL and how to call it
Pending deployment. The URL is deterministic: `https://rag-api-<project-number>.us-central1.run.app`
(`terraform -chdir=infra/terraform output -raw service_url`).

```bash
curl -s -X POST "$SERVICE_URL/v1/query" \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -H "Content-Type: application/json" \
  -d '{"question": "What is the standard warranty for the Atlas AMR and how many SEV1 tickets did it get in 2025?"}'
```

## Measured metrics

**Offline**, from `eval/reports/20261117T183540Z-local.md` (35 golden questions; heuristic reasoner;
regression gate, not model quality):
recall@5 1.00 · MRR 1.00 · route accuracy 1.00 · answer correctness 0.875 · faithfulness (lexical proxy)
1.00 · abstention accuracy 1.00 · grounded rate 1.00 · p50/p95/p99 latency 5.8 / 27.4 / 92.7 ms.

**Engineering:** 117 tests · 89.3% coverage · 22 SQL-guard attack cases · every graph path tested
(vector / sql / parallel both / rewrite / abstain / grounding regeneration).

**Live:** pending. Fill from `eval/reports/<ts>-http.md` after `.\tasks.ps1 eval-live`: recall@5,
faithfulness (Gemini judge), p95 latency, output tokens/s, cost per request.

## Daily cost estimate
- Vector Search deployed index (1 × e2-standard-2): **≈ $2.3/day** (list-price estimate; the dominant cost).
- Everything else at demo traffic (Cloud Run min 0, Firestore, BigQuery, GCS, AR, Logging): **< $0.10/day**.
- Per request: estimated $0.002–0.006 (see docs/cost.md); to be replaced with the measured value.
- **Stop Vector Search billing:** `.\tasks.ps1 vs-undeploy` (or `make vs-undeploy`). Index data is kept.

## Blockers (from BLOCKERS.md)
1. gcloud has no authenticated account → `gcloud auth login` and `gcloud auth application-default login`.
2. No GCP project → `.\scripts\bootstrap_project.ps1`.
3. GitHub token lacks `workflow` scope → `gh auth refresh -h github.com -s workflow,repo`, then merge and push `ci-workflows`.
4. Budget alert needs billing-account permission → `.\scripts\create_budget.ps1`.
5. Docker Desktop absent (not needed: Cloud Build).
6. New terminal needed for the PATH updates.

## Resume bullets
Only numbers that are measured today are filled in. Bracketed fields **must** come from the live report.
Don't publish them before it exists.

- Built an enterprise RAG assistant on Google Cloud: a Python ingestion pipeline (structure-aware chunking,
  idempotent content-hash sync) embeds documents into **Vertex AI Vector Search** with hybrid dense +
  BM25-sparse retrieval (query-side IDF, RRF) and reranking. A guarded **text-to-SQL** tool (AST guard, dry
  run, byte cap, self-repair) answers questions over **BigQuery**. Live recall@5 **[x.xx]** on a
  35-question golden set.
- Orchestrated retrieval as a **LangGraph** state graph: LLM routing across vector, SQL and parallel hybrid
  paths, batched relevance grading with a self-correcting query-rewrite loop, and **Gemini 3.5 Flash**
  answers with validated citations plus a grounding check. Live faithfulness **[x.xx]**, abstention accuracy
  **[x.xx]** on out-of-scope questions.
- Shipped it as an IAM-only **FastAPI service on Cloud Run** (ID-token verification, keyless GitHub Actions
  CI/CD over Workload Identity Federation, Terraform). An evaluation harness gates p95 latency **[x.x s]**,
  **[n] tokens/s** and **$[0.00x] per request**. 117 tests at 89% coverage; 22 SQL-injection and
  exfiltration cases blocked.
