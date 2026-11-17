"""Evaluation metrics (pure functions, unit-tested)."""

from __future__ import annotations

import math
import re
from collections.abc import Sequence

_NUM_ITEM = re.compile(r"^-?[\d,]*\.?\d+$")
_NUM_IN_TEXT = re.compile(
    r"(?<![\w.])(-?\$?\d[\d,]*(?:\.\d+)?)\s*(million|mn|m|billion|bn|b|thousand|k)?\b", re.I
)
_SCALE = {"million": 1e6, "mn": 1e6, "m": 1e6, "billion": 1e9, "bn": 1e9, "b": 1e9, "thousand": 1e3, "k": 1e3}


def source_name(uri: str) -> str:
    return uri.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def unique_sources(uris: Sequence[str]) -> list[str]:
    """Ordered, de-duplicated document names (document-level ranking from chunk-level results)."""
    return list(dict.fromkeys(source_name(u) for u in uris))


def recall_at_k(ranked: Sequence[str], relevant: Sequence[str], k: int) -> float | None:
    rel = {source_name(r) for r in relevant}
    if not rel:
        return None
    top = set(unique_sources(ranked)[:k])
    return len(rel & top) / len(rel)


def hit_at_k(ranked: Sequence[str], relevant: Sequence[str], k: int) -> float | None:
    r = recall_at_k(ranked, relevant, k)
    return None if r is None else float(r > 0)


def reciprocal_rank(ranked: Sequence[str], relevant: Sequence[str]) -> float | None:
    rel = {source_name(r) for r in relevant}
    if not rel:
        return None
    for i, name in enumerate(unique_sources(ranked), start=1):
        if name in rel:
            return 1.0 / i
    return 0.0


def percentile(values: Sequence[float], p: float) -> float | None:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    if len(vals) == 1:
        return float(vals[0])
    pos = (len(vals) - 1) * p / 100.0
    lo, hi = math.floor(pos), math.ceil(pos)
    return float(vals[lo] + (vals[hi] - vals[lo]) * (pos - lo))


def mean(values: Sequence[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def numbers_in(text: str) -> list[float]:
    out = []
    for m in _NUM_IN_TEXT.finditer(text):
        raw = m.group(1).replace("$", "").replace(",", "")
        try:
            v = float(raw)
        except ValueError:
            continue
        if m.group(2):
            v *= _SCALE[m.group(2).lower()]
        out.append(v)
    return out


def contains_item(answer: str, item: str, rel_tol: float = 0.005) -> bool:
    """Numeric items match any number in the answer within ``rel_tol`` (handles '$136.79 million');
    text items match case-insensitively on word boundaries (short all-caps codes like 'NA' case-sensitively)."""
    item = item.strip()
    if _NUM_ITEM.match(item):
        target = float(item.replace(",", ""))
        decimals = len(item.split(".")[1]) if "." in item else 0
        abs_tol = 0.5 * 10 ** (-decimals) if decimals else 0.0
        tolerant = bool(decimals) or abs(target) >= 1000  # small integers (counts) must match exactly
        for v in numbers_in(answer):
            if tolerant and abs(v - target) <= max(abs_tol, rel_tol * abs(target)):
                return True
            if not tolerant and v == target:
                return True
        return False
    flags = 0 if (len(item) <= 4 and item.isupper()) else re.I
    return re.search(rf"(?<!\w){re.escape(item)}(?!\w)", answer, flags) is not None


def answer_correctness(answer: str, must_contain: Sequence[str]) -> float | None:
    if not must_contain:
        return None
    return sum(contains_item(answer, m) for m in must_contain) / len(must_contain)
