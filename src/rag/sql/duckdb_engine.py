"""Local SQL engine: DuckDB loaded from the same CSVs + schema.yaml used to seed BigQuery.

The text-to-SQL tool always produces BigQuery SQL; this engine transpiles it with sqlglot and strips the
``project.dataset`` qualification so local runs exercise the same prompts, guard and repair loop.
"""

from __future__ import annotations

import datetime as dt
import decimal
import threading
from pathlib import Path
from typing import Any

import duckdb
import sqlglot
from sqlglot import exp

from rag.sql.catalog import Catalog
from rag.sql.engines import QueryOutput, SqlExecutionError

_TYPES = {
    "STRING": "VARCHAR",
    "INT64": "BIGINT",
    "FLOAT64": "DOUBLE",
    "NUMERIC": "DECIMAL(18,2)",
    "DATE": "DATE",
    "TIMESTAMP": "TIMESTAMP",
    "BOOL": "BOOLEAN",
}


def to_jsonable(v: Any) -> Any:
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, dt.datetime | dt.date):
        return v.isoformat()
    return v


class DuckDBEngine:
    name = "duckdb"

    def __init__(self, catalog: Catalog, data_dir: str | Path) -> None:
        self.catalog = catalog
        self._lock = threading.Lock()
        self._con = duckdb.connect(":memory:")
        for t in catalog.tables:
            cols = ", ".join(f'"{c.name}" {_TYPES.get(c.type.upper(), "VARCHAR")}' for c in t.columns)
            self._con.execute(f'CREATE TABLE "{t.name}" ({cols})')
            csv_path = Path(data_dir) / f"{t.name}.csv"
            if csv_path.exists():
                self._con.execute(
                    f'INSERT INTO "{t.name}" SELECT * FROM read_csv(?, header=true, columns='
                    f"{ {c.name: _TYPES.get(c.type.upper(), 'VARCHAR') for c in t.columns} }, nullstr='')",
                    [str(csv_path)],
                )

    @staticmethod
    def transpile(sql: str) -> str:
        tree = sqlglot.parse_one(sql, read="bigquery")
        for table in tree.find_all(exp.Table):
            table.set("db", None)
            table.set("catalog", None)
        return tree.sql(dialect="duckdb")

    def dry_run(self, sql: str) -> int:
        try:
            with self._lock:
                self._con.execute("EXPLAIN " + self.transpile(sql))
        except Exception as e:
            raise SqlExecutionError(str(e).splitlines()[0]) from e
        return 0

    def execute(self, sql: str) -> QueryOutput:
        try:
            with self._lock:
                cur = self._con.execute(self.transpile(sql))
                cols = [d[0] for d in cur.description or []]
                rows = [[to_jsonable(v) for v in row] for row in cur.fetchall()]
        except Exception as e:
            raise SqlExecutionError(str(e).splitlines()[0]) from e
        return QueryOutput(columns=cols, rows=rows, bytes_processed=0)
