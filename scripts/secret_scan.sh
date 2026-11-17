#!/usr/bin/env bash
# Pre-push secret scan over tracked + staged files. Exit 1 on any finding.
set -uo pipefail
cd "$(dirname "$0")/.."
pattern='BEGIN (RSA |EC )?PRIVATE KEY|AIza[0-9A-Za-z_-]{20,}|ya29\.[0-9A-Za-z_-]+|gh[pousr]_[0-9A-Za-z]{30,}|"private_key_id"'
if git grep -nE "$pattern" -- . ':!scripts/secret_scan.sh' ':!scripts/secret_scan.ps1'; then
  echo "secret scan: FOUND potential secrets"; exit 1
fi
if git grep --cached -nE "$pattern" -- . ':!scripts/secret_scan.sh' ':!scripts/secret_scan.ps1'; then
  echo "secret scan: FOUND potential secrets in index"; exit 1
fi
bad=$(git ls-files | grep -iE '(^|/)\.env$|\.tfstate|-key\.json$|credentials.*\.json$' || true)
[[ -z "$bad" ]] || { echo "secret scan: sensitive files tracked: $bad"; exit 1; }
echo "secret scan: clean"
