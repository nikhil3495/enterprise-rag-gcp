<#
.SYNOPSIS
  End-to-end deploy on Windows: .\scripts\deploy.ps1 [-Stage infra|image|app|seed|ingest|vars|all]
.NOTES
  Requires gcloud (user login + ADC) and terraform >= 1.9. Docker is not required (Cloud Build).
#>
param(
    [ValidateSet("infra", "image", "app", "seed", "ingest", "vars", "all")]
    [string]$Stage = "all",
    [string]$ExtraTfVars = ""
)
$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$Tf = Join-Path $Root "infra\terraform"

function Invoke-Native {
    # Simple (non-advanced) function: every token, including --flags, lands in $args untouched.
    $exe = $args[0]
    $rest = @($args | Select-Object -Skip 1)
    & $exe @rest
    if ($LASTEXITCODE -ne 0) { throw "$exe $($rest -join ' ') failed with exit code $LASTEXITCODE" }
}
function Write-Step([string]$m) { Write-Host "`n==> $m" -ForegroundColor Cyan }

$Project = $env:RAG_GCP_PROJECT
if (-not $Project) { $Project = (& gcloud config get-value project 2>$null) }
$Region = if ($env:RAG_REGION) { $env:RAG_REGION } else { "us-central1" }
$Owner = $env:OWNER_EMAIL
if (-not $Owner) { $Owner = (& gcloud config get-value account 2>$null) }
$Repo = if ($env:GITHUB_REPO) { $env:GITHUB_REPO } else { "nikhil3495/enterprise-rag-gcp" }
$Tag = if ($env:IMAGE_TAG) { $env:IMAGE_TAG } else { (& git -C $Root rev-parse --short HEAD) }
$Image = "$Region-docker.pkg.dev/$Project/rag/rag-api:$Tag"
if (-not $Project) { throw "No project: set RAG_GCP_PROJECT or run 'gcloud config set project <id>'" }
if (-not $Owner) { throw "No gcloud account: run 'gcloud auth login'" }

function Get-TfVars {
    $v = @("-var=project_id=$Project", "-var=region=$Region", "-var=owner_email=$Owner", "-var=github_repo=$Repo")
    if ($ExtraTfVars) { $v += $ExtraTfVars.Split(" ", [StringSplitOptions]::RemoveEmptyEntries) }
    return $v
}
function Get-TfOutput([string]$name) { (& terraform "-chdir=$Tf" output -raw $name).Trim() }

function Invoke-Infra {
    Write-Step "Enabling bootstrap APIs"
    Invoke-Native gcloud services enable serviceusage.googleapis.com cloudresourcemanager.googleapis.com `
        storage.googleapis.com iam.googleapis.com aiplatform.googleapis.com run.googleapis.com `
        artifactregistry.googleapis.com cloudbuild.googleapis.com --project $Project
    $bucket = "gs://$Project-tfstate"
    & gcloud storage buckets describe $bucket --project $Project *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Step "Creating state bucket $bucket"
        Invoke-Native gcloud storage buckets create $bucket --project $Project --location $Region --uniform-bucket-level-access
        Invoke-Native gcloud storage buckets update $bucket --versioning
    }
    Invoke-Native terraform "-chdir=$Tf" init -input=false -reconfigure "-backend-config=bucket=$Project-tfstate"
    Write-Step "terraform apply (Vector Search deployment can take 20-60 min)"
    $vars = Get-TfVars
    Invoke-Native terraform "-chdir=$Tf" apply -input=false -auto-approve @vars
}
function Invoke-Image {
    Write-Step "Building $Image with Cloud Build"
    Invoke-Native gcloud builds submit $Root --project $Project --region $Region --tag $Image --timeout=1800s
}
function Invoke-App {
    Write-Step "Rolling out $Image"
    Invoke-Native gcloud run services update rag-api --project $Project --region $Region --image $Image --quiet
    Invoke-Native gcloud run jobs update rag-ingest --project $Project --region $Region --image $Image --quiet
}
function Invoke-Seed {
    $bucket = Get-TfOutput "bucket"
    Write-Step "Uploading documents to gs://$bucket/docs"
    Invoke-Native gcloud storage rsync (Join-Path $Root "data\docs") "gs://$bucket/docs" --recursive --delete-unmatched-destination-objects
    Write-Step "Seeding BigQuery"
    Invoke-Native gcloud run jobs execute rag-ingest --project $Project --region $Region --args=seed-bigquery --wait
}
function Invoke-Ingest {
    Write-Step "Running ingestion job"
    Invoke-Native gcloud run jobs execute rag-ingest --project $Project --region $Region --wait
}
function Set-RepoVars {
    Write-Step "Setting GitHub repository variables (no secrets)"
    Invoke-Native gh variable set GCP_PROJECT_ID --repo $Repo --body $Project
    Invoke-Native gh variable set GCP_REGION --repo $Repo --body $Region
    Invoke-Native gh variable set WIF_PROVIDER --repo $Repo --body (Get-TfOutput "wif_provider")
    Invoke-Native gh variable set DEPLOYER_SA --repo $Repo --body (Get-TfOutput "deployer_sa")
    Invoke-Native gh variable set SERVICE_URL --repo $Repo --body (Get-TfOutput "service_url")
}

switch ($Stage) {
    "infra" { Invoke-Infra }
    "image" { Invoke-Image }
    "app" { Invoke-App }
    "seed" { Invoke-Seed }
    "ingest" { Invoke-Ingest }
    "vars" { Set-RepoVars }
    "all" {
        Invoke-Infra; Invoke-Image; Invoke-App; Invoke-Seed; Invoke-Ingest; Set-RepoVars
        & (Join-Path $PSScriptRoot "smoke_test.ps1") -Url (Get-TfOutput "service_url")
    }
}
