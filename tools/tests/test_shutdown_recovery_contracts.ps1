$ErrorActionPreference = "Stop"

# ==========================================
# CONTRACT TEST: SHUTDOWN RECOVERY
# ==========================================
# Назначение:
# - Поведенчески проверяет MCU firmware status и recovery на fake Klippy.
# - Фиксирует пассивность default-диагностики и отсутствие loader auto-restart.
# Контур:
# - runnable локально, без принтера, сети, движения и нагрева.

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$pythonCommand = @(
  Get-Command python, python3 -All -ErrorAction SilentlyContinue |
    Where-Object { $_.Source -notlike "*\Microsoft\WindowsApps\*" }
)[0]
if (-not $pythonCommand) {
  throw "python is required"
}

& $pythonCommand.Source (Join-Path $PSScriptRoot "test_shutdown_recovery.py")
if ($LASTEXITCODE -ne 0) {
  throw "shutdown recovery behavior test failed"
}

$collector = Get-Content -LiteralPath (Join-Path $repoRoot "tools\collect_eddy_diagnostic.sh") -Raw -Encoding UTF8
$antiShutdown = Get-Content -LiteralPath (Join-Path $repoRoot "loader\steps\klipper-anti-shutdown.sh") -Raw -Encoding UTF8
$recovery = Get-Content -LiteralPath (Join-Path $repoRoot "moonraker\components\treed_recovery.py") -Raw -Encoding UTF8
$canSetup = Get-Content -LiteralPath (Join-Path $repoRoot "loader\steps\can-setup.sh") -Raw -Encoding UTF8

if ($collector -notmatch 'TREED_DIAGNOSTIC_MODE:-passive') {
  throw "FAIL: diagnostics must default to passive mode"
}
if ($collector -match 'canbus_query\.py') {
  throw "FAIL: diagnostics must not use canbus_query.py"
}
if ($collector -notmatch 'firmware_restart_sent=0' -or $collector -notmatch 'can_reconfigured=0') {
  throw "FAIL: diagnostic manifest must record side-effect boundaries"
}
if ($collector -notmatch 'session_boundary=' -or $collector -notmatch 'reason=counter_reset') {
  throw "FAIL: diagnostic deltas must reject changed sessions and reset counters"
}
$passiveExit = $collector.IndexOf('if [ "${MODE}" = "passive" ]')
$gcodeSubmit = $collector.IndexOf('GCODE=$''RESPOND')
if ($passiveExit -lt 0 -or $gcodeSubmit -lt 0 -or $passiveExit -gt $gcodeSubmit) {
  throw "FAIL: passive mode must exit before any G-code is assembled"
}
if ($antiShutdown -match 'gcode/script|systemctl restart') {
  throw "FAIL: loader anti-shutdown must not perform automatic recovery"
}
if ($recovery -notmatch 'do_restart\("FIRMWARE_RESTART"\)') {
  throw "FAIL: explicit recovery must use the standard firmware restart"
}
if ($canSetup -notmatch 'KLIPPER_STATE.*systemctl show' -or $canSetup -notmatch 'refuse reconfiguration while klipper\.service') {
  throw "FAIL: CAN setup must retain the active-Klipper reconfiguration guard"
}
if ($canSetup -notmatch 'StartLimitBurst=3' -or $canSetup -notmatch 'StartLimitIntervalSec=60') {
  throw "FAIL: CAN setup retries must be bounded by systemd"
}
if ($canSetup -match '(?m)^(Requires|RequiredBy)=.*(network|ssh)') {
  throw "FAIL: CAN failure must not become a network or SSH dependency"
}

Write-Output "PASS: shutdown recovery contracts"
