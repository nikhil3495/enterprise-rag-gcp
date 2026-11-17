locals {
  vs_enabled = var.deploy_vector_search
  common_env = {
    RAG_PROFILE          = "prod"
    RAG_GCP_PROJECT      = var.project_id
    RAG_REGION           = var.region
    RAG_GENAI_LOCATION   = "global"
    RAG_GENERATION_MODEL = var.generation_model
    RAG_FAST_MODEL       = var.fast_model
    RAG_STATE_URI        = "gs://${google_storage_bucket.data.name}/state"
    RAG_DOCS_SOURCE      = "gs://${google_storage_bucket.data.name}/docs"
    RAG_GCS_BUCKET       = google_storage_bucket.data.name
    RAG_BQ_DATASET       = google_bigquery_dataset.analytics.dataset_id
    RAG_VECTOR_BACKEND   = local.vs_enabled ? "vertex" : "memory"
    RAG_VS_INDEX_ID      = local.vs_enabled ? google_vertex_ai_index.rag[0].id : ""
    RAG_VS_ENDPOINT_ID   = local.vs_enabled ? google_vertex_ai_index_endpoint.rag[0].id : ""
    # Known up front: lets the service deploy while the index is still being deployed (20-60 min).
    RAG_VS_DEPLOYED_INDEX_ID = local.vs_enabled ? local.deployed_index_id : ""
  }
  api_env = merge(local.common_env, {
    RAG_AUTH_MODE               = "iam"
    RAG_AUTH_AUDIENCE           = join(",", concat([local.service_url], var.extra_token_audiences))
    RAG_AUTH_ALLOWED_PRINCIPALS = join(",", [var.owner_email, google_service_account.deployer.email])
  })
}

resource "google_cloud_run_v2_service" "api" {
  name                = "rag-api"
  location            = var.region
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_ALL"
  labels              = local.labels

  template {
    service_account                  = google_service_account.api.email
    max_instance_request_concurrency = 20
    timeout                          = "120s"

    scaling {
      min_instance_count = 0
      max_instance_count = 5
    }

    containers {
      image = var.image

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }

      dynamic "env" {
        for_each = local.api_env
        content {
          name  = env.key
          value = env.value
        }
      }

      startup_probe {
        http_get {
          path = "/healthz"
        }
        initial_delay_seconds = 0
        period_seconds        = 3
        timeout_seconds       = 2
        failure_threshold     = 30
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].containers[0].image, client, client_version]
  }

  depends_on = [google_project_iam_member.api]
}

resource "google_cloud_run_v2_job" "ingest" {
  name                = "rag-ingest"
  location            = var.region
  deletion_protection = false
  labels              = local.labels

  template {
    task_count = 1

    template {
      service_account = google_service_account.ingest.email
      timeout         = "1800s"
      max_retries     = 1

      containers {
        image   = var.image
        command = ["rag"]
        args    = ["ingest", "--prune"]

        resources {
          limits = {
            cpu    = "1"
            memory = "2Gi"
          }
        }

        dynamic "env" {
          for_each = local.common_env
          content {
            name  = env.key
            value = env.value
          }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].template[0].containers[0].image, client, client_version]
  }

  depends_on = [google_project_iam_member.ingest]
}

# IAM-only access: no allUsers binding exists anywhere.
resource "google_cloud_run_v2_service_iam_member" "owner_invoker" {
  name     = google_cloud_run_v2_service.api.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "user:${var.owner_email}"
}

resource "google_cloud_run_v2_service_iam_member" "deployer_invoker" {
  name     = google_cloud_run_v2_service.api.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.deployer.email}"
}
