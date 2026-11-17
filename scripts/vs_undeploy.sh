#!/usr/bin/env bash
# Stop Vector Search node-hour billing: undeploy the index (index data is kept).
# Redeploy later with: TF_EXTRA_VARS="" ./scripts/deploy.sh infra
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT="${RAG_GCP_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
OWNER="${OWNER_EMAIL:-$(gcloud config get-value account 2>/dev/null)}"
terraform -chdir="$ROOT/infra/terraform" init -input=false -reconfigure -backend-config="bucket=$PROJECT-tfstate" >/dev/null
terraform -chdir="$ROOT/infra/terraform" apply -input=false -auto-approve \
  -var="project_id=$PROJECT" -var="owner_email=$OWNER" -var="vs_deployed=false" \
  -target=google_vertex_ai_index_endpoint_deployed_index.rag
echo "Vector Search index undeployed. Queries return 503 until redeployed (./scripts/deploy.sh infra)."
