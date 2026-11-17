<#
.SYNOPSIS
  Smoke test a deployed service: .\scripts\smoke_test.ps1 -Url https://rag-api-...run.app
#>
param([Parameter(Mandatory)][string]$Url)
$ErrorActionPreference = "Stop"
$Url = $Url.TrimEnd("/")
$Token = if ($env:RAG_ID_TOKEN) { $env:RAG_ID_TOKEN } else { (& gcloud auth print-identity-token).Trim() }
$Headers = @{ Authorization = "Bearer $Token" }

function Get-StatusCode([string]$u, [hashtable]$h) {
    try { (Invoke-WebRequest -Uri $u -Headers $h -UseBasicParsing -Method Get).StatusCode }
    catch { [int]$_.Exception.Response.StatusCode }
}

$code = Get-StatusCode "$Url/v1/info" @{}
if ($code -ne 403 -and $code -ne 401) { throw "unauthenticated request returned $code (expected 403)" }
Write-Host "ok  unauthenticated -> $code"
$code = Get-StatusCode "$Url/healthz" $Headers
if ($code -ne 200) { throw "authenticated /healthz returned $code" }
Write-Host "ok  /healthz -> 200"

function Test-Question([string]$q, [string]$route) {
    $body = @{ question = $q } | ConvertTo-Json
    $r = Invoke-RestMethod -Uri "$Url/v1/query" -Method Post -Headers $Headers -ContentType "application/json" `
        -Body $body -TimeoutSec 120
    if ($r.route -ne $route) { throw "route $($r.route) != $route for: $q" }
    if (-not $r.citations -or $r.citations.Count -eq 0) { throw "no citations for: $q" }
    if ($r.insufficient_context) { throw "abstained on: $q" }
    $ans = $r.answer.Substring(0, [Math]::Min(140, $r.answer.Length))
    Write-Host ("ok  [{0}] {1:N0} ms `${2:N5} :: {3}" -f $r.route, $r.latency_ms, $r.usage.cost_usd, $ans)
}

Test-Question "What is the minimum password length required by the security policy?" "vector"
Test-Question "How many SEV1 support tickets were opened in 2025?" "sql"
Test-Question "What is the standard warranty period for the Atlas AMR, and how many SEV1 support tickets were opened for the Atlas AMR in 2025?" "both"
Write-Host "SMOKE TEST PASSED" -ForegroundColor Green
