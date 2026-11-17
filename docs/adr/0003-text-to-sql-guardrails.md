# ADR 0003 — Text-to-SQL guardrails

**Status:** accepted

## Context
LLM-generated SQL runs against BigQuery. Risks: data modification, exfiltration (EXPORT, EXTERNAL_QUERY),
reading other datasets, and expensive scans. Prompt injection inside documents could try to steer the SQL.

## Decision
Layers, outermost first:

1. **IAM (hard boundary).** The runtime SA has only `roles/bigquery.jobUser` (project) and
   `roles/bigquery.dataViewer` on the `enterprise_analytics` dataset. It cannot write or read anything else.
2. **AST guard (sqlglot, BigQuery dialect)**, in `src/rag/sql/guard.py`:
   - exactly one statement;
   - root must be `SELECT` or a set operation (`UNION`/`INTERSECT`/`EXCEPT`), CTEs allowed;
   - reject DDL/DML/`MERGE`/`EXPORT`/`DECLARE`/`SET`/`CALL`/`EXECUTE IMMEDIATE`/scripting (`Command`) nodes
     anywhere in the tree;
   - reject table-valued functions (any `Table` whose name isn't an identifier, e.g. `ML.PREDICT(...)`) and
     `EXTERNAL_QUERY` anywhere;
   - every table must be allow-listed in the configured dataset and project. CTE names are exempt;
     `INFORMATION_SCHEMA` and other datasets are rejected;
   - tables are rewritten fully qualified, and the outer `LIMIT` is forced to `≤ 200`.
3. **Dry run.** Reject if estimated bytes exceed `maximum_bytes_billed` (1 GB default).
4. **Execution** with `maximum_bytes_billed`, job labels (`app=enterprise-rag`) and a timeout.
5. **Repair loop.** Guard rejections and execution errors go back to the drafter as feedback, up to 2 times.

## Consequences
* 22 parametrised attack cases are unit-tested. The repair loop is tested end to end on DuckDB.
* The guard is deliberately conservative: legitimate but exotic SQL (TVFs, scripting) is refused.
* Cost per SQL question is bounded: ≤ 1 GB billed ≈ $0.006 at $6.25/TiB. The demo tables are a few hundred KB.
