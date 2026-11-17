"""Faithfulness judges.

faithfulness = supported claims / total claims, judged against the exact context the generator saw.
* :class:`GeminiJudge` — LLM-as-judge (fast model, structured output) used for live evaluation.
* :class:`HeuristicJudge` — offline proxy: a sentence is supported if >= 70% of its content words (and all
  of its numbers) appear in the context. Clearly labelled as a proxy in reports.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, Field

from rag.eval.metrics import numbers_in
from rag.graph import prompts
from rag.providers.base import StructuredLLM
from rag.text import content_words, split_sentences

_CITE = re.compile(r"\[(?:S|SQL)\d+\]")


@dataclass
class FaithfulnessResult:
    score: float
    supported: int
    total: int
    unsupported: list[str]


class Judge(Protocol):
    name: str

    def faithfulness(
        self, question: str, answer: str, context: str, abstained: bool
    ) -> FaithfulnessResult: ...


class _Claim(BaseModel):
    claim: str
    supported: bool


class _Claims(BaseModel):
    claims: list[_Claim] = Field(default_factory=list)


class GeminiJudge:
    name = "gemini"

    def __init__(self, llm: StructuredLLM) -> None:
        self.llm = llm

    def faithfulness(self, question: str, answer: str, context: str, abstained: bool) -> FaithfulnessResult:
        if abstained:
            return FaithfulnessResult(1.0, 1, 1, [])
        res = self.llm.generate_structured(
            role="fast",
            name="judge_faithfulness",
            system=prompts.FAITHFULNESS_SYSTEM,
            prompt=f"<sources>\n{context}\n</sources>\n\nQuestion: {question}\n\nAnswer:\n{answer}",
            schema=_Claims,
        )
        total = len(res.claims)
        if total == 0:
            return FaithfulnessResult(1.0, 0, 0, [])
        supported = sum(c.supported for c in res.claims)
        return FaithfulnessResult(
            supported / total, supported, total, [c.claim for c in res.claims if not c.supported]
        )


class HeuristicJudge:
    name = "heuristic"

    def __init__(self, coverage: float = 0.7) -> None:
        self.coverage = coverage

    def faithfulness(self, question: str, answer: str, context: str, abstained: bool) -> FaithfulnessResult:
        if abstained:
            return FaithfulnessResult(1.0, 1, 1, [])
        ctx_words = set(content_words(context))
        ctx_numbers = {round(n, 2) for n in numbers_in(context.replace(",", ""))}
        sentences = [_CITE.sub("", s).strip() for s in split_sentences(answer)]
        sentences = [s for s in sentences if content_words(s)]
        if not sentences:
            return FaithfulnessResult(1.0, 0, 0, [])
        unsupported = []
        for s in sentences:
            words = content_words(s)
            cov = sum(w in ctx_words for w in words) / len(words)
            nums_ok = all(round(n, 2) in ctx_numbers for n in numbers_in(s.replace(",", "")))
            if cov < self.coverage or not nums_ok:
                unsupported.append(s)
        supported = len(sentences) - len(unsupported)
        return FaithfulnessResult(supported / len(sentences), supported, len(sentences), unsupported)
