# ---- Service accounts ---------------------------------------------------------------------------
resource "google_service_account" "api" {
  account_id   = "rag-api"
  display_name = "RAG API (Cloud Run service runtime)"
}

resource "google_service_account" "ingest" {
  account_id   = "rag-ingest"
  display_name = "RAG ingestion (Cloud Run job runtime)"
}

resource "google_service_account" "deployer" {
  account_id   = "gh-deployer"
  display_name = "GitHub Actions deployer (Workload Identity Federation)"
}

locals {
  api_project_roles = [
    "roles/aiplatform.user",      # Gemini + Vector Search queries
    "roles/bigquery.jobUser",     # run (read-only) query jobs
    "roles/datastore.user",       # Firestore docstore reads
    "roles/discoveryengine.user", # Vertex AI Ranking API (rankingConfigs.rank); viewer lacks rank
  ]
  ingest_project_roles = [
    "roles/aiplatform.user", # embeddings + index upserts
    "roles/bigquery.jobUser",
    "roles/datastore.user",
  ]
  deployer_project_roles = [
    "roles/run.developer",     # deploy service revisions / update job
    "roles/aiplatform.user",   # Gemini judge for the live eval in CD
    "roles/logging.logWriter", # job logs
  ]
}

resource "google_project_iam_member" "api" {
  for_each = toset(local.api_project_roles)
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.api.email}"
}

resource "google_project_iam_member" "ingest" {
  for_each = toset(local.ingest_project_roles)
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.ingest.email}"
}

resource "google_project_iam_member" "deployer" {
  for_each = toset(local.deployer_project_roles)
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.deployer.email}"
}

# Dataset-level BigQuery access: the API can only read; IAM is the hard boundary for text-to-SQL.
resource "google_bigquery_dataset_iam_member" "api_viewer" {
  dataset_id = google_bigquery_dataset.analytics.dataset_id
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.api.email}"
}

resource "google_bigquery_dataset_iam_member" "ingest_editor" {
  dataset_id = google_bigquery_dataset.analytics.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.ingest.email}"
}

resource "google_storage_bucket_iam_member" "api_reader" {
  bucket = google_storage_bucket.data.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.api.email}"
}

resource "google_storage_bucket_iam_member" "ingest_admin" {
  bucket = google_storage_bucket.data.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.ingest.email}"
}

resource "google_artifact_registry_repository_iam_member" "deployer_writer" {
  location   = google_artifact_registry_repository.rag.location
  repository = google_artifact_registry_repository.rag.name
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_service_account_iam_member" "deployer_acts_as" {
  for_each           = { api = google_service_account.api.name, ingest = google_service_account.ingest.name }
  service_account_id = each.value
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.deployer.email}"
}

# ---- Workload Identity Federation (GitHub OIDC → gh-deployer, no keys) ------------------------------
resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github-actions"
  display_name              = "GitHub Actions"
  description               = "OIDC federation for ${var.github_repo}"
  depends_on                = [google_project_service.apis]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-oidc"
  display_name                       = "GitHub OIDC"
  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
    "attribute.actor"      = "assertion.actor"
  }
  attribute_condition = "assertion.repository == '${var.github_repo}'"
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "deployer_wif" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repo}"
}
