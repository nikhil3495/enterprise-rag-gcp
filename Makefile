# Unix/macOS/Git-Bash task runner. Windows PowerShell: .\tasks.ps1 <target>
SHELL := bash
SERVICE_URL ?= $(shell terraform -chdir=infra/terraform output -raw service_url 2>/dev/null)

.PHONY: setup lint fmt typecheck test dev ingest-local eval docker-build deploy smoke eval-live vs-undeploy teardown secret-scan

setup:         ## install locked deps + regenerate deterministic sample data
	uv sync --frozen
	uv run python scripts/generate_sample_data.py

lint:
	uv run ruff check .
	uv run ruff format --check .

fmt:
	uv run ruff check --fix .
	uv run ruff format .

typecheck:
	uv run mypy

test:
	uv run pytest --cov --cov-fail-under=80

dev:           ## local API on :8080 (offline profile)
	uv run rag serve --port 8080

ingest-local:
	uv run rag ingest --prune

eval:          ## offline eval with threshold gate
	uv run rag ingest
	uv run rag eval --target local

docker-build:
	docker build -t rag-api:local .

deploy:        ## infra + image + rollout + seed + ingest + repo vars + smoke
	./scripts/deploy.sh all

smoke:
	./scripts/smoke_test.sh "$(SERVICE_URL)"

eval-live:
	uv run rag eval --target http --url "$(SERVICE_URL)"

vs-undeploy:   ## stop Vector Search node billing (keeps index data)
	./scripts/vs_undeploy.sh

teardown:      ## terraform plan -destroy; run ./scripts/teardown.sh --yes to destroy
	./scripts/teardown.sh --plan

secret-scan:
	./scripts/secret_scan.sh
