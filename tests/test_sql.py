from __future__ import annotations

from pathlib import Path

import pytest

from rag.sql.catalog import Catalog
from rag.sql.duckdb_engine import DuckDBEngine
from rag.sql.engines import BytesLimitExceeded, QueryOutput, SqlExecutionError
from rag.sql.guard import SqlGuard, SqlGuardError
from rag.sql.text_to_sql import SqlDraft, TextToSqlTool

ROOT = Path(__file__).resolve().parents[1]
CATALOG = Catalog.from_yaml(ROOT / "data" / "structured" / "schema.yaml")


@pytest.fixture(scope="module")
def guard() -> SqlGuard:
    return SqlGuard(
        project="proj-1", dataset="enterprise_analytics", allowed_tables=CATALOG.table_names, max_limit=200
    )


@pytest.fixture(scope="module")
def duck() -> DuckDBEngine:
    return DuckDBEngine(CATALOG, ROOT / "data" / "structured")


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        ("DELETE FROM sales_orders WHERE TRUE", "DELETE"),
        ("INSERT INTO sales_orders (order_id) VALUES ('x')", "INSERT"),
        ("UPDATE sales_orders SET units = 0 WHERE TRUE", "UPDATE"),
        ("MERGE sales_orders t USING headcount s ON FALSE WHEN NOT MATCHED THEN INSERT ROW", "MERGE"),
        ("DROP TABLE sales_orders", "DROP"),
        ("CREATE TABLE x AS SELECT 1", "CREATE"),
        ("SELECT 1 FROM sales_orders; DROP TABLE sales_orders", "exactly one statement"),
        ("SELECT * FROM sales_orders; SELECT * FROM headcount", "exactly one statement"),
        ("EXPORT DATA OPTIONS(uri='gs://b/*', format='CSV') AS SELECT * FROM sales_orders", "EXPORT"),
        ("DECLARE x INT64 DEFAULT 1", "DECLARE"),
        ("SET @@query_label = 'x'", "SET"),
        ("SELECT * FROM EXTERNAL_QUERY('conn', 'SELECT 1')", "EXTERNAL_QUERY"),
        ("SELECT (SELECT 1 FROM EXTERNAL_QUERY('c', 'q')) AS x FROM sales_orders", "EXTERNAL_QUERY"),
        ("SELECT * FROM ML.PREDICT(MODEL m, TABLE sales_orders)", "table-valued"),
        ("SELECT * FROM secrets", "not allowed"),
        ("SELECT * FROM other_ds.sales_orders", "dataset 'other_ds'"),
        ("SELECT * FROM `evil-proj.enterprise_analytics.sales_orders`", "project 'evil-proj'"),
        ("SELECT * FROM `region-us`.INFORMATION_SCHEMA.TABLES", "not allowed"),
        ("CALL proc()", "COMMAND"),
        ("SELECT 1", "at least one allowed table"),
        ("", "empty"),
        ("SELEC * FORM x", ""),
    ],
)
def test_guard_rejects(guard: SqlGuard, sql: str, reason: str) -> None:
    with pytest.raises(SqlGuardError, match=reason or None):
        guard.validate(sql)


def test_guard_qualifies_and_clamps_limit(guard: SqlGuard) -> None:
    g = guard.validate("SELECT region, SUM(revenue_usd) FROM sales_orders GROUP BY region LIMIT 100000")
    assert "`proj-1`.enterprise_analytics.sales_orders" in g.sql and g.sql.endswith("LIMIT 200")
    assert g.limit == 200 and g.tables == ("sales_orders",)


def test_guard_adds_limit_and_keeps_small_limit(guard: SqlGuard) -> None:
    assert guard.validate("SELECT * FROM headcount").sql.endswith("LIMIT 200")
    assert guard.validate("SELECT * FROM headcount LIMIT 5").limit == 5
    assert guard.validate("SELECT * FROM headcount LIMIT @n").limit == 200  # non-literal → clamp


def test_guard_allows_cte_union_and_qualified_names(guard: SqlGuard) -> None:
    g = guard.validate(
        "WITH t AS (SELECT product FROM enterprise_analytics.support_tickets) "
        "SELECT product FROM t UNION ALL SELECT product FROM `proj-1.enterprise_analytics.sales_orders`;"
    )
    assert "FROM t" in g.sql and set(g.tables) == {"support_tickets", "sales_orders"}
    assert g.sql.endswith("LIMIT 200")


def test_duckdb_engine_transpiles_bigquery(duck: DuckDBEngine, guard: SqlGuard) -> None:
    g = guard.validate(
        "SELECT COUNT(*) AS n FROM support_tickets WHERE severity = 'SEV1' AND EXTRACT(YEAR FROM created_at) = 2025"
    )
    out = duck.execute(g.sql)
    assert out.columns == ["n"] and out.rows[0][0] > 0
    assert duck.dry_run(g.sql) == 0
    with pytest.raises(SqlExecutionError):
        duck.execute("SELECT nope FROM `p.enterprise_analytics.headcount`")
    with pytest.raises(SqlExecutionError):
        duck.dry_run("SELECT nope FROM `p.enterprise_analytics.headcount`")


class ScriptedDrafter:
    def __init__(self, drafts: list[str]) -> None:
        self.drafts = drafts
        self.feedback: list[str | None] = []

    def draft_sql(self, question: str, schema: str, feedback: str | None = None) -> SqlDraft:
        self.feedback.append(feedback)
        return SqlDraft(sql=self.drafts[min(len(self.feedback) - 1, len(self.drafts) - 1)], explanation="x")


def _tool(drafter: object, guard: SqlGuard, engine: object, repairs: int = 2) -> TextToSqlTool:
    return TextToSqlTool(
        drafter=drafter,
        guard=guard,
        engine=engine,
        catalog=CATALOG,  # type: ignore[arg-type]
        project="proj-1",
        max_repairs=repairs,
    )


def test_repair_loop_recovers_from_guard_and_execution_errors(guard: SqlGuard, duck: DuckDBEngine) -> None:
    drafter = ScriptedDrafter(
        [
            "DELETE FROM sales_orders",  # guard rejection
            "SELECT missing_col FROM sales_orders",  # execution error
            "SELECT SUM(units) AS u FROM sales_orders WHERE product = 'Atlas AMR'",
        ]
    )
    res = _tool(drafter, guard, duck).run("units of atlas?")
    assert res.ok and res.attempts == 3 and res.rows[0][0] > 0
    assert drafter.feedback[0] is None
    assert "rejected by guard" in (drafter.feedback[1] or "")
    assert "failed" in (drafter.feedback[2] or "") and "missing_col" in (drafter.feedback[2] or "")
    assert "Allowed values: NA, EMEA, APAC, LATAM" in _tool(drafter, guard, duck).schema_text


def test_repair_loop_gives_up(guard: SqlGuard, duck: DuckDBEngine) -> None:
    res = _tool(ScriptedDrafter(["DROP TABLE headcount"]), guard, duck, repairs=2).run("q")
    assert not res.ok and res.attempts == 3 and "DROP" in (res.error or "")


def test_bytes_limit_is_repairable_error(guard: SqlGuard) -> None:
    class Engine:
        name = "fake"
        calls = 0

        def dry_run(self, sql: str) -> int:
            self.calls += 1
            if self.calls == 1:
                raise BytesLimitExceeded("too many bytes")
            return 10

        def execute(self, sql: str) -> QueryOutput:
            return QueryOutput(["x"], [[1]], 10)

    res = _tool(ScriptedDrafter(["SELECT * FROM headcount"]), guard, Engine()).run("q")
    assert res.ok and res.attempts == 2 and res.bytes_processed == 10


def test_drafter_failure_is_not_retried(guard: SqlGuard, duck: DuckDBEngine) -> None:
    class Boom:
        def draft_sql(self, *a: object, **k: object) -> SqlDraft:
            raise RuntimeError("llm down")

    res = _tool(Boom(), guard, duck).run("q")
    assert not res.ok and "llm down" in (res.error or "")


def test_catalog_summary_and_detailed() -> None:
    assert CATALOG.summary().count("\n") == 2
    assert "`p.enterprise_analytics.sales_orders`" in CATALOG.detailed("p")
    assert CATALOG.table("SALES_ORDERS") is not None and CATALOG.table("nope") is None
