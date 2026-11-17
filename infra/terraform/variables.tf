variable "project_id" {
  description = "GCP project ID."
  type        = string
}

variable "region" {
  description = "Region for Cloud Run, Vector Search, Artifact Registry and GCS."
  type        = string
  default     = "us-central1"
}

variable "owner_email" {
  description = "Owner's Google account; granted run.invoker and allow-listed in-app."
  type        = string
}

variable "github_repo" {
  description = "owner/repo allowed to deploy through Workload Identity Federation."
  type        = string
  default     = "nikhil3495/enterprise-rag-gcp"
}

variable "image" {
  description = "Initial container image. CI owns subsequent rollouts (lifecycle.ignore_changes)."
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "deploy_vector_search" {
  description = "Create the Vertex AI Vector Search index + endpoint + deployed index. false = memory/GCS fallback."
  type        = bool
  default     = true
}

variable "vs_deployed" {
  description = "Keep the index deployed (billed per node-hour). false = `make vs-undeploy`: index data is kept."
  type        = bool
  default     = true
}

variable "vs_machine_type" {
  description = "Machine for the deployed index (SHARD_SIZE_SMALL supports e2-standard-2)."
  type        = string
  default     = "e2-standard-2"
}

variable "generation_model" {
  type    = string
  default = "gemini-3.5-flash"
}

variable "fast_model" {
  type    = string
  default = "gemini-3.5-flash-lite"
}

variable "billing_account" {
  description = "Billing account ID for the budget alert (empty = skip budget)."
  type        = string
  default     = ""
}

variable "budget_amount" {
  description = "Monthly budget in the billing account currency (INR 2000 ~= USD 25)."
  type        = number
  default     = 2000
}

variable "budget_currency" {
  type    = string
  default = "INR"
}

# gcloud CLI's OAuth client ID: `gcloud auth print-identity-token` user tokens carry this audience.
# Accepted in-app (together with the service URL) so the owner can curl the service; Cloud Run IAM and the
# principal allowlist remain the access boundary. See docs/adr/0004-iam-and-wif-auth.md.
variable "extra_token_audiences" {
  type    = list(string)
  default = ["32555940559.apps.googleusercontent.com"]
}
