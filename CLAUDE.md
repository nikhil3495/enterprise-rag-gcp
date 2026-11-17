# CLAUDE.md — working notes for AI assistants

Start every session by reading `PROGRESS.md` and `BLOCKERS.md`.

## Commands
- Install: `uv sync --frozen` · Tests: `uv run pytest --cov` · Lint: `uv run ruff check . && uv run ruff format --check .` · Types: `uv run mypy`
- Offline eval: `uv run rag ingest && uv run rag eval --target local`
- Windows task runner: `.\tasks.ps1 <target>` (mirrors the Makefile)
- Regenerate data and golden set (deterministic): `uv run python scripts/generate_sample_data.py`

## Conventions
- Every cloud dependency sits behind a protocol in `providers/base.py` or `store/base.py`, with an offline
  implementation. Never call a cloud SDK from graph or ingestion logic directly; wire it in `container.py`.
- Model IDs and prices live only in `config.py` (env `RAG_*`).
- Graph nodes are pure functions `(state, deps) -> partial update` and append to `trace`.
- Never hand-write eval numbers. README and FINAL_REPORT metrics must be copied from a committed
  `eval/reports/*` file.
- Never commit `.env`, keys or tfstate. Run `scripts/secret_scan.sh` before pushing.
- Conventional Commits.

## Gotchas
- On Windows, Python `Path.write_text` defaults to cp1252: always pass `encoding="utf-8"`.
- PowerShell 5.1 `Set-Content -Encoding utf8` writes a BOM.
- The GitHub token needs the `workflow` scope to push `.github/workflows/*`.
- `terraform` from winget is not on Git Bash's PATH until a new shell starts; use PowerShell.
