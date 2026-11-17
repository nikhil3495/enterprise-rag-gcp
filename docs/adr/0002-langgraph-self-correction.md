# ADR 0002 — LangGraph state graph with self-correction and a grounding check

**Status:** accepted

## Context
Questions need documents, numbers from BigQuery, or both. Retrieval sometimes returns nothing useful, and
LLMs sometimes state things the sources don't support. The orchestration must be inspectable, testable
offline, and bounded in cost.

## Decision
A `StateGraph` over a `TypedDict` state:

`route → {retrieve | run_sql | retrieve ∥ run_sql} → grade → (generate | rewrite → retrieve) → check_grounding → (END | generate)`

* **route** — fast model with a structured `{route, reason}` output; it sees a one-line-per-table catalog.
* **Parallel fan-out** — for `both`, the conditional edge returns two targets, so `retrieve` and `run_sql`
  run in the same superstep. `grade` is the fan-in.
* **grade** — one batched fast-model call grades every candidate passage. Irrelevant passages are dropped.
  Missing grades default to *relevant*, so evidence is never silently lost.
* **Self-correction** — if nothing is relevant and SQL didn't succeed, `rewrite` reformulates the query
  (≤ 2 times). After that, `generate` runs in abstain mode, with no model call and
  `insufficient_context=true`.
* **generate** — sources are labelled `[S1]…[Sn]` and `[SQL1]`. The system prompt requires citations,
  exact SQL numbers, and abstention when the context is insufficient, and treats source text as untrusted.
  Unknown citation labels are stripped in post-processing.
* **check_grounding** — a fast-model judge returns `{grounded, unsupported_claims}`. One regeneration runs
  with that feedback. The response always carries the final `grounded` flag.
* `trace` uses an `operator.add` reducer: every node appends `{node, ms, …}`, which `debug=true` returns.

## Consequences
* The worst case is bounded: 1 route + 3 retrievals + 3 grades + 2 rewrites + 2 generations + 2 checks.
* Every path (vector / sql / both / rewrite / abstain / regeneration) is unit-tested with injected fakes
  (`tests/test_graph.py`), including a timing assertion that proves the `both` branches run in parallel.
* The diagram in `docs/architecture.md` is exported from the compiled graph, so it can't drift from the code.
