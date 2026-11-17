"""Runtime configuration (pydantic-settings, ``RAG_`` env prefix).

Two profiles share one codebase:

* ``local`` (default) — offline: heuristic reasoner, hash embeddings, in-memory hybrid index,
  JSON docstore, DuckDB. No credentials or network required (tests + CI).
* ``prod`` — Gemini on Vertex AI, Vertex AI Vector Search, Firestore, BigQuery, Vertex Ranking API.

Every component can be overridden individually (``RAG_VECTOR_BACKEND=memory`` etc.).
All model IDs and prices are configuration, never hard-coded in logic.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Profile = Literal["local", "prod"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RAG_", env_file=".env", extra="ignore")

    profile: Profile = "local"

    # --- GCP -----------------------------------------------------------------------------
    gcp_project: str = ""
    region: str = "us-central1"
    genai_location: str = "global"  # Gemini 3.x models are served from the global endpoint

    # --- Models (env-overridable) --------------------------------------------------------
    generation_model: str = "gemini-3.5-flash"
    fast_model: str = "gemini-3.5-flash-lite"
    embedding_model: str = "gemini-embedding-001"
    embedding_dim: int = 768
    thinking_level: str = "LOW"
    llm_timeout_s: float = 60.0
    llm_max_retries: int = 3

    # --- Prices (USD) ----------------------------------------------------------------------
    price_generation_input_per_m: float = 1.50
    price_generation_output_per_m: float = 9.00
    price_fast_input_per_m: float = 0.30
    price_fast_output_per_m: float = 2.50
    price_embedding_input_per_m: float = 0.15
    price_rerank_per_1k_queries: float = 1.00
    price_bigquery_per_tib: float = 6.25

    # --- Component selection ("auto" = decided by profile) ----------------------------------
    reasoner: Literal["auto", "gemini", "heuristic"] = "auto"
    embedder: Literal["auto", "gemini", "hash"] = "auto"
    vector_backend: Literal["auto", "memory", "vertex"] = "auto"
    docstore_backend: Literal["auto", "local", "firestore"] = "auto"
    sql_backend: Literal["auto", "duckdb", "bigquery"] = "auto"
    reranker: Literal["auto", "vertex", "lexical", "none"] = "auto"

    # --- Data / state ------------------------------------------------------------------------
    docs_source: str = "data/docs"  # local dir or gs://bucket/prefix
    structured_dir: str = "data/structured"
    state_uri: str = ".rag"  # local dir or gs://bucket/prefix (memory snapshot, sparse stats, local docstore)
    gcs_bucket: str = ""

    # --- Vertex AI Vector Search ---------------------------------------------------------------
    vs_index_id: str = ""
    vs_endpoint_id: str = ""
    vs_deployed_index_id: str = ""
    vs_upsert_batch: int = 100

    # --- Firestore -----------------------------------------------------------------------------
    firestore_database: str = "(default)"
    firestore_chunks_collection: str = "rag_chunks"
    firestore_documents_collection: str = "rag_documents"

    # --- BigQuery / text-to-SQL -----------------------------------------------------------------
    bq_dataset: str = "enterprise_analytics"
    bq_location: str = "US"
    bq_max_bytes_billed: int = 1_000_000_000
    bq_timeout_s: float = 30.0
    sql_row_limit: int = 200
    max_sql_repairs: int = 2

    # --- Chunking --------------------------------------------------------------------------------
    chunk_tokens: int = 350
    chunk_overlap: int = 60

    # --- Retrieval -------------------------------------------------------------------------------
    candidate_k: int = 30
    top_k: int = 6
    rrf_alpha: float = Field(default=0.6, ge=0.0, le=1.0)
    ranking_model: str = "semantic-ranker-default-004"

    # --- Graph -----------------------------------------------------------------------------------
    max_rewrites: int = 2
    max_regenerations: int = 1

    # --- API / auth ------------------------------------------------------------------------------
    auth_mode: Literal["none", "iam"] = "none"
    # Comma-separated accepted ID-token audiences: the Cloud Run service URL, plus optionally the gcloud CLI
    # OAuth client ID so `gcloud auth print-identity-token` user tokens work (see docs/adr/0004).
    auth_audience: str = ""
    auth_allowed_principals: str = ""  # comma-separated emails; empty = any IAM-authorised caller
    max_question_chars: int = 2000
    log_level: str = "INFO"

    # ---------------------------------------------------------------------------------------------
    def component(self, name: str) -> str:
        """Resolve an ``auto`` component choice from the active profile."""
        value = str(getattr(self, name))
        if value != "auto":
            return value
        prod = self.profile == "prod"
        defaults = {
            "reasoner": ("gemini", "heuristic"),
            "embedder": ("gemini", "hash"),
            "vector_backend": ("vertex", "memory"),
            "docstore_backend": ("firestore", "local"),
            "sql_backend": ("bigquery", "duckdb"),
            "reranker": ("vertex", "lexical"),
        }
        return defaults[name][0 if prod else 1]

    @property
    def allowed_principals(self) -> set[str]:
        return {p.strip().lower() for p in self.auth_allowed_principals.split(",") if p.strip()}

    @property
    def sql_project(self) -> str:
        return self.gcp_project or "local"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
