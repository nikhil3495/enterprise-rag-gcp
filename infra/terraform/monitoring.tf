# Log-based metrics extracted from the one-line-per-query `rag_request` structured log.
locals {
  rag_request_filter = join(" AND ", [
    "resource.type=\"cloud_run_revision\"",
    "resource.labels.service_name=\"rag-api\"",
    "jsonPayload.message=\"rag_request\"",
  ])
}

resource "google_logging_metric" "latency" {
  name            = "rag_request_latency_ms"
  description     = "End-to-end graph latency per query (ms)"
  filter          = local.rag_request_filter
  value_extractor = "EXTRACT(jsonPayload.latency_ms)"

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "DISTRIBUTION"
    unit        = "ms"
    labels {
      key        = "route"
      value_type = "STRING"
    }
  }
  label_extractors = {
    route = "EXTRACT(jsonPayload.route)"
  }
  bucket_options {
    exponential_buckets {
      num_finite_buckets = 32
      growth_factor      = 1.4
      scale              = 50
    }
  }
  depends_on = [google_project_service.apis]
}

resource "google_logging_metric" "cost" {
  name            = "rag_request_cost_usd"
  description     = "Estimated model + retrieval + BigQuery cost per query (USD)"
  filter          = local.rag_request_filter
  value_extractor = "EXTRACT(jsonPayload.cost_usd)"

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "DISTRIBUTION"
    unit        = "USD"
  }
  bucket_options {
    exponential_buckets {
      num_finite_buckets = 30
      growth_factor      = 1.6
      scale              = 0.00005
    }
  }
  depends_on = [google_project_service.apis]
}

# Optional budget alert (requires billing.budgets.create on the billing account).
resource "google_billing_budget" "monthly" {
  count           = var.billing_account == "" ? 0 : 1
  billing_account = var.billing_account
  display_name    = "enterprise-rag monthly budget"

  budget_filter {
    projects = ["projects/${data.google_project.this.number}"]
  }

  amount {
    specified_amount {
      currency_code = var.budget_currency
      units         = tostring(var.budget_amount)
    }
  }

  threshold_rules {
    threshold_percent = 0.5
  }
  threshold_rules {
    threshold_percent = 0.9
  }
  threshold_rules {
    threshold_percent = 1.0
  }

  depends_on = [google_project_service.apis]
}
