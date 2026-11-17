# ADR 0004 — IAM-only API access and keyless CI (Workload Identity Federation)

**Status:** accepted

## Context
The assistant exposes internal company data. There must be no public access, and no long-lived credentials
in GitHub or on laptops.

## Decision
* **Cloud Run** is deployed without any `allUsers` binding. Only principals with `roles/run.invoker` (the
  owner and the `gh-deployer` SA) get past Google's front end; everyone else receives **403** before the
  container sees the request.
* **In-app verification** (`RAG_AUTH_MODE=iam`) is defence in depth. The app verifies the Google-signed ID
  token (signature, expiry, issuer `accounts.google.com`, audience) with cached certs that are refreshed once
  on failure. It enforces an allowlist (`RAG_AUTH_ALLOWED_PRINCIPALS`, verified email). The JSON errors are
  `401 unauthenticated` and `403 forbidden`.
* **Accepted audiences:** the service URL (service-account tokens, CI) and the gcloud CLI OAuth client ID
  (`32555940559.apps.googleusercontent.com`). `gcloud auth print-identity-token` user tokens carry the
  latter. Accepting it is safe because IAM invoker and the email allowlist still apply.
* **CI/CD** authenticates with GitHub OIDC through a WIF pool whose provider has
  `attribute_condition = "assertion.repository == 'nikhil3495/enterprise-rag-gcp'"`, impersonating
  `gh-deployer`. There are no JSON keys anywhere. The same federation mints the ID token for the post-deploy
  smoke test and the live eval.
* **Least privilege:** `rag-api` reads only (Vertex AI user, BigQuery jobUser plus dataset dataViewer,
  Firestore user, bucket objectViewer, Discovery Engine user for ranking). `rag-ingest` can additionally
  write the bucket and the dataset. `gh-deployer` can push images, deploy revisions, act as the two runtime
  SAs, invoke the service, and call Gemini for the eval judge.

## Consequences
* `/healthz` and `/readyz` are unauthenticated in-app (startup probes) but still behind Cloud Run IAM.
* Deleting and recreating the WIF pool within 30 days needs a new pool ID because pools are soft-deleted.
