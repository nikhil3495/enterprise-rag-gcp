"""Every graph path with injected fakes: vector / sql / both (parallel) / rewrite / abstain / regeneration."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from rag.config import Settings
from rag.graph.builder import build_graph, mermaid
from rag.graph.nodes import ABSTAIN_ANSWER, GraphDeps, build_sources, validate_citations
from rag.graph.reasoner import GeneratedAnswer, GroundingVerdict, RouteDecision
from rag.graph.service import RAGService
from rag.models import ScoredChunk, SqlResult
from rag.retrieval.hybrid import RetrievalResult
from rag.sql.text_to_sql import SqlDraft
from rag.telemetry import current_tracker

from .conftest import scored


@dataclass
class FakeReasoner:
    route_to: str = "vector"
    relevant: bool | list[bool] = True
    grounded_seq: list[bool] = field(default_factory=lambda: [True])
    answer: str = "PTO accrues at 1.67 days per month [S1]. Bogus [S9]."
    calls: list[str] = field(default_factory=list)
    name: str = "fake"

    def route(self, question: str, catalog_summary: str) -> RouteDecision:
        self.calls.append("route")
        return RouteDecision(route=self.route_to, reason="test")  # type: ignore[arg-type]

    def grade(self, question: str, chunks: list[ScoredChunk]) -> list[bool]:
        self.calls.append("grade")
        if isinstance(self.relevant, list):
            flag = self.relevant.pop(0) if self.relevant else False
            return [flag] * len(chunks)
        return [self.relevant] * len(chunks)

    def rewrite(self, question: str, previous_query: str, attempt: int) -> str:
        self.calls.append("rewrite")
        return f"{question} rewritten {attempt}"

    def draft_sql(self, question: str, schema: str, feedback: str | None = None) -> SqlDraft:
        return SqlDraft(sql="SELECT 1")

    def generate(self, question: str, sources: str, feedback: str | None = None) -> GeneratedAnswer:
        self.calls.append(f"generate:{'feedback' if feedback else 'first'}")
        tracker = current_tracker()
        if tracker:
            tracker.add_llm(
                name="generate",
                role="generation",
                model="m",
                input_tokens=100,
                output_tokens=50,
                latency_ms=500,
            )
        return GeneratedAnswer(answer=self.answer)

    def check_grounding(self, question: str, answer: str, sources: str) -> GroundingVerdict:
        self.calls.append("check_grounding")
        ok = self.grounded_seq.pop(0) if self.grounded_seq else True
        return GroundingVerdict(grounded=ok, unsupported_claims=[] if ok else ["claim X"])


class FakeRetriever:
    def __init__(self, delay: float = 0.0) -> None:
        self.queries: list[str] = []
        self.delay = delay
        self.threads: set[str] = set()

    def retrieve(
        self, query: str, top_k: int | None = None, candidate_k: int | None = None
    ) -> RetrievalResult:
        self.queries.append(query)
        self.threads.add(threading.current_thread().name)
        time.sleep(self.delay)
        hit = scored(
            "abc-0001",
            "Full-time employees accrue PTO at 1.67 days per month.",
            0.9,
            source="data/docs/employee_handbook.md",
            title="Handbook",
            section="PTO",
        )
        return RetrievalResult(query=query, candidates=[hit], results=[hit], timings_ms={"embed": 1.0})


class FakeSql:
    def __init__(self, ok: bool = True, delay: float = 0.0) -> None:
        self.ok, self.delay, self.calls = ok, delay, 0

    def run(self, question: str) -> SqlResult:
        self.calls += 1
        time.sleep(self.delay)
        tracker = current_tracker()
        if tracker:
            tracker.add_bigquery(bytes_billed=10 * 1024**2)
        if not self.ok:
            return SqlResult(ok=False, sql="SELECT x", error="boom", attempts=3)
        return SqlResult(ok=True, sql="SELECT 20 AS n", columns=["n"], rows=[[20]], row_count=1, attempts=1)


def run(reasoner: FakeReasoner, retriever: FakeRetriever | None = None, sql: FakeSql | None = None) -> dict:  # type: ignore[type-arg]
    deps = GraphDeps(
        reasoner=reasoner,
        retriever=retriever or FakeRetriever(),
        sql_tool=sql or FakeSql(),
        catalog_summary="- t: table",
        max_rewrites=2,
        max_regenerations=1,
    )
    return build_graph(deps).invoke({"question": "How much PTO?", "top_k": 3, "trace": []})


def nodes(state: dict) -> list[str]:  # type: ignore[type-arg]
    return [t["node"] for t in state["trace"]]


def test_vector_path_with_citation_validation() -> None:
    r = FakeReasoner()
    s = run(r)
    assert nodes(s) == ["route", "retrieve", "grade", "generate", "check_grounding"]
    assert s["route"] == "vector" and s["grounded"] is True
    assert "[S9]" not in s["answer"] and "[S1]" in s["answer"]
    assert [c.label for c in s["citations"]] == ["S1"]
    assert s["citations"][0].source_uri == "data/docs/employee_handbook.md"
    assert s["citations"][0].section == "PTO"


def test_sql_path_skips_retrieval() -> None:
    r = FakeReasoner(route_to="sql", answer="There were 20 tickets [SQL1].")
    retr = FakeRetriever()
    s = run(r, retr)
    assert nodes(s) == ["route", "run_sql", "grade", "generate", "check_grounding"]
    assert retr.queries == []
    assert s["citations"][0].kind == "sql" and s["citations"][0].sql == "SELECT 20 AS n"


def test_both_path_runs_retrieve_and_sql_in_parallel() -> None:
    r = FakeReasoner(route_to="both", answer="Warranty is 24 months [S1] and 20 tickets [SQL1].")
    retr, sql = FakeRetriever(delay=0.3), FakeSql(delay=0.3)
    t0 = time.perf_counter()
    s = run(r, retr, sql)
    elapsed = time.perf_counter() - t0
    assert set(nodes(s)[1:3]) == {"retrieve", "run_sql"}
    assert nodes(s)[3:] == ["grade", "generate", "check_grounding"]
    assert elapsed < 0.55  # parallel superstep, not 0.6s sequential
    assert {c.label for c in s["citations"]} == {"S1", "SQL1"}


def test_self_correcting_rewrite_loop_then_abstain() -> None:
    r = FakeReasoner(relevant=False)
    retr = FakeRetriever()
    s = run(r, retr)
    assert nodes(s) == [
        "route",
        "retrieve",
        "grade",
        "rewrite",
        "retrieve",
        "grade",
        "rewrite",
        "retrieve",
        "grade",
        "generate",
        "check_grounding",
    ]
    assert retr.queries[1].endswith("rewritten 1") and retr.queries[2].endswith("rewritten 2")
    assert s["insufficient_context"] is True and s["answer"] == ABSTAIN_ANSWER and s["citations"] == []
    assert "generate:first" not in r.calls  # abstain mode makes no LLM call


def test_rewrite_recovers_when_second_retrieval_is_relevant() -> None:
    r = FakeReasoner(relevant=[False, True])
    s = run(r)
    assert nodes(s).count("rewrite") == 1 and s["insufficient_context"] is False


def test_sql_failure_without_docs_abstains() -> None:
    s = run(FakeReasoner(route_to="sql"), sql=FakeSql(ok=False))
    assert s["insufficient_context"] is True and "rewrite" not in nodes(s)


def test_grounding_failure_regenerates_once_with_feedback() -> None:
    r = FakeReasoner(grounded_seq=[False, False])
    s = run(r)
    assert nodes(s) == [
        "route",
        "retrieve",
        "grade",
        "generate",
        "check_grounding",
        "generate",
        "check_grounding",
    ]
    assert r.calls.count("generate:feedback") == 1
    assert s["grounded"] is False and s["unsupported_claims"] == ["claim X"]


def test_grounding_regeneration_success() -> None:
    s = run(FakeReasoner(grounded_seq=[False, True]))
    assert s["grounded"] is True and s["generations"] == 2


def test_validate_citations_and_sources() -> None:
    sources, available = build_sources(
        [scored("a-0001", "text", page=3)],
        SqlResult(ok=True, sql="S", columns=["n"], rows=[[1]], row_count=1),
    )
    assert "[S1] Doc > S (page 3)" in sources and "[SQL1]" in sources
    text, cites = validate_citations("A [SQL1]. B [S1][S1]. C [S7] .", available)
    assert [c.label for c in cites] == ["SQL1", "S1"] and "[S7]" not in text and text.endswith("C.")


def test_service_tracks_usage_across_parallel_branches(settings: Settings) -> None:
    deps = GraphDeps(
        reasoner=FakeReasoner(route_to="both", answer="x [S1] [SQL1]"),
        retriever=FakeRetriever(),
        sql_tool=FakeSql(),
        catalog_summary="",
        max_rewrites=2,
    )
    r = RAGService(deps, settings).answer("q", request_id="rid-1")
    assert r.request_id == "rid-1" and r.route == "both"
    assert r.usage["input_tokens"] == 100 and r.usage["output_tokens"] == 50
    assert r.usage["output_tokens_per_s"] == 100.0  # 50 tokens / 0.5 s
    expected = (100 * 1.5 + 50 * 9.0) / 1e6 + (10 * 1024**2) / 1024**4 * 6.25
    assert abs(r.usage["cost_usd"] - round(expected, 6)) < 1e-6
    assert r.context.startswith("[S1]")


def test_mermaid_export_contains_all_nodes() -> None:
    deps = GraphDeps(
        reasoner=FakeReasoner(), retriever=FakeRetriever(), sql_tool=FakeSql(), catalog_summary=""
    )
    m = mermaid(deps)
    for n in ("route", "retrieve", "run_sql", "grade", "rewrite", "generate", "check_grounding"):
        assert n in m
