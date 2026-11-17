# Pre-push secret scan (PowerShell). Exit 1 on any finding.
$pattern = 'BEGIN (RSA |EC )?PRIVATE KEY|AIza[0-9A-Za-z_-]{20,}|ya29\.[0-9A-Za-z_-]+|gh[pousr]_[0-9A-Za-z]{30,}|"private_key_id"'
Set-Location (Join-Path $PSScriptRoot "..")
$hits = & git grep -nE $pattern -- . ':!scripts/secret_scan.sh' ':!scripts/secret_scan.ps1'
if ($hits) { $hits; Write-Host "secret scan: FOUND potential secrets"; exit 1 }
$bad = & git ls-files | Select-String -Pattern '(^|/)\.env$|\.tfstate|-key\.json$|credentials.*\.json$'
if ($bad) { $bad; Write-Host "secret scan: sensitive files tracked"; exit 1 }
Write-Host "secret scan: clean"
