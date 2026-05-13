param(
  [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
)

# ==========================================
# CONTRACT-TEST: KLIPPER EDDY Z-HOME
# ==========================================
# Назначение:
# - фиксирует штатный Eddy Z-home через G28 Z + точный PROBE;
# - запрещает постоянный z0_adjust как замену корректировки позиции;
# - проверяет, что SET_KINEMATIC_POSITION доступен в runtime-профиле.
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

# Блок 2: Загрузка Eddy-профиля и профильного compatibility include.
$RemovedZ0Adjust = "z0" + "_adjust"
$probeEddy = Read-RepoFile "klipper/profiles/treed_v2_corexy_v1/probe_eddy_duo.cfg"
$forceMoveCompat = Read-RepoFile "klipper/profiles/treed_v2_corexy_v1/eddy_force_move_calibration.cfg"
$macrosFlow = Read-RepoFile "klipper/profiles/treed_v2_corexy_v1/macros_print_flow.cfg"
$profileReadme = Read-RepoFile "klipper/profiles/treed_v2_corexy_v1/README.md"

$eddyHomeZ = Get-GcodeMacroBlock $probeEddy "_TREED_EDDY_HOME_Z"
$reloadZOffset = Get-GcodeMacroBlock $probeEddy "_RELOAD_Z_OFFSET_FROM_PROBE"
$setZFromProbe = Get-GcodeMacroBlock $probeEddy "SET_Z_FROM_PROBE"
$captureLiveZ = Get-GcodeMacroBlock $probeEddy "_TREED_EDDY_CAPTURE_LIVE_Z_OFFSET"
$startMachinePrep = Get-GcodeMacroBlock $macrosFlow "_TREED_START_MACHINE_PREP"
$startPrint = Get-GcodeMacroBlock $macrosFlow "START_PRINT"

# Блок 3: Проверка штатного Eddy Z-home без постоянного z0_adjust.
Assert-Contains $probeEddy '(?ms)^\[force_move\]\s+enable_force_move:\s*True' "Eddy runtime profile must enable SET_KINEMATIC_POSITION"
Assert-Contains $probeEddy '(?ms)^\[temperature_probe btt_eddy\]\s+sensor_type:\s*Generic 3950\s+sensor_pin:\s*eddy:gpio26\s+min_temp:\s*10\s+max_temp:\s*100\s+horizontal_move_z:\s*2' "Eddy profile must expose linked temperature_probe for thermal drift calibration"
Assert-Contains $profileReadme 'TEMPERATURE_PROBE_CALIBRATE PROBE=btt_eddy TARGET=56 STEP=4' "Eddy README must document temperature drift calibration"
Assert-NotContains $forceMoveCompat '(?m)^\[force_move\]' "legacy Eddy calibration include must not duplicate runtime force_move section"

Assert-NotContains $probeEddy $RemovedZ0Adjust "Eddy profile must not use fixed base Z adjustment"
Assert-NotContains $probeEddy 'Eddy Z0 adjust applied' "Eddy profile must not report fixed SET_GCODE_OFFSET Z adjustment"
Assert-NotContains $captureLiveZ 'homing_origin\.z\|float\s*-' "autosave capture must not subtract a removed base z0_adjust"

Assert-ContainsBefore $eddyHomeZ '(?m)^\s*G28\.1 Z\s*$' '(?m)^\s*SET_Z_FROM_PROBE\s*$' "Eddy home must run correction immediately after G28.1 Z"
Assert-ContainsBefore $setZFromProbe '(?m)^\s*PROBE\b' '(?m)^\s*_RELOAD_Z_OFFSET_FROM_PROBE\s*$' "SET_Z_FROM_PROBE must probe before reloading Z"
Assert-Contains $reloadZOffset 'printer\.probe\.last_probe_position\.z' "Z reload must use the last PROBE result"
Assert-Contains $reloadZOffset '(?m)^\s*SET_KINEMATIC_POSITION Z=\{z - printer\.probe\.last_probe_position\.z\}\s*$' "Z reload must rewrite kinematic Z from PROBE result"

# Блок 4: START_PRINT должен строить Z0, mesh и park только на прогретом столе.
Assert-NotContains $startMachinePrep '(?m)^\s*_TREED_HOME_ALL\s*$' "START machine prep must not home before bed preheat"
Assert-ContainsBefore $startPrint '(?m)^\s*_TREED_START_PREHEAT\s*$' '(?m)^\s*_TREED_HOME_ALL\s*$' "START_PRINT must wait for bed preheat before Eddy homing"
Assert-ContainsBefore $startPrint '(?m)^\s*_TREED_HOME_ALL\s*$' '(?m)^\s*_TREED_PRINT_OFFSET_ENABLE\s*$' "START_PRINT must enable print offset only after hot Eddy homing"
Assert-ContainsBefore $startPrint '(?m)^\s*_TREED_PRINT_OFFSET_ENABLE\s*$' '(?m)^\s*_TREED_START_KAMP_PREP\s*$' "START_PRINT must prepare mesh and smart park after hot homing"
Assert-Contains $macrosFlow 'DEFAULT_MESH_METHOD = "scan" if MESH == "adaptive" else "automatic"' "Adaptive mesh default must prefer scan over rapid_scan for precision"
Assert-NotContains $macrosFlow 'mesh_method\|default\("rapid_scan"\)' "Adaptive mesh fallback must not silently return to rapid_scan"
Assert-NotContains $macrosFlow 'DEFAULT_MESH_METHOD = "rapid_scan" if MESH == "adaptive" else "automatic"' "Adaptive mesh default must not use rapid_scan in precision profile"

Write-Output "PASS: klipper eddy contracts"
