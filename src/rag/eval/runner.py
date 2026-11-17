"""Evaluation harness: run the golden set against a target and gate on thresholds.

Targets
* ``local`` — in-process service built from the current settings (offline profile in CI).
* ``http``  — a deployed Cloud Run URL; requests carry a Google ID token
  (google-auth ``fetch_id_token``; fallback ``gcloud auth print-identity-token``).

Metrics: recall@k (k=1,3,5,10; document level; before and after rerank), MRR, hit-rate, route accuracy,
faithfulness, answer correctness (must_contain with numeric tolerance), abstention accuracy,
latency p50/p95/p99, output tokens/s, cost per request (mean, p95, total), grounded rate.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import yaml

from rag.eval import metrics as M
from rag.eval.judge import GeminiJudge, HeuristicJudge, Judge

KS = (1, 3, 5, 10)


@dataclass
class Observation:
    answer: str
    route: str
    grounded: bool
    insufficient_context: bool
    candidate_sources: list[str]
    reranked_sources: list[str]
    context: str
    usage: dict[str, Any]
    latency_ms: float
    sql: str | None = None
    error: str | None = None


@dataclass
class ItemResult:
    id: str
    category: str
    question: str
    expected_route: str
    route: str
    answer: str
    grounded: bool
    abstained: bool
    latency_ms: float
    cost_usd: float
    output_tokens_per_s: float | None
    recall_pre: dict[str, float | None] = field(default_factory=dict)
    recall_post: dict[str, float | None] = field(default_factory=dict)
    rr_post: float | None = None
    correctness: float | None = None
    faithfulness: float | None = None
    unsupported: list[str] = field(default_factory=list)
    sql: str | None = None
    error: str | None = None


class Target(Protocol):
    name: str

    def ask(self, question: str) -> Observation: ...


# ---- targets -----------------------------------------------------------------------------------------
class LocalTarget:
    name = "local"

    def __init__(self) -> None:
        from rag.config import Settings
        from rag.container import Container

        self.container = Container(Settings())
        self.service = self.container.service

    def ask(self, question: str) -> Observation:
        r = self.service.answer(question)
        return Observation(
            answer=r.answer,
            route=r.route,
            grounded=r.grounded,
            insufficient_context=r.insufficient_context,
            candidate_sources=[c.chunk.source_uri for c in r.candidates],
            reranked_sources=[c.chunk.source_uri for c in r.retrieved],
            context=r.context,
            usage=r.usage,
            latency_ms=r.latency_ms,
            sql=r.sql.sql if r.sql else None,
        )


def fetch_identity_token(audience: str) -> str:
    try:
        import google.auth.transport.requests
        from google.oauth2 import id_token

        return str(id_token.fetch_id_token(google.auth.transport.requests.Request(), audience))
    except Exception:
        gcloud = shutil.which("gcloud") or shutil.which("gcloud.cmd")
        if not gcloud:
            raise
        out = subprocess.run(  # noqa: S603 - fixed argv; binary resolved via PATH
            [gcloud, "auth", "print-identity-token"], capture_output=True, text=True, check=True
        )
        return out.stdout.strip()


class HttpTarget:
    name = "http"

    def __init__(self, url: str, timeout_s: float = 120.0) -> None:
        import httpx

        self.url = url.rstrip("/")
        self.token = fetch_identity_token(self.url)
        self.token_at = time.time()
        self.client = httpx.Client(timeout=timeout_s)

    def ask(self, question: str) -> Observation:
        if time.time() - self.token_at > 45 * 60:
            self.token, self.token_at = fetch_identity_token(self.url), time.time()
        t0 = time.perf_counter()
        resp = self.client.post(
            f"{self.url}/v1/query",
            json={"question": question, "debug": True},
            headers={"Authorization": f"Bearer {self.token}"},
        )
        wall_ms = (time.perf_counter() - t0) * 1000
        if resp.status_code != 200:
            return Observation(
                "",
                "error",
                False,
                False,
                [],
                [],
                "",
                {},
                wall_ms,
                error=f"HTTP {resp.status_code}: {resp.text[:200]}",
            )
        d = resp.json()
        return Observation(
            answer=d["answer"],
            route=d["route"],
            grounded=d["grounded"],
            insufficient_context=d["insufficient_context"],
            candidate_sources=[p["source_uri"] for p in d.get("candidates") or []],
            reranked_sources=[p["source_uri"] for p in d.get("retrieved") or []],
            context=d.get("context") or "",
            usage=d["usage"],
            latency_ms=wall_ms,  # end-to-end as seen by the client (includes network + auth)
            sql=(d.get("sql") or {}).get("query"),
        )


# ---- harness -----------------------------------------------------------------------------------------
def load_golden(path: str | Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def stratified(items: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    by_cat: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for it in items:
        by_cat[it["category"]].append(it)
    picked: list[dict[str, Any]] = []
    i = 0
    while len(picked) < min(n, len(items)):
        for cat in sorted(by_cat):
            if i < len(by_cat[cat]) and len(picked) < n:
                picked.append(by_cat[cat][i])
        i += 1
    return sorted(picked, key=lambda x: x["id"])


def evaluate_item(item: dict[str, Any], obs: Observation, judge: Judge) -> ItemResult:
    rel = item.get("relevant_sources", [])
    oos = item["category"] == "out_of_scope"
    res = ItemResult(
        id=item["id"],
        category=item["category"],
        question=item["question"],
        expected_route=item["expected_route"],
        route=obs.route,
        answer=obs.answer,
        grounded=obs.grounded,
        abstained=obs.insufficient_context,
        latency_ms=obs.latency_ms,
        cost_usd=float(obs.usage.get("cost_usd") or 0.0),
        output_tokens_per_s=obs.usage.get("output_tokens_per_s"),
        sql=obs.sql,
        error=obs.error,
    )
    if obs.error:
        return res
    if rel:
        res.recall_pre = {f"@{k}": M.recall_at_k(obs.candidate_sources, rel, k) for k in KS}
        res.recall_post = {f"@{k}": M.recall_at_k(obs.reranked_sources, rel, k) for k in KS}
        res.rr_post = M.reciprocal_rank(obs.reranked_sources, rel)
    if not oos:
        res.correctness = M.answer_correctness(obs.answer, item.get("must_contain", []))
    f = judge.faithfulness(item["question"], obs.answer, obs.context, obs.insufficient_context)
    res.faithfulness, res.unsupported = f.score, f.unsupported
    return res


def _r(x: float | None, n: int = 4) -> float | None:
    return None if x is None else round(x, n)


def aggregate(results: list[ItemResult]) -> dict[str, Any]:
    ok = [r for r in results if not r.error]
    with_rel = [r for r in ok if r.recall_post]
    in_scope = [r for r in ok if r.category != "out_of_scope"]
    oos = [r for r in ok if r.category == "out_of_scope"]
    lat = [r.latency_ms for r in ok]
    costs = [r.cost_usd for r in ok]
    tps = [r.output_tokens_per_s for r in ok if r.output_tokens_per_s]
    summary: dict[str, Any] = {
        "n_items": len(results),
        "n_errors": len(results) - len(ok),
        "recall_pre_rerank": {f"@{k}": _r(M.mean([r.recall_pre[f"@{k}"] for r in with_rel])) for k in KS},
        "recall": {f"@{k}": _r(M.mean([r.recall_post[f"@{k}"] for r in with_rel])) for k in KS},
        "hit_rate@5": _r(M.mean([float((r.recall_post["@5"] or 0) > 0) for r in with_rel])),
        "mrr": _r(M.mean([r.rr_post for r in with_rel])),
        "route_accuracy": _r(M.mean([float(r.route == r.expected_route) for r in ok])),
        "answer_correctness": _r(M.mean([r.correctness for r in in_scope])),
        "faithfulness": _r(M.mean([r.faithfulness for r in ok])),
        "abstention_accuracy": _r(M.mean([float(r.abstained) for r in oos])),
        "false_abstention_rate": _r(M.mean([float(r.abstained) for r in in_scope])),
        "grounded_rate": _r(M.mean([float(r.grounded) for r in ok])),
        "latency_ms": {
            "p50": _r(M.percentile(lat, 50), 1),
            "p95": _r(M.percentile(lat, 95), 1),
            "p99": _r(M.percentile(lat, 99), 1),
            "mean": _r(M.mean(lat), 1),
        },
        "output_tokens_per_s": {"mean": _r(M.mean(tps), 1), "p50": _r(M.percentile(tps, 50), 1)},
        "cost_per_request_usd": {
            "mean": _r(M.mean(costs), 6),
            "p95": _r(M.percentile(costs, 95), 6),
            "total": _r(sum(costs), 6),
        },
    }
    by_cat: dict[str, Any] = {}
    for cat in sorted({r.category for r in ok}):
        rs = [r for r in ok if r.category == cat]
        by_cat[cat] = {
            "n": len(rs),
            "correctness": _r(M.mean([r.correctness for r in rs])),
            "route_accuracy": _r(M.mean([float(r.route == r.expected_route) for r in rs])),
            "faithfulness": _r(M.mean([r.faithfulness for r in rs])),
            "latency_p95_ms": _r(M.percentile([r.latency_ms for r in rs], 95), 1),
        }
    summary["by_category"] = by_cat
    return summary


def _lookup(summary: dict[str, Any], dotted: str) -> Any:
    cur: Any = summary
    for part in dotted.split("."):
        cur = cur.get(part) if isinstance(cur, dict) else None
    return cur


def check_thresholds(summary: dict[str, Any], thresholds: dict[str, Any]) -> list[str]:
    failures = []
    for key, rule in thresholds.items():
        value = _lookup(summary, key)
        if value is None:
            failures.append(f"{key}: missing")
            continue
        if "min" in rule and value < rule["min"]:
            failures.append(f"{key}: {value} < min {rule['min']}")
        if "max" in rule and value > rule["max"]:
            failures.append(f"{key}: {value} > max {rule['max']}")
    return failures


def render_markdown(report: dict[str, Any]) -> str:
    s = report["summary"]
    lines = [
        f"# Evaluation report — {report['target']} ({report['timestamp']})",
        "",
        f"- Target: `{report['target']}`{' — ' + report['url'] if report.get('url') else ''}",
        f"- Config: `{json.dumps(report.get('config', {}))}`",
        f"- Judge: `{report['judge']}` · Items: {s['n_items']} · Errors: {s['n_errors']}",
        f"- Threshold profile: `{report['threshold_profile']}` → **{'PASS' if not report['failures'] else 'FAIL'}**",
        "",
        "| Metric | Value |",
        "| --- | --- |",
    ]
    rows = [
        ("Recall@1 / @3 / @5 / @10 (after rerank)", " / ".join(str(s["recall"][f"@{k}"]) for k in KS)),
        (
            "Recall@1 / @3 / @5 / @10 (before rerank)",
            " / ".join(str(s["recall_pre_rerank"][f"@{k}"]) for k in KS),
        ),
        ("MRR", s["mrr"]),
        ("Hit rate@5", s["hit_rate@5"]),
        ("Route accuracy", s["route_accuracy"]),
        ("Answer correctness", s["answer_correctness"]),
        ("Faithfulness", s["faithfulness"]),
        ("Abstention accuracy (out-of-scope)", s["abstention_accuracy"]),
        ("False abstention rate (in-scope)", s["false_abstention_rate"]),
        ("Grounded rate", s["grounded_rate"]),
        (
            "Latency p50 / p95 / p99 (ms)",
            f"{s['latency_ms']['p50']} / {s['latency_ms']['p95']} / {s['latency_ms']['p99']}",
        ),
        ("Output tokens/s (mean)", s["output_tokens_per_s"]["mean"]),
        (
            "Cost per request USD (mean / p95)",
            f"{s['cost_per_request_usd']['mean']} / {s['cost_per_request_usd']['p95']}",
        ),
        ("Total cost USD", s["cost_per_request_usd"]["total"]),
    ]
    lines += [f"| {k} | {v} |" for k, v in rows]
    lines += [
        "",
        "## By category",
        "",
        "| Category | n | Correctness | Route acc. | Faithfulness | p95 ms |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for cat, v in s["by_category"].items():
        lines.append(
            f"| {cat} | {v['n']} | {v['correctness']} | {v['route_accuracy']} | {v['faithfulness']} | "
            f"{v['latency_p95_ms']} |"
        )
    if report["failures"]:
        lines += ["", "## Threshold failures", ""] + [f"- {f}" for f in report["failures"]]
    lines += [
        "",
        "## Items",
        "",
        "| id | route (exp) | correct | faithful | abstained | ms | answer |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for it in report["items"]:
        ans = (it["error"] or it["answer"]).replace("|", "\\|").replace("\n", " ")[:160]
        lines.append(
            f"| {it['id']} | {it['route']} ({it['expected_route']}) | {it['correctness']} | "
            f"{_r(it['faithfulness'], 2)} | {it['abstained']} | {it['latency_ms']:.0f} | {ans} |"
        )
    return "\n".join(lines) + "\n"


def run(
    *,
    target: str,
    url: str | None,
    golden_path: str,
    thresholds_path: str,
    profile: str | None,
    limit: int | None,
    out_dir: str,
    judge_mode: str = "auto",
) -> int:
    items = load_golden(golden_path)
    if limit:
        items = stratified(items, limit)
    tgt: Target
    if target == "http":
        if not url:
            print("--url is required for --target http", file=sys.stderr)
            return 2
        tgt = HttpTarget(url)
    else:
        tgt = LocalTarget()

    from rag.config import Settings

    settings = Settings()
    use_gemini = judge_mode == "gemini" or (
        judge_mode == "auto" and (target == "http" or settings.profile == "prod")
    )
    judge: Judge
    if use_gemini:
        from rag.providers.gemini import GeminiLLM, make_client

        if not settings.gcp_project:
            print("RAG_GCP_PROJECT is required for the Gemini judge", file=sys.stderr)
            return 2
        judge = GeminiJudge(
            GeminiLLM(
                client=make_client(settings.gcp_project, settings.genai_location),
                generation_model=settings.generation_model,
                fast_model=settings.fast_model,
                thinking_level=settings.thinking_level,
            )
        )
    else:
        judge = HeuristicJudge()

    if target == "local":  # warm-up so lazy initialisation isn't counted as request latency
        tgt.ask("warm-up question")

    results = []
    for it in items:
        try:
            obs = tgt.ask(it["question"])
        except Exception as e:
            obs = Observation(
                "", "error", False, False, [], [], "", {}, 0.0, error=f"{type(e).__name__}: {e}"
            )
        results.append(evaluate_item(it, obs, judge))
        print(
            f"  {it['id']:8s} route={results[-1].route:6s} correct={results[-1].correctness} "
            f"ms={results[-1].latency_ms:.0f}",
            file=sys.stderr,
        )

    summary = aggregate(results)
    profile = profile or ("live" if target == "http" else "offline")
    thresholds = (yaml.safe_load(Path(thresholds_path).read_text(encoding="utf-8")) or {}).get(profile, {})
    failures = check_thresholds(summary, thresholds)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report = {
        "timestamp": ts,
        "target": target,
        "url": url,
        "judge": judge.name,
        "threshold_profile": profile,
        "config": {
            k: getattr(settings, k)
            for k in (
                "profile",
                "candidate_k",
                "top_k",
                "rrf_alpha",
                "chunk_tokens",
                "chunk_overlap",
                "generation_model",
                "fast_model",
            )
        }
        if target == "local"
        else {"note": "server-side config; see /v1/info"},
        "summary": summary,
        "failures": failures,
        "items": [asdict(r) for r in results],
    }
    if target == "http":
        try:
            import httpx

            info = httpx.get(
                f"{url.rstrip('/')}/v1/info" if url else "",
                headers={"Authorization": f"Bearer {fetch_identity_token(url or '')}"},
                timeout=30,
            )
            report["config"] = info.json()
        except Exception:  # noqa: S110 - informational only
            pass
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{ts}-{target}"
    (out / f"{stem}.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    (out / f"{stem}.md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "report": str(out / f"{stem}.md"),
                "summary": {k: v for k, v in summary.items() if k != "by_category"},
                "failures": failures,
            },
            indent=2,
        )
    )
    return 1 if failures else 0
