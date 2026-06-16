param(
  [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
)

# ==========================================
# CONTRACT-TEST: KLIPPER UI RUNTIME TUNE
# ==========================================
# Назначение:
# - фиксирует публичные TREED_UI_* команды live-тюнинга;
# - проверяет Moonraker state surface для TreeD Shell;
# - защищает MVP-решения по pause-at-layer и volumetric flow от неявного raw G-code.
# Контур:
# - read-only: проверяет только текст конфигов и документации Klipper.

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

# Блок 2: Загрузка UI tune контракта.
$macros = Read-RepoFile "klipper/profiles/treed_v2_corexy_v1/macros.cfg"
$uiTune = Read-RepoFile "klipper/profiles/treed_v2_corexy_v1/macros_ui_tune.cfg"
$contractDoc = Read-RepoFile "klipper/profiles/treed_v2_corexy_v1/ui-runtime-tune-contract.md"
$profileReadme = Read-RepoFile "klipper/profiles/treed_v2_corexy_v1/README.md"

Assert-Contains $macros '(?m)^\[include macros_ui_tune\.cfg\]\s*$' "macros.cfg must include UI runtime tune macros"
Assert-Contains $profileReadme 'ui-runtime-tune-contract\.md' "profile README must link the UI runtime tune contract"

$state = Get-GcodeMacroBlock $uiTune "_TREED_UI_TUNE_STATE"
$speed = Get-GcodeMacroBlock $uiTune "TREED_UI_SET_SPEED_FACTOR"
$flow = Get-GcodeMacroBlock $uiTune "TREED_UI_SET_FLOW_FACTOR"
$accel = Get-GcodeMacroBlock $uiTune "TREED_UI_SET_ACCEL"
$pressure = Get-GcodeMacroBlock $uiTune "TREED_UI_SET_PRESSURE_ADVANCE"
$retraction = Get-GcodeMacroBlock $uiTune "TREED_UI_SET_RETRACTION"
$babystep = Get-GcodeMacroBlock $uiTune "TREED_UI_BABYSTEP"
$adjustZ = Get-GcodeMacroBlock $uiTune "TREED_UI_ADJUST_Z_OFFSET"

# Блок 3: State surface и runtime-only guard.
Assert-Contains $state '(?m)^variable_contract_version:\s*"1\.0"\s*$' "UI tune state must expose contract version"
Assert-Contains $state '(?m)^variable_applied_babystep:\s*0\.0\s*$' "UI tune state must expose applied babystep"
Assert-Contains $uiTune 'print_state not in \["printing", "paused"\]' "UI tune commands must be guarded to printing/paused"

# Блок 4: Публичные команды, параметры и raw Klipper mapping.
Assert-Contains $speed 'params\.PERCENT is not defined' "speed factor command must require PERCENT"
Assert-Contains $speed 'MIN_PERCENT = 10\.0' "speed factor command must define min percent"
Assert-Contains $speed 'MAX_PERCENT = 300\.0' "speed factor command must define max percent"
Assert-Contains $speed '(?m)^\s*M220 S\{percent\}\s*$' "speed factor command must map to M220"

Assert-Contains $flow 'params\.PERCENT is not defined' "flow factor command must require PERCENT"
Assert-Contains $flow 'MIN_PERCENT = 50\.0' "flow factor command must define min percent"
Assert-Contains $flow 'MAX_PERCENT = 150\.0' "flow factor command must define max percent"
Assert-Contains $flow '(?m)^\s*M221 S\{percent\}\s*$' "flow factor command must map to M221"

Assert-Contains $accel 'params\.ACCEL is not defined' "accel command must require ACCEL"
Assert-Contains $accel 'MIN_ACCEL = 500\.0' "accel command must define min accel"
Assert-Contains $accel 'printer\.configfile\.settings\.printer\.max_accel' "accel command must use profile max_accel as max"
Assert-Contains $accel '(?m)^\s*SET_VELOCITY_LIMIT ACCEL=\{accel\}\s*$' "accel command must map to SET_VELOCITY_LIMIT"

Assert-Contains $pressure 'params\.ADVANCE is not defined' "pressure advance command must require ADVANCE"
Assert-Contains $pressure 'MAX_ADVANCE = 0\.20' "pressure advance command must define max advance"
Assert-Contains $pressure '(?m)^\s*SET_PRESSURE_ADVANCE ADVANCE=\{advance\}\s*$' "pressure advance command must map to SET_PRESSURE_ADVANCE"

Assert-Contains $retraction 'params\.RETRACT_LENGTH is not defined' "retraction command must require RETRACT_LENGTH"
Assert-Contains $retraction 'MAX_RETRACT = 5\.0' "retraction command must define max retract length"
Assert-Contains $retraction 'printer\.firmware_retraction is not defined' "retraction command must fail clearly if firmware_retraction is missing"
Assert-Contains $retraction '(?m)^\s*SET_RETRACTION RETRACT_LENGTH=\{retract_length\}\s*$' "retraction command must map to SET_RETRACTION"

Assert-Contains $babystep 'params\.DELTA is not defined' "babystep command must require DELTA"
Assert-Contains $babystep 'MIN_DELTA = -0\.05' "babystep command must define min delta"
Assert-Contains $babystep 'MAX_DELTA = 0\.05' "babystep command must define max delta"
Assert-Contains $babystep '"z" not in printer\.toolhead\.homed_axes' "babystep command must require homed Z"
Assert-Contains $babystep '(?m)^\s*SET_GCODE_OFFSET Z_ADJUST=\{delta\} MOVE=1 MOVE_SPEED=5\s*$' "babystep command must map to SET_GCODE_OFFSET Z_ADJUST"
Assert-Contains $babystep 'VARIABLE=applied_babystep VALUE=\{next_applied\}' "babystep command must update applied_babystep state"
Assert-Contains $adjustZ '(?m)^\s*TREED_UI_BABYSTEP DELTA=\{params\.DELTA\}\s*$' "adjust z-offset command must be an alias to babystep"

# Блок 5: Документация команды, state surface и MVP-исключений.
foreach ($command in @(
  "TREED_UI_SET_SPEED_FACTOR",
  "TREED_UI_SET_FLOW_FACTOR",
  "TREED_UI_SET_ACCEL",
  "TREED_UI_SET_PRESSURE_ADVANCE",
  "TREED_UI_SET_RETRACTION",
  "TREED_UI_BABYSTEP",
  "TREED_UI_ADJUST_Z_OFFSET"
)) {
  Assert-Contains $contractDoc $command "contract doc must mention $command"
}

foreach ($object in @(
  "gcode_move",
  "toolhead",
  "extruder",
  "heater_bed",
  "firmware_retraction",
  "gcode_macro _TREED_UI_TUNE_STATE"
)) {
  Assert-Contains $contractDoc $object "contract doc must expose $object state"
}

Assert-Contains $contractDoc 'printer\.gcode\.script' "contract doc must include Moonraker printer.gcode.script"
Assert-Contains $contractDoc 'pause at layer' "contract doc must keep pause at layer out of MVP"
Assert-Contains $contractDoc 'volumetric flow' "contract doc must keep volumetric flow out of live MVP"

Write-Host "PASS: Klipper UI runtime tune contract"
