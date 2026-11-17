<#
.SYNOPSIS
  .\scripts\teardown.ps1          -> terraform plan -destroy (safe preview)
  .\scripts\teardown.ps1 -Yes     -> destroy everything (add -DeleteState to remove the tfstate bucket)
#>
param([switch]$Yes, [switch]$DeleteState)
$ErrorActionPreference = "Stop"
$Tf = Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..")) "infra\terraform"
$Project = if ($env:RAG_GCP_PROJECT) { $env:RAG_GCP_PROJECT } else { (& gcloud config get-value project 2>$null) }
$Owner = if ($env:OWNER_EMAIL) { $env:OWNER_EMAIL } else { (& gcloud config get-value account 2>$null) }
$vars = @("-var=project_id=$Project", "-var=owner_email=$Owner")
& terraform "-chdir=$Tf" init -input=false -reconfigure "-backend-config=bucket=$Project-tfstate" | Out-Null
if (-not $Yes) {
    & terraform "-chdir=$Tf" plan -destroy -input=false @vars
    exit $LASTEXITCODE
}
& terraform "-chdir=$Tf" destroy -input=false -auto-approve @vars
if ($LASTEXITCODE -ne 0) { throw "terraform destroy failed" }
if ($DeleteState) { & gcloud storage rm -r "gs://$Project-tfstate" }
Write-Host "Teardown complete. The WIF pool is soft-deleted for 30 days (its ID cannot be reused until then)."
