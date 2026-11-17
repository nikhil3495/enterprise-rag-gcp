#!/usr/bin/env bash
# Destroy all project resources created by Terraform. Usage: ./scripts/teardown.sh [--plan] [--yes]
#   --plan   show `terraform plan -destroy` only (safe)
#   --yes    actually destroy (also deletes the tfstate bucket when DELETE_STATE=1)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TF="$ROOT/infra/terraform"
PROJECT="${RAG_GCP_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
OWNER="${OWNER_EMAIL:-$(gcloud config get-value account 2>/dev/null)}"
VARS=(-var="project_id=$PROJECT" -var="owner_email=$OWNER")
terraform -chdir="$TF" init -input=false -reconfigure -backend-config="bucket=$PROJECT-tfstate" >/dev/null
case "${1:---plan}" in
  --plan) terraform -chdir="$TF" plan -destroy -input=false "${VARS[@]}" ;;
  --yes)
    terraform -chdir="$TF" destroy -input=false -auto-approve "${VARS[@]}"
    if [[ "${DELETE_STATE:-0}" == "1" ]]; then gcloud storage rm -r "gs://$PROJECT-tfstate"; fi
    echo "Teardown complete. Note: the WIF pool is soft-deleted for 30 days (same ID cannot be reused)." ;;
  *) echo "usage: teardown.sh [--plan|--yes]"; exit 2 ;;
esac
