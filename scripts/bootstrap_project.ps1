<#
.SYNOPSIS
  Create (or reuse) the GCP project, link the first OPEN billing account, enable APIs, set gcloud config.
  .\scripts\bootstrap_project.ps1                 # creates enterprise-rag-<6 random chars>
  .\scripts\bootstrap_project.ps1 -ProjectId x    # reuse an existing project
#>
param([string]$ProjectId = "")
$ErrorActionPreference = "Stop"
function Invoke-Native {
    # Simple (non-advanced) function: every token, including --flags, lands in $args untouched.
    $exe = $args[0]
    $rest = @($args | Select-Object -Skip 1)
    & $exe @rest
    if ($LASTEXITCODE -ne 0) { throw "$exe $($rest -join ' ') failed with exit code $LASTEXITCODE" }
}

if (-not $ProjectId) {
    $suffix = -join ((97..122) + (48..57) | Get-Random -Count 6 | ForEach-Object { [char]$_ })
    $ProjectId = "enterprise-rag-$suffix"
    Write-Host "Creating project $ProjectId"
    Invoke-Native gcloud projects create $ProjectId --name "Enterprise RAG"
}
$billing = (& gcloud billing accounts list --filter="open=true" --format="value(name)" --limit=1)
if (-not $billing) {
    throw "No OPEN billing account visible to this user. Link one manually: gcloud billing projects link $ProjectId --billing-account=XXXX"
}
$billing = $billing.Trim().Replace("billingAccounts/", "")
Invoke-Native gcloud billing projects link $ProjectId --billing-account=$billing
Invoke-Native gcloud billing projects describe $ProjectId
Invoke-Native gcloud config set project $ProjectId
Invoke-Native gcloud auth application-default set-quota-project $ProjectId
Invoke-Native gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com `
    aiplatform.googleapis.com bigquery.googleapis.com firestore.googleapis.com storage.googleapis.com iam.googleapis.com `
    iamcredentials.googleapis.com sts.googleapis.com discoveryengine.googleapis.com logging.googleapis.com `
    monitoring.googleapis.com cloudresourcemanager.googleapis.com serviceusage.googleapis.com billingbudgets.googleapis.com `
    --project $ProjectId
Write-Host "Project $ProjectId ready (billing account $billing). Next: .\scripts\deploy.ps1"
