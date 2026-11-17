#!/usr/bin/env bash
# End-to-end deploy: ./scripts/deploy.sh [infra|image|app|seed|ingest|vars|all]
# Requires: gcloud (authenticated: user + ADC), terraform >= 1.9. Docker is NOT required (Cloud Build).
set -euo pipefail

STAGE="${1:-all}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TF="$ROOT/infra/terraform"
PROJECT="${RAG_GCP_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${RAG_REGION:-us-central1}"
OWNER="${OWNER_EMAIL:-$(gcloud config get-value account 2>/dev/null)}"
REPO="${GITHUB_REPO:-nikhil3495/enterprise-rag-gcp}"
TAG="${IMAGE_TAG:-$(git -C "$ROOT" rev-parse --short HEAD)}"
IMAGE="$REGION-docker.pkg.dev/$PROJECT/rag/rag-api:$TAG"

[[ -n "$PROJECT" ]] || { echo "No project: set RAG_GCP_PROJECT or 'gcloud config set project'"; exit 2; }
[[ -n "$OWNER" ]] || { echo "No gcloud account: run 'gcloud auth login'"; exit 2; }
log() { printf '\n==> %s\n' "$*"; }

tf_vars() {
  echo "-var=project_id=$PROJECT -var=region=$REGION -var=owner_email=$OWNER -var=github_repo=$REPO ${TF_EXTRA_VARS:-}"
}

stage_infra() {
  log "Enabling bootstrap APIs"
  gcloud services enable serviceusage.googleapis.com cloudresourcemanager.googleapis.com storage.googleapis.com \
    iam.googleapis.com aiplatform.googleapis.com run.googleapis.com artifactregistry.googleapis.com \
    cloudbuild.googleapis.com --project "$PROJECT"
  local bucket="gs://$PROJECT-tfstate"
  if ! gcloud storage buckets describe "$bucket" --project "$PROJECT" >/dev/null 2>&1; then
    log "Creating state bucket $bucket"
    gcloud storage buckets create "$bucket" --project "$PROJECT" --location "$REGION" --uniform-bucket-level-access
    gcloud storage buckets update "$bucket" --versioning
  fi
  terraform -chdir="$TF" init -input=false -reconfigure -backend-config="bucket=$PROJECT-tfstate"
  log "terraform apply (Vector Search deployment can take 20-60 min)"
  # shellcheck disable=SC2046
  terraform -chdir="$TF" apply -input=false -auto-approve $(tf_vars)
}

stage_image() {
  log "Building $IMAGE with Cloud Build"
  gcloud builds submit "$ROOT" --project "$PROJECT" --region "$REGION" --tag "$IMAGE" --timeout=1800s
}

stage_app() {
  log "Rolling out $IMAGE"
  gcloud run services update rag-api --project "$PROJECT" --region "$REGION" --image "$IMAGE" --quiet
  gcloud run jobs update rag-ingest --project "$PROJECT" --region "$REGION" --image "$IMAGE" --quiet
}

stage_seed() {
  local bucket
  bucket="$(terraform -chdir="$TF" output -raw bucket)"
  log "Uploading documents to gs://$bucket/docs"
  gcloud storage rsync "$ROOT/data/docs" "gs://$bucket/docs" --recursive --delete-unmatched-destination-objects
  log "Seeding BigQuery (job execution with args override)"
  gcloud run jobs execute rag-ingest --project "$PROJECT" --region "$REGION" --args=seed-bigquery --wait
}

stage_ingest() {
  log "Running ingestion job"
  gcloud run jobs execute rag-ingest --project "$PROJECT" --region "$REGION" --wait
}

stage_vars() {
  log "Setting GitHub repository variables (no secrets)"
  gh variable set GCP_PROJECT_ID --repo "$REPO" --body "$PROJECT"
  gh variable set GCP_REGION --repo "$REPO" --body "$REGION"
  gh variable set WIF_PROVIDER --repo "$REPO" --body "$(terraform -chdir="$TF" output -raw wif_provider)"
  gh variable set DEPLOYER_SA --repo "$REPO" --body "$(terraform -chdir="$TF" output -raw deployer_sa)"
  gh variable set SERVICE_URL --repo "$REPO" --body "$(terraform -chdir="$TF" output -raw service_url)"
}

case "$STAGE" in
  infra) stage_infra ;;
  image) stage_image ;;
  app) stage_app ;;
  seed) stage_seed ;;
  ingest) stage_ingest ;;
  vars) stage_vars ;;
  all) stage_infra; stage_image; stage_app; stage_seed; stage_ingest; stage_vars
       "$ROOT/scripts/smoke_test.sh" "$(terraform -chdir="$TF" output -raw service_url)" ;;
  *) echo "unknown stage: $STAGE"; exit 2 ;;
esac
