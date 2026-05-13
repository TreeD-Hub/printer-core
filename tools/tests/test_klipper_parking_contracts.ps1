param(
  [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
)

# ==========================================
# CONTRACT-TEST: KLIPPER PARKING
# ==========================================
# Назначение:
# - фиксирует безопасные точки парковки END_PRINT/PAUSE;
# - защищает CANCEL_PRINT от XY-движений при аварийной отмене.
# Контур:
# - read-only: проверяет только текст конфигов Klipper.

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

function Assert-ContainsBefore {
  param(
    [string]$Content,
    [string]$FirstPattern,
    [string]$SecondPattern,
    [string]$Message
  )

  $first = [regex]::Match($Content, $FirstPattern)
  if (-not $first.Success) {
    throw "FAIL: $Message (first pattern not found)"
  }

  $second = [regex]::Match($Content, $SecondPattern)
  if (-not $second.Success) {
    throw "FAIL: $Message (second pattern not found)"
  }

  if ($first.Index -gt $second.Index) {
    throw "FAIL: $Message"
  }
}

function Get-GcodeMacroBlock {
  param(
    [string]$Content,
    [string]$MacroName
  )

  $escapedName = [regex]::Escape($MacroName)
  $match = [regex]::Match($Content, "(?ms)^\[gcode_macro $escapedName\].*?(?=^\[gcode_macro |\z)")
  if (-not $match.Success) {
    throw "FAIL: macro $MacroName not found"
  }

  return $match.Value
}

# Блок 2: Загрузка проверяемых макросов и примеров override.
$macrosCore = Read-RepoFile "klipper/profiles/treed_v2_corexy_v1/macros_core.cfg"
$macrosHoming = Read-RepoFile "klipper/profiles/treed_v2_corexy_v1/macros_homing.cfg"
$macrosFlow = Read-RepoFile "klipper/profiles/treed_v2_corexy_v1/macros_print_flow.cfg"
$macrosPause = Read-RepoFile "klipper/profiles/treed_v2_corexy_v1/macros_pause_resume.cfg"
$localOverrides = Read-RepoFile "klipper/local_overrides.example.cfg"

$g28 = Get-GcodeMacroBlock $macrosHoming "G28"
$zHopBeforeXy = Get-GcodeMacroBlock $macrosCore "_TREED_Z_HOP_BEFORE_XY"
$endPrint = Get-GcodeMacroBlock $macrosFlow "END_PRINT"
$pauseCfg = Get-GcodeMacroBlock $macrosCore "_TREED_PAUSE_PARK_CFG"
$pauseExec = Get-GcodeMacroBlock $macrosPause "_TREED_PAUSE_EXEC"
$cancelPrint = Get-GcodeMacroBlock $macrosPause "CANCEL_PRINT"

# Блок 3: Проверка контрактов конечной парковки, паузы и аварийной отмены.
Assert-Contains $zHopBeforeXy '(?m)^\s*FORCE_MOVE STEPPER=stepper_z DISTANCE=\{z_hop\} VELOCITY=5 ACCEL=100\s*$' "Z-hop helper must use FORCE_MOVE before homing"
Assert-Contains $zHopBeforeXy '(?m)^\s*G1 Z\{target_z\} F1500\s*$' "Z-hop helper must use normal G1 when Z is already homed"
Assert-NotContains $zHopBeforeXy 'пропущен' "Z-hop helper must not skip the move"
Assert-NotContains $macrosCore '(?m)^\[gcode_macro _TREED_HOME_XY_SENSORLESS\]' "G28 owns X/Y homing; extra X/Y homing wrapper must not exist"

Assert-ContainsBefore $g28 '(?m)^\s*_TREED_Z_HOP_BEFORE_XY\s*$' '(?m)^\s*G28\.1 X\s*$' "G28 must run Z-hop before X homing"
Assert-ContainsBefore $g28 '(?m)^\s*_TREED_Z_HOP_BEFORE_XY\s*$' '(?m)^\s*G28\.1 Y\s*$' "G28 must run Z-hop before Y homing"

Assert-Contains $endPrint 'printer\["gcode_macro G28"\]\.xy_backoff_mm' "END_PRINT uses the same XY backoff as homing"
Assert-Contains $endPrint 'set park_x = x_max - xy_backoff' "END_PRINT parks X at the homing backoff point"
Assert-Contains $endPrint 'set park_y = y_max - xy_backoff' "END_PRINT parks Y at the homing backoff point"
Assert-NotContains $endPrint 'set park_x = x_min \+ 10\.0' "END_PRINT must not park at the opposite X edge"
Assert-ContainsBefore $endPrint '(?m)^\s*_TREED_Z_HOP_BEFORE_XY\s*$' '(?m)^\s*G1 X\{park_x\} Y\{park_y\} F6000\s*$' "END_PRINT must run Z-hop before parking XY"

Assert-Contains $pauseCfg 'variable_park_x_raw: 122\.5' "PAUSE default X park is the middle of the 245 mm X travel"
Assert-Contains $pauseCfg 'variable_park_y_raw: 0\.0' "PAUSE default Y park is the front service edge"
Assert-Contains $localOverrides 'park_x_raw VALUE=122\.5' "local override example documents the PAUSE X middle default"
Assert-Contains $localOverrides 'park_y_raw VALUE=0\.0' "local override example documents the PAUSE Y0 default"
Assert-ContainsBefore $pauseExec '(?m)^\s*_TREED_Z_HOP_BEFORE_XY\s*$' '(?m)^\s*G1 X\{exec_state\.park_x\|float\} Y\{exec_state\.park_y\|float\} F6000\s*$' "PAUSE must run Z-hop before parking XY"

Assert-Contains $cancelPrint 'G1 Z\{target_z\} F300' "CANCEL_PRINT keeps the Z-hop"
Assert-NotContains $cancelPrint '(?m)^\s*G[01]\s+.*\bX' "CANCEL_PRINT must not issue XY moves with X"
Assert-NotContains $cancelPrint '(?m)^\s*G[01]\s+.*\bY' "CANCEL_PRINT must not issue XY moves with Y"
Assert-NotContains $cancelPrint '(?m)^\s*G28\b' "CANCEL_PRINT must not home axes"

Write-Output "PASS: klipper parking contracts"
