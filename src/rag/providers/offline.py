"""Offline providers: deterministic hash embeddings + a lexical BM25-style reranker.

These make the whole system runnable and testable without credentials. They are deliberately simple
baselines — production uses Gemini embeddings and the Vertex AI Ranking API.
"""

from __future__ import annotations

import hashlib
import itertools
import math
from collections import Counter

import numpy as np

from rag.models import ScoredChunk
from rag.providers.base import l2_normalize
from rag.text import content_words


def _stem(w: str) -> str:
    for suf in ("ing", "ies", "es", "s", "ed"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def _features(text: str) -> list[str]:
    toks = [_stem(w) for w in content_words(text)]
    grams = [f"{a}_{b}" for a, b in itertools.pairwise(toks)]
    trigrams = [f"#{t[i : i + 3]}" for t in toks if len(t) > 4 for i in range(len(t) - 2)]
    return toks + grams + trigrams


class HashEmbedder:
    """Signed feature-hashing embedder (unigrams, bigrams, char-trigrams) with log-TF, L2-normalised."""

    def __init__(self, dim: int = 768) -> None:
        self.dim = dim

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        for feat, tf in Counter(_features(text)).items():
            h = int.from_bytes(hashlib.blake2b(feat.encode(), digest_size=8).digest(), "little")
            weight = (1.0 + math.log(tf)) * (0.5 if feat.startswith("#") else 1.0)
            v[h % self.dim] += weight if (h >> 63) & 1 else -weight
        return l2_normalize(v)

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return np.stack([self._vec(t) for t in texts])

    def embed_query(self, text: str) -> np.ndarray:
        return self._vec(text)


class LexicalReranker:
    """BM25-flavoured rescoring of candidates against the query (title/section boosted)."""

    name = "lexical"

    def __init__(self, k1: float = 1.2, b: float = 0.75) -> None:
        self.k1, self.b = k1, b

    def rerank(self, query: str, candidates: list[ScoredChunk], top_n: int) -> list[ScoredChunk]:
        if not candidates:
            return []
        q_terms = {_stem(w) for w in content_words(query)}
        docs = [
            [
                _stem(w)
                for w in content_words(f"{c.chunk.title} {c.chunk.section} {c.chunk.section} {c.chunk.text}")
            ]
            for c in candidates
        ]
        n = len(docs)
        avgdl = sum(len(d) for d in docs) / max(n, 1) or 1.0
        df = Counter(t for d in docs for t in set(d))
        out: list[ScoredChunk] = []
        max_retrieval = max((c.retrieval_score for c in candidates), default=0.0) or 1.0
        for c, d in zip(candidates, docs, strict=True):
            tf = Counter(d)
            s = 0.0
            for t in q_terms:
                if tf[t] == 0:
                    continue
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * tf[t] * (self.k1 + 1) / (tf[t] + self.k1 * (1 - self.b + self.b * len(d) / avgdl))
            coverage = sum(1 for t in q_terms if tf[t]) / max(len(q_terms), 1)
            score = s * (0.5 + coverage) + 0.25 * (c.retrieval_score / max_retrieval)
            out.append(c.model_copy(update={"rerank_score": score, "score": score}))
        out.sort(key=lambda x: x.score, reverse=True)
        return out[:top_n]


class PassthroughReranker:
    name = "none"

    def rerank(self, query: str, candidates: list[ScoredChunk], top_n: int) -> list[ScoredChunk]:
        return candidates[:top_n]
