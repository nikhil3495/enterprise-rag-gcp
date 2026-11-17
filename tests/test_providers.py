"""Production adapters tested with stubbed clients (no network)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from pydantic import BaseModel

from rag.config import Settings
from rag.graph.reasoner import GeminiReasoner, GradeResult, HeuristicReasoner
from rag.models import Chunk, DocumentRecord, SparseVector
from rag.providers import gemini as gem
from rag.providers.gemini import GeminiEmbedder, GeminiLLM, LLMError
from rag.providers.offline import LexicalReranker
from rag.providers.vertex_ranker import FallbackReranker, VertexRanker
from rag.sql.bigquery_engine import BigQueryEngine
from rag.sql.catalog import Catalog
from rag.sql.engines import BytesLimitExceeded, SqlExecutionError
from rag.store.base import VectorRecord
from rag.store.docstore import FirestoreDocStore
from rag.store.vertex import VertexVectorStore
from rag.telemetry import track_usage

from .conftest import ROOT, scored


class APIError(Exception):
    def __init__(self, code: int, msg: str = "err") -> None:
        super().__init__(msg)
        self.code = code


class Answer(BaseModel):
    answer: str
    n: int = 0


def resp(text: str = "", parsed: Any = None, p: int = 10, c: int = 5, t: int = 2) -> SimpleNamespace:
    return SimpleNamespace(
        text=text,
        parsed=parsed,
        usage_metadata=SimpleNamespace(
            prompt_token_count=p, candidates_token_count=c, thoughts_token_count=t
        ),
    )


class FakeModels:
    def __init__(self, script: list[Any]) -> None:
        self.script = script
        self.configs: list[Any] = []

    def generate_content(self, *, model: str, contents: str, config: Any) -> Any:
        self.configs.append(config)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def llm(script: list[Any]) -> tuple[GeminiLLM, FakeModels]:
    models = FakeModels(script)
    return GeminiLLM(
        client=SimpleNamespace(models=models),
        generation_model="gen",
        fast_model="fast",
        thinking_level="LOW",
        max_retries=2,
    ), models


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gem.time, "sleep", lambda s: None)


def test_structured_parse_from_parsed_and_text_and_usage() -> None:
    m, models = llm([resp(parsed=Answer(answer="a")), resp(text='```json\n{"answer": "b", "n": 2}\n```')])
    with track_usage(Settings(_env_file=None)) as t:  # type: ignore[call-arg]
        assert (
            m.generate_structured(role="generation", name="g", system="s", prompt="p", schema=Answer).answer
            == "a"
        )
        assert m.generate_structured(role="fast", name="f", system="s", prompt="p", schema=Answer).n == 2
    s = t.summary()
    assert s.input_tokens == 20 and s.output_tokens == 10 and s.thinking_tokens == 4
    assert models.configs[0].thinking_config.thinking_level.value == "LOW"
    assert (
        models.configs[0].response_schema is Answer
        and models.configs[0].response_mime_type == "application/json"
    )


def test_parse_dict_and_garbage() -> None:
    assert GeminiLLM.parse(resp(parsed={"answer": "x"}), Answer).answer == "x"
    with pytest.raises(LLMError):
        GeminiLLM.parse(resp(text="not json"), Answer)


def test_thinking_rejected_retries_without_and_remembers() -> None:
    m, models = llm(
        [
            APIError(400, "thinking_level is not supported"),
            resp(text='{"answer":"ok"}'),
            resp(text='{"answer":"ok2"}'),
        ]
    )
    assert m.generate_structured(role="fast", name="r", system="s", prompt="p", schema=Answer).answer == "ok"
    assert models.configs[1].thinking_config is None
    m.generate_structured(role="fast", name="r", system="s", prompt="p", schema=Answer)
    assert models.configs[2].thinking_config is None and "fast" in m._no_thinking


def test_retries_on_429_then_fails_with_llm_error() -> None:
    m, _ = llm([APIError(429), resp(text='{"answer":"ok"}')])
    assert m.generate_structured(role="fast", name="r", system="s", prompt="p", schema=Answer).answer == "ok"
    m2, _ = llm([APIError(503), APIError(503), APIError(503)])
    with pytest.raises(LLMError):
        m2.generate_structured(role="fast", name="r", system="s", prompt="p", schema=Answer)
    m3, models3 = llm([APIError(403, "permission denied")])
    with pytest.raises(LLMError):
        m3.generate_structured(role="fast", name="r", system="s", prompt="p", schema=Answer)
    assert len(models3.configs) == 1  # non-retryable


class FakeEmbedModels:
    def __init__(self, reject_batch: bool) -> None:
        self.reject_batch = reject_batch
        self.calls: list[int] = []

    def embed_content(self, *, model: str, contents: list[str], config: Any) -> Any:
        self.calls.append(len(contents))
        if self.reject_batch and len(contents) > 1:
            raise APIError(400, "only one input supported")
        assert config.output_dimensionality == 4
        embs = [
            SimpleNamespace(values=[3.0, 4.0, 0.0, 0.0], statistics=SimpleNamespace(token_count=3))
            for _ in contents
        ]
        return SimpleNamespace(embeddings=embs)


@pytest.mark.parametrize("reject", [False, True])
def test_embedder_batches_normalises_and_falls_back(reject: bool) -> None:
    models = FakeEmbedModels(reject)
    e = GeminiEmbedder(client=SimpleNamespace(models=models), dim=4, batch_size=2)
    with track_usage(Settings(_env_file=None)) as t:  # type: ignore[call-arg]
        m = e.embed_documents(["a", "b", "c"])
    assert m.shape == (3, 4) and np.allclose(np.linalg.norm(m, axis=1), 1.0)
    assert np.allclose(m[0], [0.6, 0.8, 0, 0])
    assert t.summary().input_tokens == 9
    assert e.single_input_only is reject
    if reject:
        assert models.calls[0] == 2 and models.calls[1:] == [1, 1, 1]
    assert e.embed_query("q").shape == (4,)
    assert e.embed_documents([]).shape == (0, 4)


def test_gemini_reasoner_prompts_and_grade_defaults() -> None:
    class StubLLM:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def generate_structured(self, **kw: Any) -> Any:
            self.calls.append(kw)
            schema = kw["schema"]
            if schema is GradeResult:
                return GradeResult(grades=[{"id": "0", "relevant": False}])  # type: ignore[list-item]
            fields = {
                "route": "sql",
                "query": " better query ",
                "sql": "SELECT 1",
                "answer": "a [S1]",
                "grounded": True,
            }
            return schema(**{k: v for k, v in fields.items() if k in schema.model_fields})

    stub = StubLLM()
    r = GeminiReasoner(stub)
    assert r.route("q", "- t: d").route == "sql" and "- t: d" in stub.calls[-1]["system"]
    assert r.grade("q", [scored("a-1", "x"), scored("a-2", "y")]) == [False, True]  # missing grade → keep
    assert r.grade("q", []) == []
    assert r.rewrite("q", "q", 1) == "better query"
    assert r.draft_sql("q", "SCHEMA", "fix: err").sql == "SELECT 1"
    assert "fix: err" in stub.calls[-1]["prompt"] and "SCHEMA" in stub.calls[-1]["system"]
    r.generate("q", "[S1] src", feedback="- claim")
    assert stub.calls[-1]["role"] == "generation" and "- claim" in stub.calls[-1]["prompt"]
    assert "untrusted" in stub.calls[-1]["system"]
    assert r.check_grounding("q", "a", "s").grounded


def test_heuristic_reasoner_routes_and_drafts() -> None:
    h = HeuristicReasoner(Catalog.from_yaml(ROOT / "data" / "structured" / "schema.yaml"))
    assert h.route("What was the total revenue in EMEA in 2025?", "").route == "sql"
    assert h.route("What is the minimum password length?", "").route == "vector"
    assert (
        h.route("What is the Atlas warranty and how many SEV1 tickets were opened in 2025?", "").route
        == "both"
    )
    sql = h.draft_sql("Which product had the most support tickets in 2025?", "").sql
    assert "GROUP BY product" in sql and "LIMIT 1" in sql and "EXTRACT(YEAR FROM created_at) = 2025" in sql
    q = h.draft_sql("How many Atlas AMR units were sold in Q1 2026?", "").sql
    assert "SUM(units)" in q and "product = 'Atlas AMR'" in q and "BETWEEN '2026-01-01' AND '2026-03-31'" in q
    hc = h.draft_sql("Engineering headcount in Berlin in 2026-Q3?", "").sql
    assert "quarter = '2026-Q3'" in hc and "location = 'Berlin'" in hc
    assert "region = 'NA'" not in h.draft_sql("total revenue from national accounts", "").sql
    assert h.generate("q", "").insufficient_context
    assert not h.check_grounding("q", "There were 5 incidents last year.", "").grounded
    assert "paid time off" in h.rewrite("How much PTO?", "", 1)


# ---- Vertex Vector Search -----------------------------------------------------------------------
def test_vertex_vector_store_batches_and_hybrid_query() -> None:
    upserts: list[int] = []
    removed: list[list[str]] = []
    index = SimpleNamespace(
        upsert_datapoints=lambda datapoints: upserts.append(len(datapoints)),
        remove_datapoints=lambda datapoint_ids: removed.append(list(datapoint_ids)),
    )
    captured: dict[str, Any] = {}

    def find_neighbors(**kw: Any) -> Any:
        captured.update(kw)
        return [
            [
                SimpleNamespace(id="c1", distance=0.9, sparse_distance=2.0),
                SimpleNamespace(id="c2", distance=0.5, sparse_distance=None),
            ]
        ]

    vs = VertexVectorStore(
        project="p",
        region="r",
        index_id="i",
        endpoint_id="e",
        deployed_index_id="d",
        batch_size=100,
        index=index,
        endpoint=SimpleNamespace(find_neighbors=find_neighbors),
    )
    recs = [
        VectorRecord(f"id{i}", np.ones(3), SparseVector(dimensions=[1, 5], values=[0.5, 0.2]))
        for i in range(250)
    ]
    vs.upsert(recs)
    assert upserts == [100, 100, 50]
    vs.remove([f"id{i}" for i in range(150)])
    assert [len(b) for b in removed] == [100, 50]
    hits = vs.query(np.ones(3), SparseVector(dimensions=[1], values=[2.0]), k=5, alpha=0.6)
    q = captured["queries"][0]
    assert captured["deployed_index_id"] == "d" and captured["num_neighbors"] == 5
    assert q.rrf_ranking_alpha == 0.6 and q.sparse_embedding_dimensions == [1]
    assert [h.id for h in hits] == ["c1", "c2"] and hits[0].score > hits[1].score
    dp = VertexVectorStore._datapoint(recs[0])
    assert dp.datapoint_id == "id0" and list(dp.sparse_embedding.dimensions) == [1, 5]


# ---- Ranking API ----------------------------------------------------------------------------------
class FakeRankClient:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.request: Any = None

    @staticmethod
    def ranking_config_path(project: str, location: str, config: str) -> str:
        return f"projects/{project}/locations/{location}/rankingConfigs/{config}"

    def rank(self, request: Any, timeout: float) -> Any:
        if self.fail:
            raise RuntimeError("ranking api disabled")
        self.request = request
        return SimpleNamespace(
            records=[SimpleNamespace(id="b-0001", score=0.97), SimpleNamespace(id="a-0001", score=0.1)]
        )


def test_vertex_ranker_and_fallback() -> None:
    cands = [scored("a-0001", "travel per diem"), scored("b-0001", "password length")]
    client = FakeRankClient()
    vr = VertexRanker(project="p", client=client)
    with track_usage(Settings(_env_file=None)) as t:  # type: ignore[call-arg]
        out = vr.rerank("password", cands, 2)
    assert [c.chunk.chunk_id for c in out] == ["b-0001", "a-0001"] and out[0].rerank_score == 0.97
    assert client.request.model == "semantic-ranker-default-004"
    assert client.request.ranking_config.endswith("rankingConfigs/default_ranking_config")
    assert t.summary().rerank_calls == 1 and t.summary().cost_usd == pytest.approx(0.001)
    assert vr.rerank("q", [], 3) == []
    fb = FallbackReranker(VertexRanker(project="p", client=FakeRankClient(fail=True)), LexicalReranker())
    assert fb.rerank("password length", cands, 1)[0].chunk.chunk_id == "b-0001" and fb.last_used == "lexical"


# ---- BigQuery -------------------------------------------------------------------------------------
class FakeJob:
    def __init__(self, processed: int, rows: list[dict[str, Any]] | None = None, fail: bool = False) -> None:
        self.total_bytes_processed = processed
        self.total_bytes_billed = processed
        self._rows = rows or []
        self.fail = fail

    def result(self, timeout: float) -> Any:
        if self.fail:
            raise RuntimeError("Syntax error: Unexpected keyword\nmore")
        schema = [SimpleNamespace(name=k) for k in (self._rows[0] if self._rows else {})]
        rows = [SimpleNamespace(values=lambda r=r: list(r.values())) for r in self._rows]
        return _Result(schema, rows)


class _Result(list):  # type: ignore[type-arg]
    def __init__(self, schema: list[Any], rows: list[Any]) -> None:
        super().__init__(rows)
        self.schema = schema


def test_bigquery_engine_dry_run_limit_execute_and_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    jobs: list[Any] = []

    class Client:
        def query(self, sql: str, job_config: Any) -> FakeJob:
            jobs.append(job_config)
            if job_config.dry_run:
                return FakeJob(5_000_000_000 if "big" in sql else 1000)
            return FakeJob(1024**3, rows=[{"n": 51}], fail="bad" in sql)

    eng = BigQueryEngine(project="p", max_bytes_billed=1_000_000_000, client=Client())
    with pytest.raises(BytesLimitExceeded):
        eng.dry_run("SELECT big")
    assert eng.dry_run("SELECT small") == 1000
    with track_usage(Settings(_env_file=None)) as t:  # type: ignore[call-arg]
        out = eng.execute("SELECT n")
    assert out.columns == ["n"] and out.rows == [[51]]
    assert jobs[-1].maximum_bytes_billed == 1_000_000_000 and jobs[-1].labels["app"] == "enterprise-rag"
    assert t.summary().cost_usd == pytest.approx(6.25 / 1024)
    with pytest.raises(SqlExecutionError, match=r"Syntax error: Unexpected keyword$"):
        eng.execute("SELECT bad")


def test_catalog_from_bigquery_metadata() -> None:
    fields = [
        SimpleNamespace(
            name="region", field_type="STRING", description="Sales region. Allowed values: NA, EMEA."
        ),
        SimpleNamespace(name="units", field_type="INT64", description=None),
    ]
    table = SimpleNamespace(table_id="sales_orders", description="Orders", schema=fields)
    client = SimpleNamespace(
        list_tables=lambda ds: [SimpleNamespace(reference="ref")], get_table=lambda ref: table
    )
    cat = Catalog.from_bigquery(client, "p", "enterprise_analytics")
    col = cat.tables[0].columns[0]
    assert col.allowed_values == ["NA", "EMEA"] and col.description == "Sales region."
    assert "Allowed values: NA, EMEA." in col.full_description


# ---- Firestore ------------------------------------------------------------------------------------
class FakeSnap:
    def __init__(self, id: str, data: dict[str, Any] | None) -> None:
        self.id, self._data, self.exists = id, data, data is not None

    def to_dict(self) -> dict[str, Any] | None:
        return self._data


class FakeDocRef:
    def __init__(self, coll: FakeColl, id: str) -> None:
        self.coll, self.id = coll, id

    def set(self, data: dict[str, Any]) -> None:
        self.coll.data[self.id] = data

    def get(self) -> FakeSnap:
        return FakeSnap(self.id, self.coll.data.get(self.id))

    def delete(self) -> None:
        self.coll.data.pop(self.id, None)


class FakeColl:
    def __init__(self) -> None:
        self.data: dict[str, dict[str, Any]] = {}

    def document(self, id: str) -> FakeDocRef:
        return FakeDocRef(self, id)

    def stream(self) -> list[FakeSnap]:
        return [FakeSnap(k, v) for k, v in self.data.items()]


class FakeBatch:
    def __init__(self) -> None:
        self.ops: list[Any] = []

    def set(self, ref: FakeDocRef, data: dict[str, Any]) -> None:
        self.ops.append(lambda: ref.set(data))

    def delete(self, ref: FakeDocRef) -> None:
        self.ops.append(ref.delete)

    def commit(self) -> None:
        for op in self.ops:
            op()


class FakeFirestore:
    def __init__(self) -> None:
        self.colls: dict[str, FakeColl] = {}

    def collection(self, name: str) -> FakeColl:
        return self.colls.setdefault(name, FakeColl())

    def batch(self) -> FakeBatch:
        return FakeBatch()

    def get_all(self, refs: list[FakeDocRef]) -> list[FakeSnap]:
        return [r.get() for r in refs]


def test_firestore_docstore_roundtrip() -> None:
    fs = FirestoreDocStore(project="p", client=FakeFirestore())
    chunks = [
        Chunk(chunk_id=f"d-{i:04d}", doc_id="d", source_uri="gs://b/x.md", title="T", text=f"t{i}")
        for i in range(3)
    ]
    fs.put_chunks(chunks)
    assert set(fs.get_chunks(["d-0000", "d-0002", "missing"])) == {"d-0000", "d-0002"}
    assert fs.get_chunks([]) == {}
    fs.delete_chunks(["d-0001"])
    assert len(list(fs.iter_chunks())) == 2
    fs.put_document(DocumentRecord(doc_id="d", source_uri="gs://b/x.md", title="T", content_hash="h"))
    assert fs.get_document("d") is not None and fs.get_document("zz") is None
    assert [d.doc_id for d in fs.list_documents()] == ["d"]
    fs.delete_document("d")
    assert fs.list_documents() == []
    fs.persist()
