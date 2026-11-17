output "service_url" {
  value = google_cloud_run_v2_service.api.uri
}

output "expected_service_url" {
  description = "Deterministic URL used as the in-app token audience (must equal service_url)."
  value       = local.service_url
}

output "bucket" {
  value = google_storage_bucket.data.name
}

output "artifact_registry" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.rag.repository_id}"
}

output "vs_index_id" {
  value = var.deploy_vector_search ? google_vertex_ai_index.rag[0].id : ""
}

output "vs_endpoint_id" {
  value = var.deploy_vector_search ? google_vertex_ai_index_endpoint.rag[0].id : ""
}

output "vs_deployed_index_id" {
  value = var.deploy_vector_search ? local.deployed_index_id : ""
}

output "wif_provider" {
  value = google_iam_workload_identity_pool_provider.github.name
}

output "deployer_sa" {
  value = google_service_account.deployer.email
}

output "api_sa" {
  value = google_service_account.api.email
}

output "ingest_job" {
  value = google_cloud_run_v2_job.ingest.name
}
