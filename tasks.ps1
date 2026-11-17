<#
.SYNOPSIS
  Windows equivalent of the Makefile:  .\tasks.ps1 <target>
  Targets: setup lint fmt typecheck test dev ingest-local eval docker-build deploy smoke eval-live vs-undeploy teardown
#>
param([Parameter(Position = 0)][string]$Target = "help", [string]$Url = "")
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
function Invoke-Native {
    # Simple (non-advanced) function: every token, including --flags, lands in $args untouched.
    $exe = $args[0]
    $rest = @($args | Select-Object -Skip 1)
    & $exe @rest
    if ($LASTEXITCODE -ne 0) { throw "$exe $($rest -join ' ') failed with exit code $LASTEXITCODE" }
}
function Get-ServiceUrl {
    if ($Url) { return $Url }
    if ($env:SERVICE_URL) { return $env:SERVICE_URL }
    return (& terraform -chdir=infra/terraform output -raw service_url).Trim()
}

switch ($Target) {
    "setup" { Invoke-Native uv sync --frozen; Invoke-Native uv run python scripts/generate_sample_data.py }
    "lint" { Invoke-Native uv run ruff check .; Invoke-Native uv run ruff format --check . }
    "fmt" { Invoke-Native uv run ruff check --fix .; Invoke-Native uv run ruff format . }
    "typecheck" { Invoke-Native uv run mypy }
    "test" { Invoke-Native uv run pytest --cov --cov-fail-under=80 }
    "dev" { Invoke-Native uv run rag serve --port 8080 }
    "ingest-local" { Invoke-Native uv run rag ingest --prune }
    "eval" { Invoke-Native uv run rag ingest; Invoke-Native uv run rag eval --target local }
    "docker-build" { Invoke-Native docker build -t rag-api:local . }
    "deploy" { & .\scripts\deploy.ps1 -Stage all }
    "smoke" { & .\scripts\smoke_test.ps1 -Url (Get-ServiceUrl) }
    "eval-live" { Invoke-Native uv run rag eval --target http --url (Get-ServiceUrl) }
    "vs-undeploy" { & .\scripts\vs_undeploy.ps1 }
    "teardown" { & .\scripts\teardown.ps1 }
    default { Get-Help $PSCommandPath -Detailed }
}
