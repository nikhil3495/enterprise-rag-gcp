#!/usr/bin/env bash
# Smoke test a deployed service: ./scripts/smoke_test.sh <service-url>
# Uses $RAG_ID_TOKEN if set (CI), else `gcloud auth print-identity-token` (owner).
set -euo pipefail
URL="${1:?usage: smoke_test.sh <service-url>}"
URL="${URL%/}"
TOKEN="${RAG_ID_TOKEN:-$(gcloud auth print-identity-token)}"
fail() { echo "FAIL: $*"; exit 1; }

code=$(curl -s -o /dev/null -w '%{http_code}' "$URL/v1/info")
[[ "$code" == "403" || "$code" == "401" ]] || fail "unauthenticated request returned $code (expected 403)"
echo "ok  unauthenticated -> $code"

code=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $TOKEN" "$URL/healthz")
[[ "$code" == "200" ]] || fail "authenticated /healthz returned $code"
echo "ok  /healthz -> 200"

ask() {
  local q="$1" expect_route="$2"
  local body
  body=$(curl -sf -X POST "$URL/v1/query" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    --max-time 120 -d "{\"question\": \"$q\"}") || fail "query failed: $q"
  python3 - "$body" "$expect_route" <<'PY' || fail "unexpected response for: $q"
import json, sys
d = json.loads(sys.argv[1]); exp = sys.argv[2]
assert d["route"] == exp, f"route {d['route']} != {exp}"
assert d["citations"], "no citations"
assert not d["insufficient_context"], "abstained"
print(f"ok  [{d['route']}] {d['latency_ms']:.0f} ms ${d['usage']['cost_usd']:.5f} :: {d['answer'][:140]}")
PY
}

ask "What is the minimum password length required by the security policy?" vector
ask "How many SEV1 support tickets were opened in 2025?" sql
ask "What is the standard warranty period for the Atlas AMR, and how many SEV1 support tickets were opened for the Atlas AMR in 2025?" both
echo "SMOKE TEST PASSED"
