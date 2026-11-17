"""Dependency wiring: builds every component for the active profile from :class:`Settings`."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

from rag import storage
from rag.config import Settings
from rag.graph.nodes import GraphDeps
from rag.graph.reasoner import GeminiReasoner, HeuristicReasoner, Reasoner
from rag.graph.service import RAGService
from rag.ingestion.chunker import Chunker
from rag.ingestion.pipeline import IngestionPipeline
from rag.providers.base import Embedder, Reranker
from rag.providers.offline import HashEmbedder, LexicalReranker, PassthroughReranker
from rag.retrieval.hybrid import HybridRetriever
from rag.retrieval.sparse import BM25SparseEncoder
from rag.sql.catalog import Catalog
from rag.sql.engines import SqlEngine
from rag.sql.guard import SqlGuard
from rag.sql.text_to_sql import TextToSqlTool
from rag.store.base import DocStore, VectorStore

log = logging.getLogger(__name__)


@dataclass
class Container:
    settings: Settings

    # ---- paths ----------------------------------------------------------------------------------
    def state_path(self, name: str) -> str:
        return storage.join(self.settings.state_uri, name)

    @property
    def stats_uri(self) -> str:
        return self.state_path("sparse_stats.json")

    # ---- clients --------------------------------------------------------------------------------
    @cached_property
    def genai_client(self) -> Any:
        from rag.providers.gemini import make_client

        return make_client(self.settings.gcp_project, self.settings.genai_location)

    @cached_property
    def llm(self) -> Any:
        from rag.providers.gemini import GeminiLLM

        s = self.settings
        return GeminiLLM(
            client=self.genai_client,
            generation_model=s.generation_model,
            fast_model=s.fast_model,
            thinking_level=s.thinking_level,
            max_retries=s.llm_max_retries,
            timeout_s=s.llm_timeout_s,
        )

    # ---- components -----------------------------------------------------------------------------
    @cached_property
    def catalog(self) -> Catalog:
        s = self.settings
        yaml_catalog = Catalog.from_yaml(Path(s.structured_dir) / "schema.yaml")
        if s.component("sql_backend") == "bigquery":
            try:
                from google.cloud import bigquery

                client = bigquery.Client(project=s.gcp_project, location=s.bq_location)
                cat = Catalog.from_bigquery(client, s.gcp_project, s.bq_dataset)
                if cat.tables:
                    return cat
            except Exception as e:  # metadata unavailable: fall back to the same source of truth
                log.warning("bigquery catalog unavailable; using schema.yaml", extra={"error": str(e)[:200]})
        return yaml_catalog

    @cached_property
    def embedder(self) -> Embedder:
        s = self.settings
        if s.component("embedder") == "gemini":
            from rag.providers.gemini import GeminiEmbedder

            return GeminiEmbedder(
                client=self.genai_client,
                model=s.embedding_model,
                dim=s.embedding_dim,
                max_retries=s.llm_max_retries,
            )
        return HashEmbedder(dim=s.embedding_dim)

    @cached_property
    def vector_store(self) -> VectorStore:
        s = self.settings
        if s.component("vector_backend") == "vertex":
            from rag.store.vertex import VertexVectorStore

            return VertexVectorStore(
                project=s.gcp_project,
                region=s.region,
                index_id=s.vs_index_id,
                endpoint_id=s.vs_endpoint_id,
                deployed_index_id=s.vs_deployed_index_id,
                batch_size=s.vs_upsert_batch,
            )
        from rag.store.memory import MemoryVectorStore

        return MemoryVectorStore(dim=s.embedding_dim, snapshot_uri=self.state_path("memory_index.json"))

    @cached_property
    def docstore(self) -> DocStore:
        s = self.settings
        if s.component("docstore_backend") == "firestore":
            from rag.store.docstore import FirestoreDocStore

            return FirestoreDocStore(
                project=s.gcp_project,
                database=s.firestore_database,
                chunks_collection=s.firestore_chunks_collection,
                documents_collection=s.firestore_documents_collection,
            )
        from rag.store.docstore import LocalDocStore

        return LocalDocStore(self.state_path("docstore.json"))

    @cached_property
    def reranker(self) -> Reranker:
        s = self.settings
        choice = s.component("reranker")
        if choice == "vertex":
            from rag.providers.vertex_ranker import FallbackReranker, VertexRanker

            try:
                return FallbackReranker(
                    VertexRanker(project=s.gcp_project, model=s.ranking_model), LexicalReranker()
                )
            except Exception as e:
                log.warning("vertex ranker unavailable; lexical fallback", extra={"error": str(e)[:200]})
                return LexicalReranker()
        if choice == "none":
            return PassthroughReranker()
        return LexicalReranker()

    @cached_property
    def sparse_encoder(self) -> BM25SparseEncoder:
        return BM25SparseEncoder(ref_doc_len=int(self.settings.chunk_tokens * 0.7))

    @cached_property
    def reasoner(self) -> Reasoner:
        if self.settings.component("reasoner") == "gemini":
            return GeminiReasoner(self.llm)
        return HeuristicReasoner(self.catalog)

    @cached_property
    def sql_engine(self) -> SqlEngine:
        s = self.settings
        if s.component("sql_backend") == "bigquery":
            from rag.sql.bigquery_engine import BigQueryEngine

            return BigQueryEngine(
                project=s.gcp_project,
                location=s.bq_location,
                max_bytes_billed=s.bq_max_bytes_billed,
                timeout_s=s.bq_timeout_s,
            )
        from rag.sql.duckdb_engine import DuckDBEngine

        return DuckDBEngine(self.catalog, s.structured_dir)

    @cached_property
    def sql_tool(self) -> TextToSqlTool:
        s = self.settings
        guard = SqlGuard(
            project=s.sql_project,
            dataset=self.catalog.dataset,
            allowed_tables=self.catalog.table_names,
            max_limit=s.sql_row_limit,
        )
        return TextToSqlTool(
            drafter=self.reasoner,
            guard=guard,
            engine=self.sql_engine,
            catalog=self.catalog,
            project=s.sql_project,
            max_repairs=s.max_sql_repairs,
        )

    @cached_property
    def retriever(self) -> HybridRetriever:
        s = self.settings
        return HybridRetriever(
            embedder=self.embedder,
            sparse=self.sparse_encoder,
            vectors=self.vector_store,
            docstore=self.docstore,
            reranker=self.reranker,
            candidate_k=s.candidate_k,
            top_k=s.top_k,
            alpha=s.rrf_alpha,
            stats_uri=self.stats_uri,
        )

    @cached_property
    def pipeline(self) -> IngestionPipeline:
        s = self.settings
        return IngestionPipeline(
            chunker=Chunker(s.chunk_tokens, s.chunk_overlap),
            embedder=self.embedder,
            sparse=self.sparse_encoder,
            vectors=self.vector_store,
            docstore=self.docstore,
            stats_uri=self.stats_uri,
        )

    @cached_property
    def graph_deps(self) -> GraphDeps:
        s = self.settings
        return GraphDeps(
            reasoner=self.reasoner,
            retriever=self.retriever,
            sql_tool=self.sql_tool,
            catalog_summary=self.catalog.summary(),
            max_rewrites=s.max_rewrites,
            max_regenerations=s.max_regenerations,
            default_top_k=s.top_k,
        )

    @cached_property
    def service(self) -> RAGService:
        return RAGService(self.graph_deps, self.settings)

    def info(self) -> dict[str, Any]:
        s = self.settings
        return {
            "profile": s.profile,
            "reasoner": s.component("reasoner"),
            "embedder": s.component("embedder"),
            "vector_backend": s.component("vector_backend"),
            "docstore": s.component("docstore_backend"),
            "sql_backend": s.component("sql_backend"),
            "reranker": s.component("reranker"),
            "generation_model": s.generation_model if s.component("reasoner") == "gemini" else None,
            "fast_model": s.fast_model if s.component("reasoner") == "gemini" else None,
            "embedding_model": s.embedding_model if s.component("embedder") == "gemini" else "hash",
            "embedding_dim": s.embedding_dim,
            "candidate_k": s.candidate_k,
            "top_k": s.top_k,
            "rrf_alpha": s.rrf_alpha,
            "tables": self.catalog.table_names,
        }
