"""Per-request usage + cost accounting.

A :class:`UsageTracker` lives in a ``ContextVar`` for the duration of a request. LangGraph runs
parallel branches in worker threads with a *copied* context, so every branch sees the same tracker
object; a lock makes concurrent ``add`` calls safe and all events land in one total.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Literal

from rag.config import Settings

EventKind = Literal["llm", "embedding", "rerank", "bigquery"]
LlmRole = Literal["generation", "fast"]


@dataclass(frozen=True)
class UsageEvent:
    kind: EventKind
    name: str
    role: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    latency_ms: float = 0.0
    bytes_billed: int = 0
    queries: int = 0
    cost_usd: float = 0.0


@dataclass
class UsageSummary:
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    cost_usd: float = 0.0
    output_tokens_per_s: float | None = None
    llm_calls: int = 0
    embedding_calls: int = 0
    rerank_calls: int = 0
    bigquery_bytes_billed: int = 0

    def as_dict(self) -> dict[str, float | int | None]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "thinking_tokens": self.thinking_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "output_tokens_per_s": (
                round(self.output_tokens_per_s, 1) if self.output_tokens_per_s is not None else None
            ),
            "llm_calls": self.llm_calls,
            "embedding_calls": self.embedding_calls,
            "rerank_calls": self.rerank_calls,
            "bigquery_bytes_billed": self.bigquery_bytes_billed,
        }


TIB = 1024**4


class PriceTable:
    def __init__(self, settings: Settings) -> None:
        self.s = settings

    def llm(self, role: str, input_tokens: int, output_tokens: int, thinking_tokens: int) -> float:
        if role == "generation":
            pin, pout = self.s.price_generation_input_per_m, self.s.price_generation_output_per_m
        else:
            pin, pout = self.s.price_fast_input_per_m, self.s.price_fast_output_per_m
        # Thinking tokens are billed at the output rate.
        return (input_tokens * pin + (output_tokens + thinking_tokens) * pout) / 1_000_000

    def embedding(self, input_tokens: int) -> float:
        return input_tokens * self.s.price_embedding_input_per_m / 1_000_000

    def rerank(self, queries: int) -> float:
        return queries * self.s.price_rerank_per_1k_queries / 1000

    def bigquery(self, bytes_billed: int) -> float:
        return bytes_billed / TIB * self.s.price_bigquery_per_tib


@dataclass
class UsageTracker:
    prices: PriceTable
    events: list[UsageEvent] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def _add(self, event: UsageEvent) -> UsageEvent:
        with self._lock:
            self.events.append(event)
        return event

    def add_llm(
        self,
        *,
        name: str,
        role: LlmRole,
        model: str,
        input_tokens: int,
        output_tokens: int,
        thinking_tokens: int = 0,
        latency_ms: float = 0.0,
    ) -> UsageEvent:
        cost = self.prices.llm(role, input_tokens, output_tokens, thinking_tokens)
        return self._add(
            UsageEvent(
                "llm",
                name,
                role,
                model,
                input_tokens,
                output_tokens,
                thinking_tokens,
                latency_ms,
                cost_usd=cost,
            )
        )

    def add_embedding(self, *, model: str, input_tokens: int, latency_ms: float = 0.0) -> UsageEvent:
        return self._add(
            UsageEvent(
                "embedding",
                "embed",
                model=model,
                input_tokens=input_tokens,
                latency_ms=latency_ms,
                cost_usd=self.prices.embedding(input_tokens),
            )
        )

    def add_rerank(self, *, model: str, queries: int = 1, latency_ms: float = 0.0) -> UsageEvent:
        return self._add(
            UsageEvent(
                "rerank",
                "rerank",
                model=model,
                queries=queries,
                latency_ms=latency_ms,
                cost_usd=self.prices.rerank(queries),
            )
        )

    def add_bigquery(self, *, bytes_billed: int, latency_ms: float = 0.0) -> UsageEvent:
        return self._add(
            UsageEvent(
                "bigquery",
                "query",
                bytes_billed=bytes_billed,
                latency_ms=latency_ms,
                cost_usd=self.prices.bigquery(bytes_billed),
            )
        )

    def summary(self) -> UsageSummary:
        with self._lock:
            events = list(self.events)
        s = UsageSummary()
        gen_out, gen_ms = 0, 0.0
        for e in events:
            s.cost_usd += e.cost_usd
            if e.kind == "llm":
                s.llm_calls += 1
                s.input_tokens += e.input_tokens
                s.output_tokens += e.output_tokens
                s.thinking_tokens += e.thinking_tokens
                if e.role == "generation":
                    gen_out += e.output_tokens
                    gen_ms += e.latency_ms
            elif e.kind == "embedding":
                s.embedding_calls += 1
                s.input_tokens += e.input_tokens
            elif e.kind == "rerank":
                s.rerank_calls += 1
            elif e.kind == "bigquery":
                s.bigquery_bytes_billed += e.bytes_billed
        if gen_out and gen_ms > 0:
            s.output_tokens_per_s = gen_out / (gen_ms / 1000.0)
        return s


_current: ContextVar[UsageTracker | None] = ContextVar("rag_usage_tracker", default=None)


def current_tracker() -> UsageTracker | None:
    return _current.get()


@contextmanager
def track_usage(settings: Settings) -> Iterator[UsageTracker]:
    tracker = UsageTracker(PriceTable(settings))
    token = _current.set(tracker)
    try:
        yield tracker
    finally:
        _current.reset(token)
