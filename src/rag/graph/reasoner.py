"""Reasoners: the LLM-shaped decisions the graph needs.

* :class:`GeminiReasoner` — production. Fast model (Flash-Lite) for routing, grading, rewriting, SQL and
  grounding checks; generation model (Flash) for answers. All calls use structured JSON output.
* :class:`HeuristicReasoner` — deterministic offline baseline (keyword router, lexical grader, pattern
  SQL drafter, extractive answerer). Lets the full graph run in tests/CI with no credentials.
"""

from __future__ import annotations

import re
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from rag.graph import prompts
from rag.models import Route, ScoredChunk
from rag.providers.base import StructuredLLM
from rag.providers.offline import _stem
from rag.sql.catalog import Catalog
from rag.sql.text_to_sql import SqlDraft
from rag.text import content_words, split_sentences


# ---- structured outputs ---------------------------------------------------------------------------
class RouteDecision(BaseModel):
    route: Literal["vector", "sql", "both"]
    reason: str = ""


class ChunkGrade(BaseModel):
    id: str
    relevant: bool


class GradeResult(BaseModel):
    grades: list[ChunkGrade] = Field(default_factory=list)


class RewriteResult(BaseModel):
    query: str


class GeneratedAnswer(BaseModel):
    answer: str
    insufficient_context: bool = False


class GroundingVerdict(BaseModel):
    grounded: bool
    unsupported_claims: list[str] = Field(default_factory=list)


class Reasoner(Protocol):
    name: str

    def route(self, question: str, catalog_summary: str) -> RouteDecision: ...

    def grade(self, question: str, chunks: list[ScoredChunk]) -> list[bool]: ...

    def rewrite(self, question: str, previous_query: str, attempt: int) -> str: ...

    def draft_sql(self, question: str, schema: str, feedback: str | None = None) -> SqlDraft: ...

    def generate(self, question: str, sources: str, feedback: str | None = None) -> GeneratedAnswer: ...

    def check_grounding(self, question: str, answer: str, sources: str) -> GroundingVerdict: ...


# ===================================================================================================
class GeminiReasoner:
    name = "gemini"

    def __init__(self, llm: StructuredLLM) -> None:
        self.llm = llm

    def route(self, question: str, catalog_summary: str) -> RouteDecision:
        return self.llm.generate_structured(
            role="fast",
            name="route",
            system=prompts.ROUTER_SYSTEM.format(catalog=catalog_summary),
            prompt=f"Question: {question}",
            schema=RouteDecision,
        )

    def grade(self, question: str, chunks: list[ScoredChunk]) -> list[bool]:
        if not chunks:
            return []
        passages = "\n\n".join(
            f'<passage id="{i}">\n{c.chunk.title} > {c.chunk.section}\n{c.chunk.text}\n</passage>'
            for i, c in enumerate(chunks)
        )
        res = self.llm.generate_structured(
            role="fast",
            name="grade",
            system=prompts.GRADER_SYSTEM,
            prompt=f"Question: {question}\n\n{passages}",
            schema=GradeResult,
        )
        verdict = {g.id.strip(): g.relevant for g in res.grades}
        # Missing grades default to relevant: never silently drop evidence because the grader skipped it.
        return [verdict.get(str(i), True) for i in range(len(chunks))]

    def rewrite(self, question: str, previous_query: str, attempt: int) -> str:
        res = self.llm.generate_structured(
            role="fast",
            name="rewrite",
            system=prompts.REWRITE_SYSTEM,
            prompt=f"Question: {question}\nPrevious query: {previous_query}\nAttempt: {attempt}",
            schema=RewriteResult,
        )
        return res.query.strip() or question

    def draft_sql(self, question: str, schema: str, feedback: str | None = None) -> SqlDraft:
        prompt = f"Question: {question}"
        if feedback:
            prompt += f"\n\nYour previous attempt failed. Fix it.\n{feedback}"
        return self.llm.generate_structured(
            role="fast",
            name="text_to_sql",
            system=prompts.SQL_SYSTEM.format(schema=schema),
            prompt=prompt,
            schema=SqlDraft,
        )

    def generate(self, question: str, sources: str, feedback: str | None = None) -> GeneratedAnswer:
        prompt = f"<sources>\n{sources}\n</sources>\n\nQuestion: {question}"
        if feedback:
            prompt += (
                "\n\nA reviewer found claims in your previous answer that the sources do not support:\n"
                f"{feedback}\nRewrite the answer using only supported facts."
            )
        return self.llm.generate_structured(
            role="generation",
            name="generate",
            system=prompts.GENERATE_SYSTEM,
            prompt=prompt,
            schema=GeneratedAnswer,
            temperature=0.1,
        )

    def check_grounding(self, question: str, answer: str, sources: str) -> GroundingVerdict:
        return self.llm.generate_structured(
            role="fast",
            name="check_grounding",
            system=prompts.GROUNDING_SYSTEM,
            prompt=f"<sources>\n{sources}\n</sources>\n\nQuestion: {question}\n\nAnswer:\n{answer}",
            schema=GroundingVerdict,
        )


# ===================================================================================================
_DATA_NOUNS = ("revenue", "sales", "sold", "units", "orders", "ticket", "headcount")
_AGG = (
    "how many",
    "total",
    "average",
    "avg",
    "sum",
    "count",
    "number of",
    "highest",
    "lowest",
    "most",
    "least",
    "top",
    "trend",
    "per quarter",
    "by region",
    "by product",
    "what was the",
)
_DOC_HINTS = (
    "policy",
    "warranty",
    "sla",
    "acknowledg",
    "runbook",
    "handbook",
    "allowed",
    "must",
    "how long",
    "how often",
    "retain",
    "retention",
    "per diem",
    "password",
    "mfa",
    "release",
    "tier",
    "coverage",
    "guarantee",
    "specification",
    "payload",
    "battery",
    "postmortem",
)
_DOMAIN_STOP = {"helios", "robotics", "company", "employee", "employees"}
_DOMAIN_STOP_STEMS = {_stem(w) for w in _DOMAIN_STOP}
_SYNONYMS = {
    "pto": "paid time off accrual vacation",
    "vacation": "pto paid time off",
    "wfh": "remote work hybrid",
    "remote": "remote hybrid work days",
    "mfa": "multi-factor authentication",
    "2fa": "multi-factor authentication",
    "sla": "acknowledge within",
    "warranty": "warranty months coverage",
    "laptop": "laptops refreshed replaced",
    "per diem": "meals per diem city tier",
    "retained": "retention retained years months",
}


def _terms(text: str) -> set[str]:
    return {_stem(w) for w in content_words(text)} - _DOMAIN_STOP_STEMS


class HeuristicReasoner:
    name = "heuristic"

    def __init__(self, catalog: Catalog, *, grade_threshold: float = 0.33) -> None:
        self.catalog = catalog
        self.grade_threshold = grade_threshold

    # ---- routing ----------------------------------------------------------------------------------
    def route(self, question: str, catalog_summary: str) -> RouteDecision:
        q = question.lower()
        sql = any(n in q for n in _DATA_NOUNS) and any(a in q for a in _AGG)
        doc = any(h in q for h in _DOC_HINTS)
        route: Route = "both" if sql and doc else "sql" if sql else "vector"
        return RouteDecision(route=route, reason=f"keyword router (data={sql}, document={doc})")

    # ---- grading ----------------------------------------------------------------------------------
    def grade(self, question: str, chunks: list[ScoredChunk]) -> list[bool]:
        q = _terms(question)
        if not q:
            return [False] * len(chunks)
        out = []
        for c in chunks:
            d = _terms(f"{c.chunk.title} {c.chunk.section} {c.chunk.text}")
            out.append(len(q & d) / len(q) >= self.grade_threshold)
        return out

    def rewrite(self, question: str, previous_query: str, attempt: int) -> str:
        q = question.lower()
        extra = [v for k, v in _SYNONYMS.items() if k in q]
        kept = " ".join(w for w in content_words(question) if w not in _DOMAIN_STOP)
        return " ".join([kept, *extra]).strip() or question

    # ---- text-to-SQL (pattern based) --------------------------------------------------------------
    def draft_sql(self, question: str, schema: str, feedback: str | None = None) -> SqlDraft:
        q = question.lower()
        ds = self.catalog.dataset
        if "headcount" in q or "employees" in q or "people work" in q:
            table, date_col, metric, alias = "headcount", None, "SUM(headcount)", "headcount"
        elif "ticket" in q or "resolution" in q:
            table, date_col = "support_tickets", "created_at"
            if "resolution" in q or "average" in q:
                metric, alias = "ROUND(AVG(resolution_hours), 1)", "avg_resolution_hours"
            else:
                metric, alias = "COUNT(*)", "ticket_count"
        else:
            table, date_col = "sales_orders", "order_date"
            if "units" in q or ("sold" in q and "revenue" not in q):
                metric, alias = "SUM(units)", "units"
            elif "orders" in q and "revenue" not in q:
                metric, alias = "COUNT(*)", "order_count"
            else:
                metric, alias = "ROUND(SUM(revenue_usd), 2)", "revenue_usd"

        spec = self.catalog.table(table)
        where: list[str] = []
        group: str | None = None
        for col in spec.columns if spec else []:
            for val in col.allowed_values:
                if self._mentions(question, val):
                    where.append(f"{col.name} = '{val}'")
                    break
            if re.search(rf"\b(which|what|by|per|each)\s+{col.name.replace('_', '[ _]')}\b", q):
                group = col.name
        if group:
            where = [w for w in where if not w.startswith(f"{group} ")]

        quarter = re.search(r"\b(20\d\d)[- ]?q([1-4])\b|\bq([1-4])\s+(20\d\d)\b", q)
        year = re.search(r"\b(20\d\d)\b", q)
        if quarter:
            y = quarter.group(1) or quarter.group(4)
            n = int(quarter.group(2) or quarter.group(3))
            if table == "headcount":
                where.append(f"quarter = '{y}-Q{n}'")
            elif date_col:
                start_m = 3 * (n - 1) + 1
                end = f"{y}-{start_m + 2:02d}-{[31, 30, 30, 31][n - 1]:02d}"
                where.append(f"DATE({date_col}) BETWEEN '{y}-{start_m:02d}-01' AND '{end}'")
        elif year and date_col:
            where.append(f"EXTRACT(YEAR FROM {date_col}) = {year.group(1)}")
        elif year and table == "headcount":
            where.append(f"quarter LIKE '{year.group(1)}-%'")

        select = f"{group}, {metric} AS {alias}" if group else f"{metric} AS {alias}"
        sql = f"SELECT {select} FROM `{ds}.{table}`"
        if where:
            sql += " WHERE " + " AND ".join(where)
        if group:
            desc = "ASC" if any(w in q for w in ("lowest", "least", "fewest")) else "DESC"
            single = any(w in q for w in ("which", "highest", "most", "lowest", "least", "top"))
            sql += f" GROUP BY {group} ORDER BY {alias} {desc}" + (" LIMIT 1" if single else "")
        return SqlDraft(sql=sql, explanation=f"Pattern-based query over {table}.")

    @staticmethod
    def _mentions(question: str, value: str) -> bool:
        if len(value) <= 4 and value.isupper():
            return re.search(rf"\b{re.escape(value)}\b", question) is not None
        if re.search(rf"\b{re.escape(value)}\b", question, flags=re.I):
            return True
        first = value.split()[0]
        return (
            len(value.split()) > 1
            and len(first) > 3
            and re.search(rf"\b{first}\b", question, re.I) is not None
        )

    # ---- generation -------------------------------------------------------------------------------
    def generate(self, question: str, sources: str, feedback: str | None = None) -> GeneratedAnswer:
        q = _terms(question)
        blocks = re.split(r"\n(?=\[(?:S|SQL)\d+\])", "\n" + sources.strip())
        doc_sents: list[tuple[float, str, str]] = []
        sql_lines: list[str] = []
        for block in blocks:
            m = re.match(r"\s*\[((?:S|SQL)\d+)\][^\n]*\n?(.*)", block, flags=re.S)
            if not m:
                continue
            label, body = m.group(1), m.group(2)
            if label.startswith("SQL"):
                rows = [ln for ln in body.splitlines() if ln.startswith("|") and "---" not in ln]
                if len(rows) >= 2:
                    header = [h.strip() for h in rows[0].strip("|").split("|")]
                    for row in rows[1:4]:
                        cells = [c.strip() for c in row.strip("|").split("|")]
                        pairs = ", ".join(f"{h} = {v}" for h, v in zip(header, cells, strict=False))
                        sql_lines.append(f"The query result is {pairs} [{label}].")
                continue
            for sent in split_sentences(body):
                sent = re.sub(r"\*\*|__|`", "", sent).lstrip("-*| ").strip()
                sent = re.sub(r"^\d+[.)]\s+", "", sent)
                if sent.endswith("?") or len(sent.split()) < 3:
                    continue
                if sent.endswith("|"):
                    sent = " — ".join(c.strip() for c in sent.strip("|").split("|") if c.strip())
                overlap = len(q & _terms(sent))
                if overlap:
                    doc_sents.append((overlap / (len(q) or 1), sent.strip(), label))
        doc_sents.sort(key=lambda x: x[0], reverse=True)
        picked = [f"{s.rstrip('.')} [{lbl}]." for _, s, lbl in doc_sents[:3]]
        parts = picked + sql_lines
        if not parts:
            return GeneratedAnswer(
                answer="I don't have enough information in the available sources to answer that.",
                insufficient_context=True,
            )
        return GeneratedAnswer(answer=" ".join(parts), insufficient_context=False)

    def check_grounding(self, question: str, answer: str, sources: str) -> GroundingVerdict:
        unsupported = [
            s
            for s in split_sentences(answer)
            if len(s.split()) >= 3 and not re.search(r"\[(?:S|SQL)\d+\]", s) and re.search(r"\d", s)
        ]
        return GroundingVerdict(grounded=not unsupported, unsupported_claims=unsupported)
