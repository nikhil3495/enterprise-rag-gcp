# API

Base URL: the Cloud Run service URL (`terraform output service_url`). Every request needs
`Authorization: Bearer <Google ID token>`. Cloud Run IAM rejects unauthenticated calls with **403**, and
the app verifies the token again (`RAG_AUTH_MODE=iam`). OpenAPI is served at `/docs` and `/openapi.json`.

## `GET /healthz`
Liveness. `{"status": "ok"}`

## `GET /readyz`
`200 {"status": "ready"}` once the components are loaded, `503 {"status": "starting"}` before that.

## `GET /v1/info`
Version and the active configuration: profile, backends, model IDs, k values, α, and tables.

## `POST /v1/query`
Request:
```json
{ "question": "string (1-2000 chars)", "top_k": 6, "debug": false }
```
Response (`200`):
```json
{
  "answer": "Administrator accounts must use phishing-resistant FIDO2 hardware security keys [S1].",
  "citations": [
    {"label": "S1", "kind": "document", "source_uri": "gs://…/docs/information_security_policy.md",
     "title": "Helios Robotics Information Security Policy",
     "section": "1. Identity and Authentication > 1.1 Multi-factor authentication",
     "page": null, "snippet": "…", "score": 0.97, "chunk_id": "…-0001"},
    {"label": "SQL1", "kind": "sql", "sql": "SELECT …", "snippet": "| n |\n| --- |\n| 51 |"}
  ],
  "route": "vector | sql | both",
  "sql": {"query": "SELECT …", "ok": true, "columns": ["n"], "rows": [[51]], "row_count": 1, "attempts": 1},
  "grounded": true,
  "insufficient_context": false,
  "usage": {"input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0, "cost_usd": 0.0, "output_tokens_per_s": null},
  "latency_ms": 0.0,
  "request_id": "…",
  "retrieved": ["… (debug only) reranked passages with retrieval_score / rerank_score / text"],
  "candidates": ["… (debug only) fused hybrid candidates before rerank"],
  "trace": ["… (debug only) [{node, ms, …}] per graph step"],
  "context": "… (debug only) exact sources block given to the generator"
}
```
The values above show the shape only. Real responses carry measured usage and latency.

Headers: `X-Request-ID` is echoed back, or generated when absent or invalid. `X-Cloud-Trace-Context` is
correlated into the logs.

## Errors
All errors use `{"error": str, "detail": str, "request_id": str}`. Stack traces are never returned.

| Status | `error` | When |
| --- | --- | --- |
| 401 | `unauthenticated` | missing / invalid / expired token, wrong audience or issuer |
| 403 | `forbidden` | principal not in the allowlist (Cloud Run IAM also returns 403 before the app) |
| 422 | `invalid_request` | empty or too-long question, `top_k` out of range |
| 503 | `upstream_unavailable` | model / vector search / BigQuery failure after retries |
