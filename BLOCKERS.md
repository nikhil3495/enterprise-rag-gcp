# Blockers — need a human

Everything that does not depend on these was completed. Run the commands below, then tell Claude Code: "Read PROGRESS.md and continue".

| # | Blocker | Impact | Command for the owner |
|---|---------|--------|-----------------------|
| 1 | `gcloud` has **no authenticated account** (installed fresh via winget, v594). | No GCP project, Terraform apply, deploy, BigQuery seed, live eval. | `gcloud auth login` then `gcloud auth application-default login` |
| 2 | No GCP project configured. | Same as #1. | After #1: `.\scripts\bootstrap_project.ps1` (creates `enterprise-rag-<rand>`, links first OPEN billing account, enables APIs) — or `gcloud config set project <existing-id>` |
| 3 | GitHub token lacks the **`workflow`** scope (has `repo, gist, read:org, user`). GitHub rejects pushes that add/modify `.github/workflows/*`. | CI/CD workflow files are committed on a local branch only (`ci-workflows`) and not yet on `main`. | `gh auth refresh -h github.com -s workflow` then `git checkout main && git merge ci-workflows && git push` |
| 4 | Billing budget alert (₹2,000) needs `billing.budgets.create` on the billing account. | No budget alert yet. | After #1/#2: `.\scripts\create_budget.ps1` |
| 5 | Docker Desktop not installed. | None — images build with `gcloud builds submit` (Cloud Build). | Optional: `winget install Docker.DockerDesktop` |

Tools installed non-interactively during preflight: Terraform 1.17.1, Google Cloud SDK 594.0.0, CPython 3.12.14 (via uv).
