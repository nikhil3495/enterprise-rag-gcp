from __future__ import annotations

import shutil
from pathlib import Path

from rag import storage
from rag.container import Container

from .conftest import ROOT, make_settings


def _container(tmp_path: Path) -> tuple[Container, Path]:
    src = tmp_path / "docs"
    src.mkdir()
    for name in ("employee_handbook.md", "travel_policy.html", "it_service_desk_faq.txt"):
        shutil.copy(ROOT / "data" / "docs" / name, src / name)
    return Container(make_settings(tmp_path, docs_source=str(src))), src


def test_ingestion_is_idempotent_and_handles_updates_and_prune(tmp_path: Path) -> None:
    c, src = _container(tmp_path)
    r1 = c.pipeline.sync(str(src))
    assert len(r1.added) == 3 and r1.chunks_upserted > 0 and r1.vector_count == r1.chunks_upserted
    assert storage.exists(c.stats_uri)

    r2 = c.pipeline.sync(str(src))
    assert len(r2.unchanged) == 3 and r2.chunks_upserted == 0 and r2.chunks_deleted == 0

    # shrink a document: stale chunk ids must disappear from both stores
    (src / "employee_handbook.md").write_text(
        "# Handbook\n\n## PTO\n\nOnly one short section now.\n", encoding="utf-8"
    )
    before = c.vector_store.count()
    r3 = c.pipeline.sync(str(src))
    assert r3.updated and r3.chunks_deleted > 0
    assert c.vector_store.count() == before - r3.chunks_deleted
    handbook = next(d for d in c.docstore.list_documents() if d.source_uri.endswith("employee_handbook.md"))
    assert len(handbook.chunk_ids) == 1
    assert set(c.docstore.get_chunks(handbook.chunk_ids)) == set(handbook.chunk_ids)

    # remove a file: only --prune deletes it
    (src / "travel_policy.html").unlink()
    assert not c.pipeline.sync(str(src)).pruned
    r5 = c.pipeline.sync(str(src), prune=True)
    assert len(r5.pruned) == 1 and len(c.docstore.list_documents()) == 2
    assert c.vector_store.count() == sum(len(d.chunk_ids) for d in c.docstore.list_documents())
    assert sum(1 for _ in c.docstore.iter_chunks()) == c.vector_store.count()


def test_state_survives_restart(tmp_path: Path) -> None:
    c, src = _container(tmp_path)
    c.pipeline.sync(str(src))
    fresh = Container(c.settings)
    assert fresh.vector_store.count() == c.vector_store.count()
    assert len(fresh.docstore.list_documents()) == 3
    assert fresh.retriever.sparse.stats.n_docs == c.vector_store.count()
    res = fresh.retriever.retrieve("how often are laptops refreshed", top_k=2)
    assert res.results[0].chunk.source_name == "it_service_desk_faq.txt"


def test_storage_local_helpers(tmp_path: Path) -> None:
    uri = storage.join(str(tmp_path), "a", "b.txt")
    storage.write_text(uri, "hello")
    assert storage.read_text(uri) == "hello" and storage.exists(uri)
    assert storage.list_files(str(tmp_path)) == [uri]
    assert storage.list_files(uri) == [uri]
    assert storage.join("gs://bucket/state/", "/x.json") == "gs://bucket/state/x.json"
    assert storage.is_gcs("gs://b/x") and not storage.is_gcs("/tmp/x")
