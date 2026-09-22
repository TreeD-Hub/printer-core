$ErrorActionPreference = "Stop"

# ==========================================
# CONTRACT TEST: CAN TX QUEUE DEFAULT
# ==========================================

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$bash = "C:\Program Files\Git\bin\bash.exe"
if (-not (Test-Path -LiteralPath $bash)) {
  throw "Git Bash is required: $bash"
}

function Read-RepoFile([string]$Path) {
  return Get-Content -LiteralPath (Join-Path $repoRoot $Path) -Raw -Encoding UTF8
}

$sources = @(
  "loader\bootstrap.sh",
  "loader\steps\can-setup.sh",
  "loader\steps\check-env.sh",
  "loader\steps\verify.sh",
  "loader\README.md",
  "loader\steps\README.md",
  "docs\README.md",
  "docs\config-ownership.md"
)
foreach ($source in $sources) {
  if ((Read-RepoFile $source) -match 'TREED_CAN_TXQUEUE[^\r\n]*1024') {
    throw "FAIL: legacy CAN txqueue fallback remains in $source"
  }
}

if ((Read-RepoFile "loader\steps\can-setup.sh") -notmatch 'TREED_CAN_TXQUEUE="\$\{TREED_CAN_TXQUEUE:-128\}"') {
  throw "FAIL: can-setup production/runtime defaults must be 128"
}
if ((Read-RepoFile "loader\steps\verify.sh") -notmatch 'CAN_ENV_TXQUEUE:-128') {
  throw "FAIL: verify fallback must expect txqueuelen 128"
}
if ((Read-RepoFile "loader\steps\can-setup.sh") -notmatch 'TREED_CAN_BITRATE="\$\{TREED_CAN_BITRATE:-1000000\}"') {
  throw "FAIL: CAN bitrate default changed"
}
if ((Read-RepoFile "loader\steps\can-setup.sh") -notmatch 'TREED_CAN_RESTART_MS="\$\{TREED_CAN_RESTART_MS:-100\}"') {
  throw "FAIL: CAN restart-ms default changed"
}

$runtimeTest = (Join-Path $repoRoot "tools\tests\test_can_txqueue_runtime.sh").Replace("\", "/").Replace("C:", "/c")
& $bash $runtimeTest
if ($LASTEXITCODE -ne 0) {
  throw "CAN txqueue runtime test failed"
}

Write-Output "PASS: CAN txqueue contracts"
