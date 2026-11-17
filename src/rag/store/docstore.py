"""Chunk + document-manifest storage. Vector Search returns IDs only, so text lives here.

* :class:`LocalDocStore` — a single JSON file at ``<state_uri>/docstore.json`` (local or GCS).
* :class:`FirestoreDocStore` — collections ``rag_chunks`` and ``rag_documents``.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from typing import Any

from rag import storage
from rag.models import Chunk, DocumentRecord
from rag.store.base import batched


class LocalDocStore:
    name = "local"

    def __init__(self, uri: str | None = None) -> None:
        self.uri = uri
        self._lock = threading.RLock()
        self._chunks: dict[str, Chunk] = {}
        self._docs: dict[str, DocumentRecord] = {}
        if uri and storage.exists(uri):
            data = json.loads(storage.read_text(uri))
            self._chunks = {c["chunk_id"]: Chunk.model_validate(c) for c in data.get("chunks", [])}
            self._docs = {d["doc_id"]: DocumentRecord.model_validate(d) for d in data.get("documents", [])}

    def put_chunks(self, chunks: list[Chunk]) -> None:
        with self._lock:
            for c in chunks:
                self._chunks[c.chunk_id] = c

    def get_chunks(self, ids: list[str]) -> dict[str, Chunk]:
        with self._lock:
            return {i: self._chunks[i] for i in ids if i in self._chunks}

    def delete_chunks(self, ids: list[str]) -> None:
        with self._lock:
            for i in ids:
                self._chunks.pop(i, None)

    def iter_chunks(self) -> Iterator[Chunk]:
        with self._lock:
            items = list(self._chunks.values())
        yield from items

    def put_document(self, record: DocumentRecord) -> None:
        with self._lock:
            self._docs[record.doc_id] = record

    def get_document(self, doc_id: str) -> DocumentRecord | None:
        return self._docs.get(doc_id)

    def list_documents(self) -> list[DocumentRecord]:
        return sorted(self._docs.values(), key=lambda d: d.source_uri)

    def delete_document(self, doc_id: str) -> None:
        with self._lock:
            self._docs.pop(doc_id, None)

    def persist(self) -> None:
        if not self.uri:
            return
        with self._lock:
            payload = {
                "chunks": [c.model_dump() for c in sorted(self._chunks.values(), key=lambda c: c.chunk_id)],
                "documents": [d.model_dump() for d in self.list_documents()],
            }
        storage.write_text(self.uri, json.dumps(payload, indent=1), "application/json")


class FirestoreDocStore:
    name = "firestore"

    def __init__(
        self,
        *,
        project: str,
        database: str = "(default)",
        chunks_collection: str = "rag_chunks",
        documents_collection: str = "rag_documents",
        client: Any = None,
    ) -> None:
        if client is None:
            from google.cloud import firestore

            client = firestore.Client(project=project, database=database)
        self._db = client
        self._chunks = client.collection(chunks_collection)
        self._docs = client.collection(documents_collection)

    def put_chunks(self, chunks: list[Chunk]) -> None:
        for batch in batched(chunks, 400):
            wb = self._db.batch()
            for c in batch:
                wb.set(self._chunks.document(c.chunk_id), c.model_dump())
            wb.commit()

    def get_chunks(self, ids: list[str]) -> dict[str, Chunk]:
        if not ids:
            return {}
        refs = [self._chunks.document(i) for i in ids]
        out: dict[str, Chunk] = {}
        for snap in self._db.get_all(refs):
            if snap.exists:
                out[snap.id] = Chunk.model_validate(snap.to_dict())
        return out

    def delete_chunks(self, ids: list[str]) -> None:
        for batch in batched(ids, 400):
            wb = self._db.batch()
            for i in batch:
                wb.delete(self._chunks.document(i))
            wb.commit()

    def iter_chunks(self) -> Iterator[Chunk]:
        for snap in self._chunks.stream():
            yield Chunk.model_validate(snap.to_dict())

    def put_document(self, record: DocumentRecord) -> None:
        self._docs.document(record.doc_id).set(record.model_dump())

    def get_document(self, doc_id: str) -> DocumentRecord | None:
        snap = self._docs.document(doc_id).get()
        return DocumentRecord.model_validate(snap.to_dict()) if snap.exists else None

    def list_documents(self) -> list[DocumentRecord]:
        return sorted(
            (DocumentRecord.model_validate(s.to_dict()) for s in self._docs.stream()),
            key=lambda d: d.source_uri,
        )

    def delete_document(self, doc_id: str) -> None:
        self._docs.document(doc_id).delete()

    def persist(self) -> None:
        """Firestore writes are durable immediately."""
