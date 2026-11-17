<#
.SYNOPSIS
  Stop Vector Search node-hour billing by undeploying the index (index data is kept).
  Redeploy with: .\scripts\deploy.ps1 -Stage infra
#>
$ErrorActionPreference = "Stop"
$Tf = Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..")) "infra\terraform"
$Project = if ($env:RAG_GCP_PROJECT) { $env:RAG_GCP_PROJECT } else { (& gcloud config get-value project 2>$null) }
$Owner = if ($env:OWNER_EMAIL) { $env:OWNER_EMAIL } else { (& gcloud config get-value account 2>$null) }
& terraform "-chdir=$Tf" init -input=false -reconfigure "-backend-config=bucket=$Project-tfstate" | Out-Null
& terraform "-chdir=$Tf" apply -input=false -auto-approve "-var=project_id=$Project" "-var=owner_email=$Owner" `
    "-var=vs_deployed=false" "-target=google_vertex_ai_index_endpoint_deployed_index.rag"
if ($LASTEXITCODE -ne 0) { throw "terraform apply failed" }
Write-Host "Vector Search index undeployed. Queries return 503 until redeployed (.\scripts\deploy.ps1 -Stage infra)."
