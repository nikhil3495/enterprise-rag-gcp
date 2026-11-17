"""BM25 sparse vectors via feature hashing, with IDF applied on the *query* side.

Score decomposition (see docs/adr/0001):

    BM25(q, d) = sum_t  idf(t)  *  tf(t,d)*(k1+1) / (tf(t,d) + k1*(1 - b + b*|d|/avgdl))
                       \\_query/   \\_______________ document side _____________________/

* Document vectors store only the saturated, length-normalised TF part, using a *fixed* reference
  ``avgdl`` (the target chunk size), so a document's vector never depends on the rest of the corpus.
* Query vectors carry ``idf(t)`` computed from document-frequency stats persisted next to the index
  (``sparse_stats.json``). Stats can be refreshed after every ingestion without re-indexing a vector.
* Terms are hashed into a 2^24 space (collisions are negligible at enterprise-corpus vocabulary sizes).

The dot product the vector store computes is therefore exactly BM25.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from dataclasses import dataclass, field

from rag.models import SparseVector
from rag.providers.offline import _stem
from rag.text import content_words

SPACE_BITS = 24
SPACE = 1 << SPACE_BITS


def term_dim(term: str) -> int:
    return int.from_bytes(hashlib.blake2b(term.encode("utf-8"), digest_size=8).digest(), "little") % SPACE


def analyze(text: str) -> list[str]:
    return [_stem(w) for w in content_words(text)]


@dataclass
class SparseStats:
    n_docs: int = 0
    df: dict[int, int] = field(default_factory=dict)

    def idf(self, dim: int) -> float:
        n = self.df.get(dim, 0)
        return math.log(1.0 + (self.n_docs - n + 0.5) / (n + 0.5))

    def to_json(self) -> str:
        return json.dumps({"n_docs": self.n_docs, "df": {str(k): v for k, v in self.df.items()}})

    @classmethod
    def from_json(cls, raw: str) -> SparseStats:
        data = json.loads(raw)
        return cls(n_docs=int(data["n_docs"]), df={int(k): int(v) for k, v in data["df"].items()})


class BM25SparseEncoder:
    def __init__(self, k1: float = 1.2, b: float = 0.75, ref_doc_len: int = 250) -> None:
        self.k1, self.b, self.ref_doc_len = k1, b, ref_doc_len
        self.stats = SparseStats()

    # ---- document side -------------------------------------------------------------------
    def encode_document(self, text: str) -> SparseVector:
        terms = analyze(text)
        if not terms:
            return SparseVector()
        dl = len(terms)
        norm = self.k1 * (1 - self.b + self.b * dl / self.ref_doc_len)
        weights: dict[int, float] = {}
        for term, tf in Counter(terms).items():
            dim = term_dim(term)
            weights[dim] = weights.get(dim, 0.0) + tf * (self.k1 + 1) / (tf + norm)
        dims = sorted(weights)
        return SparseVector(dimensions=dims, values=[round(weights[d], 6) for d in dims])

    # ---- query side ----------------------------------------------------------------------
    def encode_query(self, text: str) -> SparseVector:
        dims = sorted({term_dim(t) for t in analyze(text)})
        vals = [round(self.stats.idf(d), 6) for d in dims]
        keep = [(d, v) for d, v in zip(dims, vals, strict=True) if v > 0]
        return SparseVector(dimensions=[d for d, _ in keep], values=[v for _, v in keep])

    # ---- corpus statistics ----------------------------------------------------------------
    @staticmethod
    def compute_stats(doc_vectors: list[SparseVector]) -> SparseStats:
        df: Counter[int] = Counter()
        for v in doc_vectors:
            df.update(set(v.dimensions))
        return SparseStats(n_docs=len(doc_vectors), df=dict(df))
