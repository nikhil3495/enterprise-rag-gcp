"""SQL engine protocol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class QueryOutput:
    columns: list[str]
    rows: list[list[Any]]
    bytes_processed: int = 0


class SqlEngine(Protocol):
    name: str

    def dry_run(self, sql: str) -> int:
        """Return estimated bytes processed; raise on invalid SQL."""
        ...

    def execute(self, sql: str) -> QueryOutput: ...


class SqlExecutionError(RuntimeError):
    pass


class BytesLimitExceeded(SqlExecutionError):
    pass
