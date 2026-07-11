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

function Get-ConfigSectionBlock {
  param(
    [string]$Content,
    [string]$SectionName
  )

  $escapedName = [regex]::Escape($SectionName)
  $match = [regex]::Match($Content, "(?ms)^\[$escapedName\].*?(?=^\[|\z)")
  if (-not $match.Success) {
    throw "FAIL: section $SectionName not found"
  }

  return $match.Value
}

# Блок 2: Загрузка проверяемых макросов и примеров override.
$macrosCore = Read-RepoFile "klipper/profiles/treed_v2_corexy_v1/macros_core.cfg"
$macrosHoming = Read-RepoFile "klipper/profiles/treed_v2_corexy_v1/macros_homing.cfg"
$macrosFlow = Read-RepoFile "klipper/profiles/treed_v2_corexy_v1/macros_print_flow.cfg"
$macrosPause = Read-RepoFile "klipper/profiles/treed_v2_corexy_v1/macros_pause_resume.cfg"
$macrosKamp = Read-RepoFile "klipper/profiles/treed_v2_corexy_v1/macros_kamp.cfg"
$steppers = Read-RepoFile "klipper/profiles/treed_v2_corexy_v1/steppers.cfg"
$geometry = Read-RepoFile "klipper/profiles/treed_v2_corexy_v1/geometry.cfg"
$localOverrides = Read-RepoFile "klipper/local_overrides.example.cfg"

$g28 = Get-GcodeMacroBlock $macrosHoming "G28"
$zHopBeforeXy = Get-GcodeMacroBlock $macrosCore "_TREED_Z_HOP_BEFORE_XY"
$endPrint = Get-GcodeMacroBlock $macrosFlow "END_PRINT"
$startPrint = Get-GcodeMacroBlock $macrosFlow "START_PRINT"
$startKampPrep = Get-GcodeMacroBlock $macrosFlow "_TREED_START_KAMP_PREP"
$startKampPurge = Get-GcodeMacroBlock $macrosFlow "_TREED_START_KAMP_PURGE"
$finalHeat = Get-GcodeMacroBlock $macrosFlow "_TREED_START_FINAL_HEAT"
$pauseCfg = Get-GcodeMacroBlock $macrosCore "_TREED_PAUSE_PARK_CFG"
$pauseExec = Get-GcodeMacroBlock $macrosPause "_TREED_PAUSE_EXEC"
$resumePrepWipe = Get-GcodeMacroBlock $macrosPause "_TREED_RESUME_PREP_WIPE"
$cancelPrint = Get-GcodeMacroBlock $macrosPause "CANCEL_PRINT"
$kampSettings = Get-GcodeMacroBlock $macrosKamp "_KAMP_Settings"
$smartPark = Get-GcodeMacroBlock $macrosKamp "_TREED_KAMP_SMART_PARK"
$linePurge = Get-GcodeMacroBlock $macrosKamp "_TREED_KAMP_LINE_PURGE"
$stepperX = Get-ConfigSectionBlock $steppers "stepper_x"
$stepperY = Get-ConfigSectionBlock $steppers "stepper_y"
$tmcX = Get-ConfigSectionBlock $steppers "tmc5160 stepper_x"
$tmcY = Get-ConfigSectionBlock $steppers "tmc5160 stepper_y"

# Блок 3: Проверка контрактов конечной парковки, паузы и аварийной отмены.
Assert-Contains $zHopBeforeXy '(?m)^\s*FORCE_MOVE STEPPER=stepper_z DISTANCE=\{z_hop\} VELOCITY=5 ACCEL=100\s*$' "Z-hop helper must use FORCE_MOVE before homing"
Assert-Contains $zHopBeforeXy '(?m)^\s*G1 Z\{target_z\} F1500\s*$' "Z-hop helper must use normal G1 when Z is already homed"
Assert-NotContains $zHopBeforeXy 'пропущен' "Z-hop helper must not skip the move"
Assert-NotContains $macrosCore '(?m)^\[gcode_macro _TREED_HOME_XY_SENSORLESS\]' "G28 owns X/Y homing; extra X/Y homing wrapper must not exist"

Assert-ContainsBefore $g28 '(?m)^\s*_TREED_Z_HOP_BEFORE_XY\s*$' '(?m)^\s*G28\.1 X\s*$' "G28 must run Z-hop before X homing"
Assert-ContainsBefore $g28 '(?m)^\s*_TREED_Z_HOP_BEFORE_XY\s*$' '(?m)^\s*G28\.1 Y\s*$' "G28 must run Z-hop before Y homing"
Assert-Contains $steppers '(?ms)^\[stepper_x\].*?position_endstop:\s*0\b.*?homing_positive_dir:\s*false\b' "X homing must define the left edge as raw X0"
Assert-Contains $steppers '(?ms)^\[stepper_y\].*?position_endstop:\s*245\b.*?homing_positive_dir:\s*true\b' "Y homing must keep the far edge as raw Y245"
Assert-Contains $stepperX '(?m)^\s*step_pin:\s*PG0\s*$' "CoreXY X mirror correction must route stepper_x to MOTOR1"
Assert-Contains $stepperX '(?m)^\s*dir_pin:\s*!PG1\s*$' "CoreXY X mirror correction must invert MOTOR1 direction"
Assert-Contains $stepperY '(?m)^\s*step_pin:\s*PF13\s*$' "CoreXY X mirror correction must route stepper_y to MOTOR0"
Assert-Contains $stepperY '(?m)^\s*dir_pin:\s*!PF12\s*$' "CoreXY X mirror correction must invert MOTOR0 direction"
Assert-Contains $tmcX '(?m)^\s*cs_pin:\s*PD11\s*$' "tmc5160 stepper_x must follow MOTOR1 CS after CoreXY remap"
Assert-Contains $tmcX '(?m)^\s*diag1_pin:\s*\^!PG9\s*$' "tmc5160 stepper_x must follow MOTOR1 DIAG after CoreXY remap"
Assert-Contains $tmcY '(?m)^\s*cs_pin:\s*PC4\s*$' "tmc5160 stepper_y must follow MOTOR0 CS after CoreXY remap"
Assert-Contains $tmcY '(?m)^\s*diag1_pin:\s*\^!PG6\s*$' "tmc5160 stepper_y must follow MOTOR0 DIAG after CoreXY remap"
Assert-Contains $g28 '(?m)^\s*G1 X\{xy_backoff_mm\} F\{x_retract_speed \* 60\}\s*$' "G28 X backoff must move away from X0 in the positive direction"
Assert-Contains $g28 '(?m)^\s*G1 Y-\{xy_backoff_mm\} F\{y_retract_speed \* 60\}\s*$' "G28 Y backoff must move away from Y max in the negative direction"
Assert-NotContains $g28 '(?m)^\s*SAVE_GCODE_STATE\b' "G28 must not save parser XYZ before homing"
Assert-NotContains $g28 '(?m)^\s*RESTORE_GCODE_STATE\b' "G28 must not restore stale parser XYZ after homing"
Assert-Contains $g28 'printer\.gcode_move\.absolute_coordinates' "G28 must preserve only coordinate mode explicitly"

Assert-Contains $geometry '(?m)^variable_bed_origin_x:\s*0\.0\s*$' "Eddy bed origin X must be the left edge"
Assert-Contains $geometry '(?m)^variable_print_offset_y:\s*0\.0\s*$' "Print Y offset must start at the raw Y0 movement edge"
Assert-Contains $geometry '(?m)^variable_bed_origin_y:\s*0\.0\s*$' "Eddy bed origin Y must start at the raw Y0 movement edge"
Assert-Contains $geometry '(?m)^variable_print_size_y:\s*245\.0\s*$' "Print area Y must match the full Y movement area"
Assert-Contains $geometry '(?m)^variable_bed_size_y:\s*245\.0\s*$' "Eddy bed Y size must match the full Y movement area"
Assert-Contains $geometry '(?m)^variable_bed_dir_x:\s*1\.0\s*$' "Eddy bed X direction must increase from the left edge"
Assert-Contains $geometry '(?m)^variable_bed_dir_y:\s*1\.0\s*$' "Eddy bed Y direction must increase from the front print edge"

Assert-Contains $endPrint 'printer\["gcode_macro G28"\]\.xy_backoff_mm' "END_PRINT uses the same XY backoff as homing"
Assert-Contains $endPrint 'set park_x = x_min \+ xy_backoff' "END_PRINT parks X at the homing backoff point"
Assert-Contains $endPrint 'set park_y = y_max - xy_backoff' "END_PRINT parks Y at the homing backoff point"
Assert-NotContains $endPrint 'set park_x = x_min \+ 10\.0' "END_PRINT must not park at the opposite X edge"
Assert-ContainsBefore $endPrint '(?m)^\s*_TREED_Z_HOP_BEFORE_XY\s*$' '(?m)^\s*G1 X\{park_x\} Y\{park_y\} F6000\s*$' "END_PRINT must run Z-hop before parking XY"

Assert-Contains $pauseCfg 'variable_park_x_raw: 122\.5' "PAUSE default X park is the middle of the 245 mm X travel"
Assert-Contains $pauseCfg 'variable_park_y_raw: 0\.0' "PAUSE default Y park is the front service edge"
Assert-Contains $localOverrides 'park_x_raw VALUE=122\.5' "local override example documents the PAUSE X middle default"
Assert-Contains $localOverrides 'park_y_raw VALUE=0\.0' "local override example documents the PAUSE Y0 default"
Assert-ContainsBefore $pauseExec '(?m)^\s*_TREED_Z_HOP_BEFORE_XY\s*$' '(?m)^\s*G1 X\{exec_state\.park_x\|float\} Y\{exec_state\.park_y\|float\} F6000\s*$' "PAUSE must run Z-hop before parking XY"
Assert-Contains $resumePrepWipe 'set x_right = x_max - 1\.0' "RESUME wipe must clamp the right edge inside X max"
Assert-Contains $resumePrepWipe 'set x_start = x_right - 2\.0' "RESUME wipe must move its start left when the right edge is clamped"
Assert-NotContains $resumePrepWipe 'set x_right = x_start \+ 2\.0' "RESUME wipe must not push the right edge beyond X max"

Assert-Contains $cancelPrint 'G1 Z\{target_z\} F300' "CANCEL_PRINT keeps the Z-hop"
Assert-NotContains $cancelPrint '(?m)^\s*G[01]\s+.*\bX' "CANCEL_PRINT must not issue XY moves with X"
Assert-NotContains $cancelPrint '(?m)^\s*G[01]\s+.*\bY' "CANCEL_PRINT must not issue XY moves with Y"
Assert-NotContains $cancelPrint '(?m)^\s*G28\b' "CANCEL_PRINT must not home axes"

# Блок 4: Проверка KAMP-порядка перед purge.
Assert-Contains $kampSettings 'variable_smart_park_height:\s*0\.0' "SMART_PARK default heat position must be at bed Z0"
Assert-Contains $startKampPrep '(?m)^\s*_TREED_KAMP_SMART_PARK\s*$' "START_PRINT KAMP prep must call hidden smart park helper"
Assert-Contains $startKampPurge '(?m)^\s*_TREED_KAMP_LINE_PURGE\s*$' "START_PRINT KAMP purge must call hidden line purge helper"
Assert-ContainsBefore $smartPark '(?m)^\s*G0 X\{park_x\} Y\{park_y\} F\{travel_speed\}\s*$' '(?m)^\s*G0 Z\{z_height\} F\{travel_speed\}\s*$' "SMART_PARK must move XY before lowering to heat Z"
Assert-ContainsBefore $startPrint '(?m)^\s*_TREED_START_FINAL_HEAT\s*$' '(?m)^\s*_TREED_START_KAMP_PURGE\s*$' "START_PRINT must heat nozzle before LINE_PURGE"
Assert-Contains $finalHeat '(?m)^\s*M104 S\{TARGET\}\s*$' "Final heat must set nozzle target without strict M109 wait"
Assert-Contains $finalHeat '(?m)^\s*TEMPERATURE_WAIT SENSOR=extruder MINIMUM=\{wait_min\}\s*$' "Final heat must wait only for the lower ready temperature"
Assert-NotContains $finalHeat '(?m)^\s*M109\b' "Final heat must not block on M109 thermal settling"
Assert-NotContains $finalHeat '\bMAXIMUM=' "Final heat must not wait for overshoot cooldown"
Assert-ContainsBefore $linePurge '(?m)^\s*G0 Z\{purge_height\}\s*$' '(?m)^\s*G0 X\{purge_x_center\} Y\{purge_y_origin\}\s*$' "LINE_PURGE must raise to purge height before horizontal XY move"
Assert-ContainsBefore $linePurge '(?m)^\s*G0 Z\{purge_height\}\s*$' '(?m)^\s*G0 X\{purge_x_origin\} Y\{purge_y_center\}\s*$' "LINE_PURGE must raise to purge height before vertical XY move"

Write-Output "PASS: klipper parking contracts"
