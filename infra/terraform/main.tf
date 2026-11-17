locals {
  services = [
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "aiplatform.googleapis.com",
    "bigquery.googleapis.com",
    "firestore.googleapis.com",
    "storage.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
    "discoveryengine.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "serviceusage.googleapis.com",
    "billingbudgets.googleapis.com",
  ]
  dataset_id        = "enterprise_analytics"
  deployed_index_id = "rag_chunks_deployed"
  # Deterministic Cloud Run URL (avoids a self-reference cycle for the in-app token audience).
  service_url = "https://rag-api-${data.google_project.this.number}.${var.region}.run.app"
  labels      = { app = "enterprise-rag", managed-by = "terraform" }
}

resource "google_project_service" "apis" {
  for_each           = toset(local.services)
  service            = each.value
  disable_on_destroy = false
}

# ---- Storage ------------------------------------------------------------------------------------
resource "google_storage_bucket" "data" {
  name                        = "${var.project_id}-rag-data"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = true
  labels                      = local.labels

  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      num_newer_versions = 5
      with_state         = "ARCHIVED"
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_project_service.apis]
}

resource "google_artifact_registry_repository" "rag" {
  location               = var.region
  repository_id          = "rag"
  format                 = "DOCKER"
  description            = "Enterprise RAG container images"
  cleanup_policy_dry_run = false
  labels                 = local.labels

  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 10
    }
  }

  cleanup_policies {
    id     = "delete-old"
    action = "DELETE"
    condition {
      older_than = "2592000s"
    }
  }

  depends_on = [google_project_service.apis]
}

# ---- Firestore (docstore) ---------------------------------------------------------------------------
resource "google_firestore_database" "default" {
  name                    = "(default)"
  location_id             = var.region
  type                    = "FIRESTORE_NATIVE"
  deletion_policy         = "DELETE"
  delete_protection_state = "DELETE_PROTECTION_DISABLED"

  depends_on = [google_project_service.apis]
}

# ---- BigQuery -------------------------------------------------------------------------------------
resource "google_bigquery_dataset" "analytics" {
  dataset_id                 = local.dataset_id
  location                   = "US"
  description                = "Helios Robotics sales, support and workforce analytics (synthetic data)."
  delete_contents_on_destroy = true
  labels                     = local.labels

  depends_on = [google_project_service.apis]
}
