"""BigQuery engine: dry run (bytes check) → execute with ``maximum_bytes_billed``, labels and timeout.

Also seeds the ``enterprise_analytics`` dataset from ``schema.yaml`` + CSVs, writing table and column
descriptions into BigQuery metadata (the text-to-SQL catalog is built from that metadata).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from rag.observability import Timer
from rag.sql.catalog import Catalog
from rag.sql.duckdb_engine import to_jsonable
from rag.sql.engines import BytesLimitExceeded, QueryOutput, SqlExecutionError
from rag.telemetry import current_tracker

log = logging.getLogger(__name__)

LABELS = {"app": "enterprise-rag", "component": "text-to-sql"}


class BigQueryEngine:
    name = "bigquery"

    def __init__(
        self,
        *,
        project: str,
        location: str = "US",
        max_bytes_billed: int = 1_000_000_000,
        timeout_s: float = 30.0,
        client: Any = None,
    ) -> None:
        if client is None:
            from google.cloud import bigquery

            client = bigquery.Client(project=project, location=location)
        self.client = client
        self.project = project
        self.max_bytes_billed = max_bytes_billed
        self.timeout_s = timeout_s

    def _job_config(self, **kw: Any) -> Any:
        from google.cloud import bigquery

        return bigquery.QueryJobConfig(labels=LABELS, use_legacy_sql=False, **kw)

    def dry_run(self, sql: str) -> int:
        try:
            job = self.client.query(sql, job_config=self._job_config(dry_run=True, use_query_cache=False))
        except Exception as e:
            raise SqlExecutionError(_short(e)) from e
        estimated = int(job.total_bytes_processed or 0)
        if estimated > self.max_bytes_billed:
            raise BytesLimitExceeded(
                f"query would process {estimated} bytes, above the {self.max_bytes_billed} byte limit"
            )
        return estimated

    def execute(self, sql: str) -> QueryOutput:
        timer = Timer()
        try:
            job = self.client.query(
                sql, job_config=self._job_config(maximum_bytes_billed=self.max_bytes_billed)
            )
            result = job.result(timeout=self.timeout_s)
        except Exception as e:
            raise SqlExecutionError(_short(e)) from e
        cols = [f.name for f in result.schema]
        rows = [[to_jsonable(v) for v in row.values()] for row in result]
        billed = int(job.total_bytes_billed or 0)
        tracker = current_tracker()
        if tracker:
            tracker.add_bigquery(bytes_billed=billed, latency_ms=timer.ms)
        return QueryOutput(columns=cols, rows=rows, bytes_processed=int(job.total_bytes_processed or 0))

    # ---------------------------------------------------------------------------------------------
    def seed(self, catalog: Catalog, data_dir: str | Path, location: str = "US") -> dict[str, int]:
        from google.cloud import bigquery

        ds_ref = bigquery.DatasetReference(self.project, catalog.dataset)
        ds = bigquery.Dataset(ds_ref)
        ds.location = location
        ds.description = catalog.description
        self.client.create_dataset(ds, exists_ok=True)
        counts: dict[str, int] = {}
        for t in catalog.tables:
            schema = [
                bigquery.SchemaField(c.name, c.type.upper(), mode="NULLABLE", description=c.full_description)
                for c in t.columns
            ]
            table_id = f"{self.project}.{catalog.dataset}.{t.name}"
            job_config = bigquery.LoadJobConfig(
                schema=schema,
                source_format=bigquery.SourceFormat.CSV,
                skip_leading_rows=1,
                write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
                null_marker="",
                labels={"app": "enterprise-rag", "component": "seed"},
            )
            with (Path(data_dir) / f"{t.name}.csv").open("rb") as f:
                self.client.load_table_from_file(f, table_id, job_config=job_config).result(timeout=300)
            table = self.client.get_table(table_id)
            table.description = t.description.strip()
            table.schema = schema  # load jobs may drop descriptions; set them explicitly
            self.client.update_table(table, ["description", "schema"])
            counts[t.name] = int(table.num_rows or 0)
            log.info("seeded table", extra={"table": table_id, "rows": counts[t.name]})
        return counts


def _short(e: Exception) -> str:
    msg = getattr(e, "message", None) or str(e)
    return msg.splitlines()[0][:500]
