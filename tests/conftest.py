from __future__ import annotations

from pathlib import Path

import pytest

from rag.config import Settings
from rag.container import Container
from rag.models import Chunk, ScoredChunk

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(__import__("os").environ):
        if key.startswith("RAG_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(ROOT)


def make_settings(tmp_path: Path, **kw: object) -> Settings:
    base: dict[str, object] = {
        "profile": "local",
        "state_uri": str(tmp_path / "state"),
        "docs_source": str(ROOT / "data" / "docs"),
        "structured_dir": str(ROOT / "data" / "structured"),
    }
    base.update(kw)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture(scope="session")
def ingested(tmp_path_factory: pytest.TempPathFactory) -> Container:
    tmp = tmp_path_factory.mktemp("ingested")
    c = Container(make_settings(tmp))
    c.pipeline.sync(c.settings.docs_source)
    return c


def chunk(
    cid: str,
    text: str,
    source: str = "data/docs/a.md",
    title: str = "Doc",
    section: str = "S",
    page: int | None = None,
) -> Chunk:
    return Chunk(
        chunk_id=cid,
        doc_id=cid.split("-")[0],
        source_uri=source,
        title=title,
        section=section,
        text=text,
        page=page,
    )


def scored(cid: str, text: str, score: float = 1.0, **kw: object) -> ScoredChunk:
    return ScoredChunk(chunk=chunk(cid, text, **kw), score=score, retrieval_score=score)  # type: ignore[arg-type]
