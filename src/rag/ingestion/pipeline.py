"""Idempotent ingestion: load → chunk → embed (dense + sparse) → upsert → manifest.

* Unchanged documents (same content hash) are skipped.
* Changed documents have their stale chunk IDs deleted from the vector store and the docstore first.
* ``prune=True`` removes documents that disappeared from the source.
* Sparse document-frequency stats are recomputed from the docstore after every run and persisted
  next to the index (query-side IDF, see docs/adr/0001).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from rag import storage
from rag.ingestion.chunker import Chunker
from rag.ingestion.loaders import RawDocument, load_source
from rag.models import Chunk, DocumentRecord
from rag.observability import Timer
from rag.providers.base import Embedder
from rag.retrieval.sparse import BM25SparseEncoder
from rag.store.base import DocStore, VectorRecord, VectorStore, batched
from rag.text import doc_id_for

log = logging.getLogger(__name__)


@dataclass
class IngestReport:
    documents_seen: int = 0
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    pruned: list[str] = field(default_factory=list)
    chunks_upserted: int = 0
    chunks_deleted: int = 0
    vector_count: int | None = None
    seconds: float = 0.0

    def as_dict(self) -> dict[str, object]:
        return dict(self.__dict__.items())


class IngestionPipeline:
    def __init__(
        self,
        *,
        chunker: Chunker,
        embedder: Embedder,
        sparse: BM25SparseEncoder,
        vectors: VectorStore,
        docstore: DocStore,
        stats_uri: str,
        embed_batch: int = 32,
    ) -> None:
        self.chunker = chunker
        self.embedder = embedder
        self.sparse = sparse
        self.vectors = vectors
        self.docstore = docstore
        self.stats_uri = stats_uri
        self.embed_batch = embed_batch

    def _index_chunks(self, chunks: list[Chunk]) -> None:
        for batch in batched(chunks, self.embed_batch):
            dense = self.embedder.embed_documents([c.contextual_text for c in batch])
            records = [
                VectorRecord(c.chunk_id, dense[i], self.sparse.encode_document(c.contextual_text))
                for i, c in enumerate(batch)
            ]
            self.docstore.put_chunks(batch)  # docstore first: a vector hit must always resolve
            self.vectors.upsert(records)

    def _delete(self, record: DocumentRecord) -> int:
        if record.chunk_ids:
            self.vectors.remove(record.chunk_ids)
            self.docstore.delete_chunks(record.chunk_ids)
        return len(record.chunk_ids)

    def sync_documents(self, docs: list[RawDocument], *, prune: bool = False) -> IngestReport:
        timer = Timer()
        report = IngestReport(documents_seen=len(docs))
        seen: set[str] = set()
        for doc in docs:
            doc_id = doc_id_for(doc.source_uri)
            seen.add(doc_id)
            existing = self.docstore.get_document(doc_id)
            if existing and existing.content_hash == doc.content_hash:
                report.unchanged.append(doc.source_uri)
                continue
            chunks = self.chunker.chunk(doc)
            if existing:
                stale = [cid for cid in existing.chunk_ids if cid not in {c.chunk_id for c in chunks}]
                if stale:
                    self.vectors.remove(stale)
                    self.docstore.delete_chunks(stale)
                    report.chunks_deleted += len(stale)
            self._index_chunks(chunks)
            report.chunks_upserted += len(chunks)
            self.docstore.put_document(
                DocumentRecord(
                    doc_id=doc_id,
                    source_uri=doc.source_uri,
                    title=chunks[0].title if chunks else doc.title,
                    content_hash=doc.content_hash,
                    chunk_ids=[c.chunk_id for c in chunks],
                    ingested_at=datetime.now(UTC).isoformat(),
                )
            )
            (report.updated if existing else report.added).append(doc.source_uri)
            log.info("document indexed", extra={"source_uri": doc.source_uri, "chunks": len(chunks)})

        if prune:
            for record in self.docstore.list_documents():
                if record.doc_id not in seen:
                    report.chunks_deleted += self._delete(record)
                    self.docstore.delete_document(record.doc_id)
                    report.pruned.append(record.source_uri)

        self.refresh_stats()
        self.docstore.persist()
        self.vectors.persist()
        report.vector_count = self.vectors.count()
        report.seconds = round(timer.ms / 1000, 2)
        return report

    def sync(self, source: str, *, prune: bool = False) -> IngestReport:
        return self.sync_documents(load_source(source), prune=prune)

    def refresh_stats(self) -> None:
        vecs = [self.sparse.encode_document(c.contextual_text) for c in self.docstore.iter_chunks()]
        self.sparse.stats = BM25SparseEncoder.compute_stats(vecs)
        storage.write_text(self.stats_uri, self.sparse.stats.to_json(), "application/json")
