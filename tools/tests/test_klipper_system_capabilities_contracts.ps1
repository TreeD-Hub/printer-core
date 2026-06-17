param(
  [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
)

# ==========================================
# CONTRACT-TEST: KLIPPER SYSTEM CAPABILITIES
# ==========================================
# Назначение:
# - фиксирует capability surface для destructive/system actions TreeD Shell;
# - проверяет, что V2 profile явно разрешает power/service commands;
# - защищает автоматические проверки от вызова reboot/shutdown/restart endpoints.
# Контур:
# - read-only: проверяет только текст конфигов и verify.sh.

$ErrorActionPreference = "Stop"

# Блок 1: Общие assert-хелперы для статических контрактов.
function Read-RepoFile {
  param(
    [string]$Path
  )

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

function Assert-NotContains {
  param(
    [string]$Content,
    [string]$Pattern,
    [string]$Message
  )

  if ($Content -match $Pattern) {
    throw "FAIL: $Message"
  }
}

function Get-GcodeMacroBlock {
  param(
    [string]$Content,
    [string]$MacroName
  )

  $escapedName = [regex]::Escape($MacroName)
  $pattern = '(?ms)^\[gcode_macro ' + $escapedName + '\].*?(?=^\[gcode_macro |\z)'
  $match = [regex]::Match($Content, $pattern)
  if (-not $match.Success) {
    throw "FAIL: macro $MacroName not found"
  }

  return $match.Value
}

# Блок 2: Загрузка V2 profile и loader verify contract.
$macros = Read-RepoFile "klipper/profiles/treed_v2_corexy_v1/macros.cfg"
$macrosCore = Read-RepoFile "klipper/profiles/treed_v2_corexy_v1/macros_core.cfg"
$verify = Read-RepoFile "loader/steps/verify.sh"
$testSource = Get-Content -Encoding UTF8 -LiteralPath $PSCommandPath -Raw

Assert-Contains $macros '(?m)^\[include macros_core\.cfg\]\s*$' "macros.cfg must include macros_core.cfg with system capability macros"

$systemPower = Get-GcodeMacroBlock $macrosCore "_TREED_SYSTEM_POWER"
$serviceCommands = Get-GcodeMacroBlock $macrosCore "_TREED_SERVICE_COMMANDS"

Assert-Contains $systemPower '(?m)^variable_enabled:\s*1\s*$' "_TREED_SYSTEM_POWER must be enabled by default"
Assert-Contains $serviceCommands '(?m)^variable_enabled:\s*1\s*$' "_TREED_SERVICE_COMMANDS must be enabled by default"

# Блок 3: Non-destructive loader verify surface.
Assert-Contains $verify 'gcode_macro%20_TREED_SYSTEM_POWER' "verify.sh must query _TREED_SYSTEM_POWER through Moonraker object query"
Assert-Contains $verify 'gcode_macro%20_TREED_SERVICE_COMMANDS' "verify.sh must query _TREED_SERVICE_COMMANDS through Moonraker object query"
Assert-Contains $verify ([regex]::Escape('"enabled"[[:space:]]*:[[:space:]]*1')) "verify.sh must validate enabled=1 in queried macro state"

foreach ($endpoint in @(
  "/machine/reboot",
  "/machine/shutdown",
  "/printer/restart",
  "/printer/firmware_restart",
  "/server/restart"
)) {
  Assert-NotContains $verify ([regex]::Escape($endpoint)) "verify.sh must not call destructive endpoint $endpoint"
}

Assert-NotContains $testSource '(?i)\bInvoke-(RestMethod|WebRequest)\b|\bcurl\b|\bwget\b' "contract test must not perform HTTP calls"

Write-Host "PASS: Klipper system capabilities contract"
