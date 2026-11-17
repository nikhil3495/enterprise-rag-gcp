"""``rag`` command-line interface (also the Cloud Run Job entrypoint)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated

import typer

from rag.config import Settings
from rag.observability import configure_logging

app = typer.Typer(add_completion=False, help="Enterprise RAG assistant CLI", no_args_is_help=True)


def _container(settings: Settings | None = None):  # type: ignore[no-untyped-def]
    from rag.container import Container

    s = settings or Settings()
    configure_logging(s.log_level, sys.stderr)
    return Container(s)


@app.command()
def ingest(
    source: Annotated[
        str | None, typer.Option(help="Local dir or gs:// prefix (default: RAG_DOCS_SOURCE)")
    ] = None,
    prune: Annotated[bool, typer.Option(help="Delete documents that disappeared from the source")] = False,
) -> None:
    """Chunk + embed documents into the vector store and docstore (idempotent)."""
    c = _container()
    report = c.pipeline.sync(source or c.settings.docs_source, prune=prune)
    typer.echo(json.dumps(report.as_dict(), indent=2))


@app.command()
def query(
    question: str,
    top_k: Annotated[int | None, typer.Option()] = None,
    debug: Annotated[bool, typer.Option()] = False,
) -> None:
    """Ask a question through the full LangGraph pipeline."""
    c = _container()
    r = c.service.answer(question, top_k=top_k)
    out = {
        "answer": r.answer,
        "route": r.route,
        "grounded": r.grounded,
        "insufficient_context": r.insufficient_context,
        "citations": [x.model_dump(exclude_none=True) for x in r.citations],
        "sql": r.sql.sql if r.sql else None,
        "usage": r.usage,
        "latency_ms": r.latency_ms,
    }
    if debug:
        out["trace"] = r.trace
    typer.echo(json.dumps(out, indent=2, default=str))


@app.command("seed-bigquery")
def seed_bigquery() -> None:
    """Create/replace BigQuery tables from schema.yaml + CSVs, writing column descriptions."""
    from rag.sql.bigquery_engine import BigQueryEngine
    from rag.sql.catalog import Catalog

    s = Settings()
    configure_logging(s.log_level, sys.stderr)
    if not s.gcp_project:
        typer.echo("RAG_GCP_PROJECT is required", err=True)
        raise typer.Exit(2)
    catalog = Catalog.from_yaml(Path(s.structured_dir) / "schema.yaml")
    engine = BigQueryEngine(project=s.gcp_project, location=s.bq_location)
    typer.echo(json.dumps(engine.seed(catalog, s.structured_dir, location=s.bq_location), indent=2))


@app.command("eval")
def run_eval(
    target: Annotated[str, typer.Option(help="local | http")] = "local",
    url: Annotated[str | None, typer.Option(help="Service URL for --target http")] = None,
    golden: Annotated[str, typer.Option()] = "eval/golden_set.jsonl",
    thresholds: Annotated[str, typer.Option()] = "eval/thresholds.yaml",
    profile: Annotated[str | None, typer.Option(help="Threshold profile: offline | live")] = None,
    limit: Annotated[int | None, typer.Option(help="Evaluate only the first N items (stratified)")] = None,
    out_dir: Annotated[str, typer.Option()] = "eval/reports",
    judge: Annotated[str, typer.Option(help="auto | gemini | heuristic")] = "auto",
) -> None:
    """Run the evaluation harness on the golden set; non-zero exit on threshold regression."""
    from rag.eval.runner import run

    code = run(
        target=target,
        url=url,
        golden_path=golden,
        thresholds_path=thresholds,
        profile=profile,
        limit=limit,
        out_dir=out_dir,
        judge_mode=judge,
    )
    raise typer.Exit(code)


@app.command("export-graph")
def export_graph(out: Annotated[str | None, typer.Option()] = None) -> None:
    """Print (or write) the LangGraph Mermaid diagram."""
    from rag.graph.builder import mermaid

    text = mermaid(_container().graph_deps)
    if out:
        Path(out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text + "\n")


@app.command()
def stats() -> None:
    """Show index statistics (vector count, documents)."""
    c = _container()
    docs = c.docstore.list_documents()
    typer.echo(
        json.dumps(
            {
                "vectors": c.vector_store.count(),
                "documents": len(docs),
                "chunks": sum(len(d.chunk_ids) for d in docs),
                "sources": [d.source_uri for d in docs],
            },
            indent=2,
        )
    )


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8080) -> None:
    """Run the API locally with uvicorn."""
    import uvicorn

    uvicorn.run("rag.api.main:app", host=host, port=port, log_config=None)


if __name__ == "__main__":
    app()
