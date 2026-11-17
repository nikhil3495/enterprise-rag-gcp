# Blockers — need a human

Resolve the open item, then tell Claude Code: **"Read PROGRESS.md and continue"**.

## Open

| # | Blocker | Impact | What the owner must do |
|---|---------|--------|------------------------|
| 1 | The only billing account, **"My Billing Account" (`01B2C2-B610E1-474EDF`, INR), is CLOSED** (`open: false`). Project `enterprise-rag-qf6pal` is linked to it but reports `billingEnabled: false`. | Every billable API is unusable: Vertex AI (Gemini, embeddings, Vector Search), Cloud Run, Cloud Build, BigQuery, Firestore. No deploy, seed or live eval is possible. | Reopen the account or create a new one (needs a valid payment method) at https://console.cloud.google.com/billing. Then run `gcloud billing projects link enterprise-rag-qf6pal --billing-account=<OPEN_ACCOUNT_ID>` and confirm with `gcloud billing projects describe enterprise-rag-qf6pal` (`billingEnabled: true`). |
| 2 | Budget alert (₹2,000) | Not created yet. | Created automatically after #1 via `.\scripts\create_budget.ps1` if you have billing-admin rights on the account. |

## Resolved
- ✅ gcloud login + ADC (`nikhilbansiwal3495@gmail.com`), 2026-11-18.
- ✅ GCP project created: **`enterprise-rag-qf6pal`**, set as gcloud project and ADC quota project.
- ✅ GitHub `workflow` scope. Workflows merged to `main`; **CI green** (run 44158117092); CD gate skips cleanly until repo variables are set.
- ℹ️ Docker Desktop is not needed: Cloud Build builds deploy images, and CI builds on GitHub runners.
