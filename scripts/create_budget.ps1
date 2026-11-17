<#
.SYNOPSIS
  Create a monthly budget alert (default INR 2000 ~= USD 25) for the current project.
  Needs billing.budgets.create on the billing account (Billing Account Administrator / Costs Manager).
#>
param([int]$Amount = 2000, [string]$Currency = "INR")
$ErrorActionPreference = "Stop"
$Project = (& gcloud config get-value project 2>$null)
$billing = (& gcloud billing projects describe $Project --format="value(billingAccountName)").Replace("billingAccounts/", "")
& gcloud billing budgets create --billing-account=$billing --display-name="enterprise-rag monthly budget" `
    --budget-amount="$Amount$Currency" --filter-projects="projects/$Project" `
    --threshold-rule=percent=0.5 --threshold-rule=percent=0.9 --threshold-rule=percent=1.0
if ($LASTEXITCODE -ne 0) { throw "budget creation failed (missing billing permissions?)" }
