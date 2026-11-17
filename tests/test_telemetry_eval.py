from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

import pytest

from rag.config import Settings
from rag.eval import metrics as M
from rag.eval.judge import HeuristicJudge
from rag.eval.runner import (
    ItemResult,
    Observation,
    aggregate,
    check_thresholds,
    evaluate_item,
    render_markdown,
    stratified,
)
from rag.observability import JsonFormatter, request_id_var, trace_resource, trace_var
from rag.telemetry import PriceTable, UsageTracker, current_tracker, track_usage


def test_cost_math_thinking_billed_as_output() -> None:
    p = PriceTable(Settings(_env_file=None))  # type: ignore[call-arg]
    assert p.llm("generation", 1_000_000, 0, 0) == pytest.approx(1.50)
    assert p.llm("generation", 0, 500_000, 500_000) == pytest.approx(9.00)
    assert p.llm("fast", 1_000_000, 1_000_000, 0) == pytest.approx(0.30 + 2.50)
    assert p.rerank(1000) == pytest.approx(1.0)
    assert p.bigquery(1024**4) == pytest.approx(6.25)
    assert p.embedding(1_000_000) == pytest.approx(0.15)


def test_prices_are_env_overridable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_PRICE_GENERATION_OUTPUT_PER_M", "1.0")
    assert PriceTable(Settings(_env_file=None)).llm("generation", 0, 1_000_000, 0) == pytest.approx(1.0)  # type: ignore[call-arg]


def test_tracker_is_thread_safe_and_summarises() -> None:
    t = UsageTracker(PriceTable(Settings(_env_file=None)))  # type: ignore[call-arg]

    def work() -> None:
        for _ in range(100):
            t.add_llm(name="g", role="fast", model="m", input_tokens=1, output_tokens=1)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    t.add_llm(
        name="gen",
        role="generation",
        model="m",
        input_tokens=10,
        output_tokens=200,
        thinking_tokens=5,
        latency_ms=2000,
    )
    t.add_embedding(model="e", input_tokens=7)
    t.add_rerank(model="r")
    s = t.summary()
    assert s.llm_calls == 801 and s.input_tokens == 800 + 10 + 7
    assert s.output_tokens_per_s == pytest.approx(100.0)
    assert s.thinking_tokens == 5 and s.rerank_calls == 1 and s.embedding_calls == 1
    assert set(s.as_dict()) >= {"cost_usd", "output_tokens_per_s"}


def test_track_usage_context() -> None:
    assert current_tracker() is None
    with track_usage(Settings(_env_file=None)) as t:  # type: ignore[call-arg]
        assert current_tracker() is t
    assert current_tracker() is None


def test_json_log_format_has_trace_and_request_id() -> None:
    rid, tr = request_id_var.set("r1"), trace_var.set(trace_resource("abc123/1;o=1", "proj"))
    try:
        rec = logging.LogRecord("x", logging.INFO, __file__, 1, "rag_request", (), None)
        rec.latency_ms = 12.5
        out = json.loads(JsonFormatter().format(rec))
    finally:
        request_id_var.reset(rid)
        trace_var.reset(tr)
    assert out["severity"] == "INFO" and out["request_id"] == "r1" and out["latency_ms"] == 12.5
    assert out["logging.googleapis.com/trace"] == "projects/proj/traces/abc123"
    assert trace_resource(None, "p") is None


# ---- eval metrics -------------------------------------------------------------------------------
def test_recall_mrr_hit() -> None:
    ranked = ["gs://b/docs/a.md", "data/docs/a.md", "x/b.md", "c.md"]
    assert M.unique_sources(ranked) == ["a.md", "b.md", "c.md"]
    assert M.recall_at_k(ranked, ["b.md"], 1) == 0.0
    assert M.recall_at_k(ranked, ["b.md", "c.md"], 2) == 0.5
    assert M.recall_at_k(ranked, [], 5) is None
    assert M.reciprocal_rank(ranked, ["c.md"]) == pytest.approx(1 / 3)
    assert M.reciprocal_rank(ranked, ["z.md"]) == 0.0
    assert M.hit_at_k(ranked, ["b.md"], 2) == 1.0


def test_percentile_and_mean() -> None:
    assert M.percentile([1, 2, 3, 4], 50) == 2.5
    assert M.percentile(list(range(1, 101)), 95) == pytest.approx(95.05)
    assert M.percentile([], 95) is None and M.percentile([7], 99) == 7.0
    assert M.mean([1, None, 3]) == 2 and M.mean([]) is None


@pytest.mark.parametrize(
    ("answer", "item", "ok"),
    [
        ("Revenue was $136,791,603.60 [SQL1].", "136791603.60", True),
        ("Revenue was about $136.79 million.", "136791603.60", True),
        ("Revenue was $140 million.", "136791603.60", False),
        ("There were 51 tickets.", "51", True),
        ("There were 510 tickets.", "51", False),
        ("Average 22.2 hours", "22.2", True),
        ("Average 22.24 hours", "22.2", True),
        ("NA led revenue.", "NA", True),
        ("National revenue", "NA", False),
        ("must be used by march 31", "March 31", True),
        ("FIDO2 keys", "FIDO2", True),
        ("Helios OS 4.4 added SSO", "4.4", True),
    ],
)
def test_contains_item(answer: str, item: str, ok: bool) -> None:
    assert M.contains_item(answer, item) is ok


def test_answer_correctness_fraction() -> None:
    assert M.answer_correctness("5 days by March 31", ["5", "March 31"]) == 1.0
    assert M.answer_correctness("5 days", ["5", "March 31"]) == 0.5
    assert M.answer_correctness("x", []) is None


def test_heuristic_judge() -> None:
    j = HeuristicJudge()
    ctx = "[S1] Passwords must be at least 14 characters long."
    assert j.faithfulness("q", "Passwords must be at least 14 characters [S1].", ctx, False).score == 1.0
    bad = j.faithfulness(
        "q", "Passwords must be 20 characters [S1]. Rotate quarterly via the moon.", ctx, False
    )
    assert bad.score == 0.0 and len(bad.unsupported) == 2
    assert j.faithfulness("q", "anything", "", True).score == 1.0


def _obs(**kw: object) -> Observation:
    base: dict[str, object] = {
        "answer": "Passwords must be 14 characters [S1].",
        "route": "vector",
        "grounded": True,
        "insufficient_context": False,
        "candidate_sources": ["x/sec.md", "x/h.md"],
        "reranked_sources": ["x/h.md", "x/sec.md"],
        "context": "[S1] Passwords must be 14 characters.",
        "usage": {"cost_usd": 0.001, "output_tokens_per_s": 120.0},
        "latency_ms": 900.0,
    }
    base.update(kw)
    return Observation(**base)  # type: ignore[arg-type]


def test_evaluate_aggregate_thresholds_and_markdown() -> None:
    item = {
        "id": "d1",
        "category": "document",
        "question": "pw?",
        "expected_route": "vector",
        "relevant_sources": ["sec.md"],
        "must_contain": ["14"],
    }
    oos = {
        "id": "o1",
        "category": "out_of_scope",
        "question": "capital?",
        "expected_route": "vector",
        "relevant_sources": [],
        "must_contain": [],
    }
    j = HeuristicJudge()
    results = [
        evaluate_item(item, _obs(), j),
        evaluate_item(oos, _obs(insufficient_context=True, answer="no"), j),
        evaluate_item(item, _obs(error="HTTP 503"), j),
    ]
    r0 = results[0]
    assert (
        r0.recall_pre["@1"] == 1.0
        and r0.recall_post["@1"] == 0.0
        and r0.rr_post == 0.5
        and r0.correctness == 1.0
    )
    s = aggregate(results)
    assert s["n_errors"] == 1 and s["abstention_accuracy"] == 1.0 and s["recall"]["@3"] == 1.0
    assert s["latency_ms"]["p95"] == 900.0 and s["cost_per_request_usd"]["total"] == 0.002
    assert check_thresholds(s, {"n_errors": {"max": 0}, "mrr": {"min": 0.4}, "nope.x": {"min": 1}}) == [
        "n_errors: 1 > max 0",
        "nope.x: missing",
    ]
    md = render_markdown(
        {
            "summary": s,
            "target": "local",
            "timestamp": "t",
            "judge": "heuristic",
            "threshold_profile": "offline",
            "failures": ["x"],
            "items": [r.__dict__ for r in results],
        }
    )
    assert "| MRR |" in md and "Threshold failures" in md and isinstance(results[0], ItemResult)


def test_stratified_subset_covers_categories() -> None:
    items = [
        {"id": f"{c}-{i}", "category": c}
        for c in ("document", "sql", "hybrid", "out_of_scope")
        for i in range(5)
    ]
    sub = stratified(items, 10)
    assert len(sub) == 10 and {x["category"] for x in sub} == {"document", "sql", "hybrid", "out_of_scope"}


def test_golden_set_shape() -> None:
    rows = [json.loads(x) for x in Path("eval/golden_set.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) >= 30 and len({r["id"] for r in rows}) == len(rows)
    cats = {r["category"] for r in rows}
    assert cats == {"document", "sql", "hybrid", "out_of_scope"}
    for r in rows:
        assert set(r) >= {
            "id",
            "question",
            "expected_route",
            "relevant_sources",
            "must_contain",
            "reference_answer",
        }
