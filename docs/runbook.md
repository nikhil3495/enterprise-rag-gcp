# Runbook

All commands have a PowerShell form (`.\tasks.ps1 …`, `.\scripts\*.ps1`) and a bash form (`make …`,
`./scripts/*.sh`).

## First deployment
```powershell
gcloud auth login
gcloud auth application-default login
.\scripts\bootstrap_project.ps1            # new project + billing + APIs (or -ProjectId <existing>)
.\scripts\deploy.ps1 -Stage infra          # Terraform (Vector Search takes 20-60 min)
.\scripts\deploy.ps1 -Stage image          # Cloud Build → Artifact Registry
.\scripts\deploy.ps1 -Stage app            # roll the image to rag-api + rag-ingest
.\scripts\deploy.ps1 -Stage seed           # upload docs to GCS, seed BigQuery via the job
.\scripts\deploy.ps1 -Stage ingest         # chunk + embed + upsert
.\tasks.ps1 smoke                          # 403 unauthenticated; doc/sql/hybrid answers with citations
.\tasks.ps1 eval-live                      # live report → eval/reports/
.\scripts\deploy.ps1 -Stage vars           # GitHub repo variables for CD (no secrets)
```
Or all at once: `.\scripts\deploy.ps1` (stage `all`).

## Call the API
```bash
URL=$(terraform -chdir=infra/terraform output -raw service_url)
curl -s -X POST "$URL/v1/query" \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -H "Content-Type: application/json" \
  -d '{"question": "How many SEV1 tickets were opened in 2025?", "debug": false}'
```

## Routine operations
| Task | Command |
| --- | --- |
| Re-ingest after changing documents | upload to `gs://<bucket>/docs`, then `gcloud run jobs execute rag-ingest --region us-central1 --wait` (idempotent; `--prune` removes deleted docs) |
| Re-seed BigQuery | `gcloud run jobs execute rag-ingest --args=seed-bigquery --wait --region us-central1` |
| Check index contents | `gcloud run jobs execute rag-ingest --args=stats --wait`, then read the job logs |
| Tail request logs | `gcloud logging read 'jsonPayload.message="rag_request"' --limit 20 --format json` |
| Look up one request | `gcloud logging read 'jsonPayload.request_id="<id>"'` |
| Stop Vector Search billing | `.\tasks.ps1 vs-undeploy` (queries return 503 until redeployed with `deploy.ps1 -Stage infra`) |
| Preview teardown | `.\tasks.ps1 teardown` (`terraform plan -destroy`) |
| Teardown | `.\scripts\teardown.ps1 -Yes` (add `-DeleteState` to remove the tfstate bucket) |
| Roll back | `gcloud run services update-traffic rag-api --to-revisions <prev-revision>=100 --region us-central1` |

## Troubleshooting
| Symptom | Likely cause | Action |
| --- | --- | --- |
| 403 from curl | caller lacks `roles/run.invoker` | owner and deployer SA are bound by Terraform; add others with `gcloud run services add-iam-policy-binding` |
| 401 `invalid token` | token audience not accepted, or token expired | use `gcloud auth print-identity-token`; SA tokens need audience = service URL |
| 403 `principal not allowed` | email not in `RAG_AUTH_ALLOWED_PRINCIPALS` | add it in Terraform (`api_env`) and apply |
| 503 `upstream_unavailable` | Gemini / Vector Search / BigQuery error | look up the request ID in the logs; check that the deployed index exists (`gcloud ai index-endpoints describe`) |
| Answers without reranking | Ranking API 403 or disabled | trace shows `reranker: lexical`; grant `roles/discoveryengine.user` / enable `discoveryengine.googleapis.com` |
| Gemini 404 for a model ID | model not available in the project | set `RAG_GENERATION_MODEL` / `RAG_FAST_MODEL` (Terraform vars `generation_model`/`fast_model`) to the newest available Flash / Flash-Lite |
| `thinking_level` 400 | model doesn't support it | handled automatically (retry without it, remembered per model) |
| Terraform: WIF pool already exists | pool was soft-deleted less than 30 days ago | `gcloud iam workload-identity-pools undelete github-actions --location=global`, or rename the pool |
| Cloud Build cannot push | build SA lacks Artifact Registry access | grant the Cloud Build SA `roles/artifactregistry.writer` on the `rag` repo |
