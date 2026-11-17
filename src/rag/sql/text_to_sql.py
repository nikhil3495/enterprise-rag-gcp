"""Text-to-SQL tool: draft (structured JSON) → guard → dry run → execute, with a repair loop.

On a guard or execution error the error message is fed back to the drafter, up to ``max_repairs`` times.
"""

from __future__ import annotations

import logging
from typing import Protocol

from pydantic import BaseModel

from rag.models import SqlResult
from rag.sql.catalog import Catalog
from rag.sql.engines import SqlEngine, SqlExecutionError
from rag.sql.guard import SqlGuard, SqlGuardError

log = logging.getLogger(__name__)


class SqlDraft(BaseModel):
    sql: str
    explanation: str = ""


class SqlDrafter(Protocol):
    def draft_sql(self, question: str, schema: str, feedback: str | None = None) -> SqlDraft: ...


class TextToSqlTool:
    def __init__(
        self,
        *,
        drafter: SqlDrafter,
        guard: SqlGuard,
        engine: SqlEngine,
        catalog: Catalog,
        project: str,
        max_repairs: int = 2,
    ) -> None:
        self.drafter = drafter
        self.guard = guard
        self.engine = engine
        self.catalog = catalog
        self.project = project
        self.max_repairs = max_repairs
        self.schema_text = catalog.detailed(project)

    def run(self, question: str) -> SqlResult:
        feedback: str | None = None
        last = SqlResult()
        for attempt in range(1, self.max_repairs + 2):
            try:
                draft = self.drafter.draft_sql(question, self.schema_text, feedback)
            except Exception as e:  # upstream LLM failure: not repairable here
                log.warning("sql draft failed", extra={"error": str(e)})
                return SqlResult(ok=False, attempts=attempt, error=f"draft failed: {e}")
            last = SqlResult(sql=draft.sql, explanation=draft.explanation, attempts=attempt)
            try:
                guarded = self.guard.validate(draft.sql)
                last.sql = guarded.sql
                self.engine.dry_run(guarded.sql)
                out = self.engine.execute(guarded.sql)
            except (SqlGuardError, SqlExecutionError) as e:
                kind = "rejected by guard" if isinstance(e, SqlGuardError) else "failed"
                feedback = f"The previous SQL {kind}: {e}\nPrevious SQL:\n{draft.sql}"
                last.error = str(e)
                log.info("sql attempt failed", extra={"attempt": attempt, "error": str(e)})
                continue
            return SqlResult(
                ok=True,
                sql=guarded.sql,
                explanation=draft.explanation,
                columns=out.columns,
                rows=out.rows,
                row_count=len(out.rows),
                bytes_processed=out.bytes_processed,
                attempts=attempt,
            )
        return last
