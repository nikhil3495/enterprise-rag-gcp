# Blockers — need a human

Everything that does not depend on these is done. Resolve them in order, then tell Claude Code:
**"Read PROGRESS.md and continue"**.

| # | Blocker | Impact | Command for the owner |
|---|---------|--------|-----------------------|
| 1 | `gcloud` has **no authenticated account**. It was freshly installed via winget (v594.0.0); login is interactive (browser). | No GCP project, Terraform apply, deploy, BigQuery seed, live eval, CD. | `gcloud auth login` then `gcloud auth application-default login` |
| 2 | No GCP project configured. | Same as #1. | After #1: `.\scripts\bootstrap_project.ps1` creates `enterprise-rag-<6 chars>`, links the first OPEN billing account and enables APIs. To reuse a project: `.\scripts\bootstrap_project.ps1 -ProjectId <id>` |
| 3 | GitHub token lacks the **`workflow`** scope (it has `repo, gist, read:org, user`). GitHub rejects any push that adds or modifies `.github/workflows/*`. | CI/CD workflow files sit on **local branch `ci-workflows`** and are not on `main`, so CI/CD can't run yet. | `gh auth refresh -h github.com -s workflow,repo` then `git checkout main; git merge ci-workflows; git push` |
| 4 | Billing budget alert (₹2,000) needs `billing.budgets.create` on the billing account. | No budget alert yet. | After #2: `.\scripts\create_budget.ps1` (or set `-var=billing_account=XXXX` in Terraform) |
| 5 | Docker Desktop not installed. | None: images build with Cloud Build (`gcloud builds submit`), and CI builds the image on GitHub runners. | Optional: `winget install Docker.DockerDesktop` |
| 6 | `terraform` / `gcloud` were added to PATH by winget; already-open terminals don't see them. | None. | Open a new terminal |

Tools installed non-interactively during preflight: Terraform 1.17.1, Google Cloud SDK 594.0.0,
CPython 3.12.14 (via uv).
