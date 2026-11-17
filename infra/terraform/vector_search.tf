# Vertex AI Vector Search: streaming index (dense 768-d + sparse BM25), public endpoint, smallest footprint.
# The deployed index bills per node-hour while deployed: use `make vs-undeploy` when idle.

resource "google_vertex_ai_index" "rag" {
  count               = var.deploy_vector_search ? 1 : 0
  region              = var.region
  display_name        = "rag-chunks"
  description         = "Hybrid (dense + sparse BM25) index of enterprise document chunks"
  index_update_method = "STREAM_UPDATE"
  labels              = local.labels

  metadata {
    config {
      dimensions                  = 768
      approximate_neighbors_count = 50
      distance_measure_type       = "DOT_PRODUCT_DISTANCE"
      feature_norm_type           = "UNIT_L2_NORM"
      shard_size                  = "SHARD_SIZE_SMALL"

      algorithm_config {
        tree_ah_config {
          leaf_node_embedding_count     = 500
          fraction_leaf_nodes_to_search = 0.1
        }
      }
    }
  }

  depends_on = [google_project_service.apis]
}

resource "google_vertex_ai_index_endpoint" "rag" {
  count                   = var.deploy_vector_search ? 1 : 0
  region                  = var.region
  display_name            = "rag-endpoint"
  description             = "Public endpoint for the RAG chunk index"
  public_endpoint_enabled = true
  labels                  = local.labels

  depends_on = [google_project_service.apis]
}

resource "google_vertex_ai_index_endpoint_deployed_index" "rag" {
  count             = var.deploy_vector_search && var.vs_deployed ? 1 : 0
  index_endpoint    = google_vertex_ai_index_endpoint.rag[0].id
  index             = google_vertex_ai_index.rag[0].id
  deployed_index_id = local.deployed_index_id
  display_name      = "rag-chunks-deployed"

  dedicated_resources {
    machine_spec {
      machine_type = var.vs_machine_type
    }
    min_replica_count = 1
    max_replica_count = 1
  }

  timeouts {
    create = "90m"
    update = "90m"
    delete = "60m"
  }
}
