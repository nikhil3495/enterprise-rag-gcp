# Progress

Resume with: **"Read PROGRESS.md and continue"** (after resolving `BLOCKERS.md`).

## Done ✅
- **Phase 0 — preflight.** Installed Terraform 1.17.1, gcloud 594.0.0 and CPython 3.12.14 (uv). gh is
  logged in (no `workflow` scope). gcloud has **no account** (blocker).
- **Phase 1 — repo.** `github.com/nikhil3495/enterprise-rag-gcp` (public, MIT).
- **Phase 3 — offline core.** Config, telemetry, observability, loaders, chunker, idempotent pipeline,
  BM25 sparse encoder (query-side IDF), memory hybrid store (RRF), docstores, SQL catalog/guard/DuckDB/
  BigQuery, text-to-SQL repair loop, reasoners, LangGraph graph, service, FastAPI app with IAM auth, CLI.
- **Phase 4 — data + eval.** 8 synthetic docs (md/txt/html/pdf), 3 deterministic tables, 35-item golden
  set, eval harness. Offline eval PASS (`eval/reports/20261117T183540Z-local.md`).
- **Phase 5 — production adapters.** Gemini LLM/embedder (retries, thinking fallback, batch→single
  fallback), Vertex Vector Search, Firestore, BigQuery engine + seeding, Vertex ranker + fallback, ID-token
  auth. All unit-tested with stubs.
- **Quality.** 117 tests, 89% coverage, ruff + mypy clean.
- **Infra as code.** Terraform for APIs, GCS, AR, Firestore, BigQuery, Vector Search (index, endpoint,
  deployed index), SAs + IAM, WIF, Cloud Run service + job, log metrics, budget. `terraform validate` passes
  (provider 8.11.0; lock covers linux/windows/darwin).
- **Deploy tooling.** deploy / smoke / vs_undeploy / teardown / bootstrap / budget / secret-scan in `.sh`
  and `.ps1`; Makefile + `tasks.ps1`; Dockerfile (multi-stage, non-root).
- **CI/CD workflows** written (`.github/workflows/ci.yml`, `cd.yml`). They are committed on local branch
  `ci-workflows`, not pushed (blocked: `workflow` scope).
- **Docs.** architecture (Mermaid, exported graph), 5 ADRs, DECISIONS (30 entries), runbook, cost,
  evaluation, api, CLAUDE.md, README.

## 2026-11-18 update
- ✅ gcloud + ADC logged in. Project **`enterprise-rag-qf6pal`** created and set as the gcloud project and ADC quota project.
- ✅ Workflows merged to `main`. CI run 44158117092 is green (tests, Docker build + container smoke, Terraform validate). The CD gate skipped deploy (no repo variables yet).
- ⛔ **Billing account `01B2C2-B610E1-474EDF` is CLOSED**, so the project has `billingEnabled: false`. All cloud phases wait on this (BLOCKERS #1).

## Next (in order, once billing is enabled)
1. Confirm `gcloud billing projects describe enterprise-rag-qf6pal` shows `billingEnabled: true`.
2. `.\scripts\bootstrap_project.ps1 -ProjectId enterprise-rag-qf6pal` (enables APIs) → `.\scripts\deploy.ps1 -Stage infra`. Vector Search takes 20-60 min,
   so start it first.
3. `-Stage image`, `-Stage app`, `-Stage seed`, `-Stage ingest`, then `.\tasks.ps1 smoke`.
4. Verify the Ranking API works with `roles/discoveryengine.user` (trace shows `reranker: vertex`). Verify
   that the Gemini 3.5 model IDs resolve; if one 404s, set `generation_model`/`fast_model` to the newest GA
   Flash / Flash-Lite.
5. `.\tasks.ps1 eval-live` → commit the report → fill the README Results "Live" table and the
   `docs/evaluation.md` tuning log (≤ 2 iterations).
7. `.\scripts\deploy.ps1 -Stage vars` → `gh workflow run CD` → confirm WIF deploy + smoke + live eval green.
8. `.\scripts\teardown.ps1` (plan -destroy) to verify teardown. Update FINAL_REPORT.md.
