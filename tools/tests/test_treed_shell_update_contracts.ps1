param(
  [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
)

# ==========================================
# CONTRACT-TEST: TREED SHELL UPDATE
# ==========================================
# Назначение:
# - фиксирует атомарную публикацию UI bundle и rollback;
# - требует readiness-проверку HTTP и systemd после runtime update.
# Контур:
# - read-only: проверяет только shell-контракты loader/runtime updater.

$ErrorActionPreference = "Stop"

function Read-RepoFile {
  param([string]$Path)
  return Get-Content -Encoding UTF8 -LiteralPath (Join-Path $RepoRoot $Path) -Raw
}

function Assert-Contains {
  param(
    [string]$Content,
    [string]$Pattern,
    [string]$Message
  )
  if ($Content -notmatch $Pattern) {
    throw "FAIL: $Message"
  }
}

$installer = Read-RepoFile "loader/steps/treed-shell-install.sh"
$updater = Read-RepoFile "runtime-scripts/treed-update/treed-update-apply"

Assert-Contains $installer 'SHELL_PREVIOUS_WEB_DIR="\$\{SHELL_RUNTIME_DIR\}/ui\.previous"' "loader must retain the previous UI directory"
Assert-Contains $installer 'mv "\$\{SHELL_WEB_DIR\}" "\$\{SHELL_PREVIOUS_WEB_DIR\}"' "loader must move the current UI aside before publish"
Assert-Contains $installer 'rollback_ui_archive' "loader must expose rollback after readiness failure"
Assert-Contains $installer 'wait_service_ready' "loader must wait for local UI readiness"
Assert-Contains $installer 'http://127\.0\.0\.1:\$\{SHELL_HTTP_PORT\}/index\.html' "loader must validate the local UI over HTTP"
Assert-Contains $updater 'wait_shell_ready' "runtime updater must wait for UI readiness"
Assert-Contains $updater 'systemctl is-active --quiet "\$\{SHELL_SERVICE\}"' "runtime updater must validate the UI service"
Assert-Contains $updater 'http://127\.0\.0\.1:\$\{SHELL_HTTP_PORT\}/index\.html' "runtime updater must validate the local UI over HTTP"
Assert-Contains $updater 'consecutive_ready.*-ge 3' "runtime updater must require a stable readiness window"

Write-Output "PASS: TreeD Shell update contracts"
