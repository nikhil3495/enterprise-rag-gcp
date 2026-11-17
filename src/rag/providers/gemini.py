"""Gemini on Vertex AI (google-genai SDK): structured-output LLM + embeddings.

* Structured output: ``response_mime_type="application/json"`` + ``response_schema=<pydantic model>``.
* ``thinking_level`` is sent when configured; if a model rejects it (HTTP 400 mentioning thinking) we retry
  once without it and remember that for the model.
* Retries with exponential backoff + jitter on 429/5xx.
* Embeddings: ``gemini-embedding-001`` with ``output_dimensionality`` and retrieval task types; vectors are
  L2-normalised (reduced-dimension outputs are not normalised by the service). If the endpoint rejects
  multi-input requests (HTTP 400) we fall back to parallel single-input calls.
* Every call reports token usage to the request's :class:`~rag.telemetry.UsageTracker`.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypeVar

import numpy as np
from pydantic import BaseModel

from rag.observability import Timer
from rag.providers.base import l2_normalize
from rag.telemetry import current_tracker
from rag.text import count_tokens

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)
R = TypeVar("R")

RETRYABLE = {408, 429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    """Upstream model failure after retries (mapped to HTTP 503 by the API)."""


def _status(e: Exception) -> int | None:
    code = getattr(e, "code", None)
    return int(code) if isinstance(code, int) else None


def with_retries[R](fn: Callable[[], R], *, max_retries: int, what: str, base_delay: float = 0.5) -> R:
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as e:
            status = _status(e)
            if status not in RETRYABLE or attempt >= max_retries:
                raise
            delay = min(8.0, base_delay * 2**attempt) * (0.5 + random.random())
            log.warning("retrying upstream call", extra={"what": what, "status": status, "delay_s": delay})
            time.sleep(delay)
            attempt += 1


def make_client(project: str, location: str) -> Any:
    from google import genai

    return genai.Client(vertexai=True, project=project, location=location)


class GeminiLLM:
    def __init__(
        self,
        *,
        client: Any,
        generation_model: str,
        fast_model: str,
        thinking_level: str | None = "LOW",
        max_retries: int = 3,
        timeout_s: float = 60.0,
    ) -> None:
        self.client = client
        self.models = {"generation": generation_model, "fast": fast_model}
        self.thinking_level = thinking_level or None
        self.max_retries = max_retries
        self.timeout_s = timeout_s
        self._no_thinking: set[str] = set()
        self._lock = threading.Lock()

    def _config(
        self, model: str, system: str, schema: type[BaseModel], temperature: float, thinking: bool
    ) -> Any:
        from google.genai import types

        kwargs: dict[str, Any] = {
            "system_instruction": system,
            "response_mime_type": "application/json",
            "response_schema": schema,
            "temperature": temperature,
            "http_options": types.HttpOptions(timeout=int(self.timeout_s * 1000)),
        }
        if thinking:
            kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_level=types.ThinkingLevel(str(self.thinking_level).upper())
            )
        return types.GenerateContentConfig(**kwargs)

    def generate_structured(
        self, *, role: str, name: str, system: str, prompt: str, schema: type[T], temperature: float = 0.0
    ) -> T:
        model = self.models["generation" if role == "generation" else "fast"]
        use_thinking = bool(self.thinking_level) and model not in self._no_thinking
        timer = Timer()

        def call(thinking: bool) -> Any:
            cfg = self._config(model, system, schema, temperature, thinking)
            return with_retries(
                lambda: self.client.models.generate_content(model=model, contents=prompt, config=cfg),
                max_retries=self.max_retries,
                what=f"generate:{name}",
            )

        try:
            try:
                resp = call(use_thinking)
            except Exception as e:
                if use_thinking and _status(e) == 400 and "think" in str(e).lower():
                    log.warning("model rejected thinking_level; disabling", extra={"model": model})
                    with self._lock:
                        self._no_thinking.add(model)
                    resp = call(False)
                else:
                    raise
        except Exception as e:
            raise LLMError(f"{name}: {type(e).__name__}: {str(e)[:300]}") from e

        latency = timer.ms
        parsed = self.parse(resp, schema)
        um = getattr(resp, "usage_metadata", None)
        tracker = current_tracker()
        if tracker is not None:
            tracker.add_llm(
                name=name,
                role="generation" if role == "generation" else "fast",
                model=model,
                input_tokens=int(getattr(um, "prompt_token_count", 0) or 0),
                output_tokens=int(getattr(um, "candidates_token_count", 0) or 0),
                thinking_tokens=int(getattr(um, "thoughts_token_count", 0) or 0),
                latency_ms=latency,
            )
        return parsed

    @staticmethod
    def parse(resp: Any, schema: type[T]) -> T:
        parsed = getattr(resp, "parsed", None)
        if isinstance(parsed, schema):
            return parsed
        if isinstance(parsed, dict):
            return schema.model_validate(parsed)
        text = getattr(resp, "text", None) or ""
        text = text.strip()
        if text.startswith("```"):
            text = text.strip("`")
            text = text[text.find("{") :]
        try:
            return schema.model_validate_json(text)
        except Exception as e:
            raise LLMError(f"could not parse structured output: {text[:200]!r}") from e


class GeminiEmbedder:
    def __init__(
        self,
        *,
        client: Any,
        model: str = "gemini-embedding-001",
        dim: int = 768,
        max_retries: int = 3,
        batch_size: int = 32,
        parallelism: int = 8,
    ) -> None:
        self.client = client
        self.model = model
        self.dim = dim
        self.max_retries = max_retries
        self.batch_size = batch_size
        self.parallelism = parallelism
        self.single_input_only = False

    def _call(self, contents: list[str], task_type: str) -> tuple[np.ndarray, int]:
        from google.genai import types

        cfg = types.EmbedContentConfig(task_type=task_type, output_dimensionality=self.dim)
        resp = with_retries(
            lambda: self.client.models.embed_content(model=self.model, contents=contents, config=cfg),
            max_retries=self.max_retries,
            what="embed",
        )
        vecs = np.asarray([e.values for e in resp.embeddings], dtype=np.float32)
        tokens = 0
        for e, text in zip(resp.embeddings, contents, strict=True):
            stats = getattr(e, "statistics", None)
            tc = getattr(stats, "token_count", None) if stats is not None else None
            tokens += int(tc) if tc else count_tokens(text)
        return vecs, tokens

    def _embed(self, texts: list[str], task_type: str) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        timer = Timer()
        out: list[np.ndarray] = []
        tokens = 0
        try:
            if not self.single_input_only and len(texts) > 1:
                for i in range(0, len(texts), self.batch_size):
                    try:
                        v, t = self._call(texts[i : i + self.batch_size], task_type)
                    except Exception as e:
                        if _status(e) == 400:
                            log.warning("embedding batch rejected; switching to single-input mode")
                            self.single_input_only = True
                            break
                        raise
                    out.append(v)
                    tokens += t
            if self.single_input_only or len(texts) == 1:
                done = sum(len(v) for v in out)
                with ThreadPoolExecutor(max_workers=self.parallelism) as pool:
                    results = list(pool.map(lambda s: self._call([s], task_type), texts[done:]))
                out.extend(v for v, _ in results)
                tokens += sum(t for _, t in results)
        except Exception as e:
            raise LLMError(f"embedding failed: {type(e).__name__}: {str(e)[:300]}") from e
        tracker = current_tracker()
        if tracker is not None:
            tracker.add_embedding(model=self.model, input_tokens=tokens, latency_ms=timer.ms)
        return l2_normalize(np.vstack(out))

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        return self._embed(texts, "RETRIEVAL_DOCUMENT")

    def embed_query(self, text: str) -> np.ndarray:
        return self._embed([text], "RETRIEVAL_QUERY")[0]
