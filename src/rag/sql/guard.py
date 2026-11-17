"""SQL guard (defence in depth; IAM read-only roles are the hard boundary).

Rules, enforced on the sqlglot AST (BigQuery dialect):

1. exactly one statement;
2. the root is a read-only ``SELECT`` / set operation (``UNION`` …), optionally with CTEs;
3. no DDL / DML / ``EXPORT`` / ``DECLARE`` / ``SET`` / scripting / ``CALL`` nodes anywhere in the tree;
4. no table-valued functions (``EXTERNAL_QUERY``, ``ML.*`` …) and no ``EXTERNAL_QUERY`` call anywhere;
5. every table reference is an allow-listed table in the configured dataset (CTE names excepted);
6. tables are rewritten fully qualified (``project.dataset.table``);
7. the outermost ``LIMIT`` is forced to ``<= max_limit``.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

_FORBIDDEN: tuple[type[exp.Expression], ...] = tuple(
    getattr(exp, name)
    for name in (
        "Insert",
        "Update",
        "Delete",
        "Merge",
        "Create",
        "Drop",
        "Alter",
        "TruncateTable",
        "Command",
        "Declare",
        "Set",
        "Export",
        "Transaction",
        "Commit",
        "Rollback",
        "Use",
        "Grant",
        "Revoke",
        "Execute",
        "LoadData",
        "Copy",
        "Into",
        "Describe",
        "Pragma",
        "Analyze",
    )
    if hasattr(exp, name)
)
_FORBIDDEN_FUNCS = {"EXTERNAL_QUERY", "EXTERNAL_OBJECT_TRANSFORM", "SESSION_USER", "VECTOR_SEARCH"}


class SqlGuardError(ValueError):
    pass


@dataclass(frozen=True)
class GuardedSql:
    sql: str
    tables: tuple[str, ...]
    limit: int


class SqlGuard:
    def __init__(
        self, *, project: str, dataset: str, allowed_tables: list[str], max_limit: int = 200
    ) -> None:
        self.project = project
        self.dataset = dataset
        self.allowed = {t.lower() for t in allowed_tables}
        self.max_limit = max_limit

    def validate(self, sql: str) -> GuardedSql:
        sql = (sql or "").strip().rstrip(";").strip()
        if not sql:
            raise SqlGuardError("empty SQL")
        try:
            statements = [s for s in sqlglot.parse(sql, read="bigquery") if s is not None]
        except ParseError as e:
            raise SqlGuardError(f"SQL does not parse: {str(e).splitlines()[0]}") from e
        if len(statements) != 1:
            raise SqlGuardError(f"exactly one statement is allowed (got {len(statements)})")
        tree = statements[0]

        if not isinstance(tree, exp.Select | exp.SetOperation | exp.Subquery):
            raise SqlGuardError(
                f"only read-only SELECT queries are allowed (got {type(tree).__name__.upper()})"
            )
        for node in tree.walk():
            if isinstance(node, _FORBIDDEN):
                raise SqlGuardError(f"forbidden statement or clause: {type(node).__name__.upper()}")
            if isinstance(node, exp.Func):
                fname = (node.sql_name() if not isinstance(node, exp.Anonymous) else str(node.this)).upper()
                if fname in _FORBIDDEN_FUNCS:
                    raise SqlGuardError(f"forbidden function: {fname}")

        cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
        tables: list[str] = []
        for table in tree.find_all(exp.Table):
            if not isinstance(table.this, exp.Identifier):
                raise SqlGuardError("table-valued functions are not allowed")
            name = table.name.lower()
            if not table.args.get("db") and name in cte_names:
                continue
            db, catalog = table.db, table.catalog
            if db and db.lower() != self.dataset.lower():
                raise SqlGuardError(f"dataset '{db}' is not allowed; use {self.dataset}")
            if catalog and catalog.lower() != self.project.lower():
                raise SqlGuardError(f"project '{catalog}' is not allowed")
            if name not in self.allowed:
                raise SqlGuardError(
                    f"table '{table.name}' is not allowed; allowed tables: {', '.join(sorted(self.allowed))}"
                )
            table.set("db", exp.to_identifier(self.dataset))
            table.set("catalog", exp.to_identifier(self.project))
            tables.append(name)
        if not tables:
            raise SqlGuardError("query must read from at least one allowed table")

        limit = self._clamp_limit(tree)
        rendered = tree.sql(dialect="bigquery")
        return GuardedSql(sql=rendered, tables=tuple(dict.fromkeys(tables)), limit=limit)

    def _clamp_limit(self, tree: exp.Expression) -> int:
        target = tree.this if isinstance(tree, exp.Subquery) else tree
        current = target.args.get("limit")
        value: int | None = None
        if isinstance(current, exp.Limit):
            lit = current.expression
            if isinstance(lit, exp.Literal) and lit.is_int:
                value = int(lit.this)
        new_limit = self.max_limit if value is None or value > self.max_limit or value < 0 else value
        target.set("limit", exp.Limit(expression=exp.Literal.number(new_limit)))
        if target.args.get("offset") is not None and not isinstance(
            target.args["offset"].expression, exp.Literal
        ):
            raise SqlGuardError("OFFSET must be a literal")
        return new_limit
