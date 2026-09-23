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
  $match = [regex]::Match($Content, "(?ms)^\[gcode_macro $escapedName\].*?(?=^\[|\z)")
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
$homeXy = Get-GcodeMacroBlock $macrosHoming "_TREED_HOME_XY_RUN"
$sensorlessPrepare = Get-GcodeMacroBlock $macrosHoming "_TREED_SENSORLESS_PREPARE"
$zHopBeforeXy = Get-GcodeMacroBlock $macrosCore "_TREED_Z_HOP_BEFORE_XY"
$endPrint = Get-GcodeMacroBlock $macrosFlow "END_PRINT"
$startPrint = Get-GcodeMacroBlock $macrosFlow "START_PRINT"
$startAfterPreheat = Get-GcodeMacroBlock $macrosFlow "_TREED_START_AFTER_PREHEAT"
$heatPoll = Get-ConfigSectionBlock $macrosFlow "delayed_gcode _TREED_START_HEAT_POLL"
$startSmartPark = Get-GcodeMacroBlock $macrosFlow "_TREED_START_SMART_PARK"
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
Assert-Contains $zHopBeforeXy 'if z_hop <= 0\.0' "Z-hop must reject nonpositive clearance"
Assert-Contains $zHopBeforeXy '(?m)^\s*G1 Z\{target_z - current_z\} F1500\s*$' "Z-hop must use a positive relative move independent of G-code offsets"
Assert-ContainsBefore $zHopBeforeXy 'if target_z > current_z' '(?m)^\s*G1 Z\{target_z - current_z\} F1500\s*$' "Z-hop must only move upward after the Z max clamp"
Assert-ContainsBefore $zHopBeforeXy '(?m)^\s*G91\s*$' '(?m)^\s*G1 Z\{target_z - current_z\} F1500\s*$' "Z-hop must switch to relative coordinates before moving"
Assert-ContainsBefore $zHopBeforeXy '(?m)^\s*FORCE_MOVE STEPPER=stepper_z DISTANCE=\{z_hop\} VELOCITY=5 ACCEL=100\s*$' '(?m)^\s*SET_KINEMATIC_POSITION Z=\{z_hop\} SET_HOMED=Z\s*$' "Unknown Z must move away before marking only Z temporarily homed"
Assert-ContainsBefore $zHopBeforeXy 'if params\.MARK_Z\|default\(0\)\|int == 1' '(?m)^\s*SET_KINEMATIC_POSITION Z=\{z_hop\} SET_HOMED=Z\s*$' "Only full G28 may mark Z temporarily homed"
Assert-NotContains $macrosCore '(?m)^\[gcode_macro _TREED_HOME_XY_SENSORLESS\]' "G28 owns X/Y homing; extra X/Y homing wrapper must not exist"

Assert-ContainsBefore $g28 '(?m)^\s*_TREED_Z_HOP_BEFORE_XY MARK_Z=\{1 if home_z else 0\}\s*$' 'TREED_MOTION_GUARD ACTION=HOME_XY' "G28 must run Z-hop before guarded X/Y homing"
Assert-ContainsBefore $g28 '(?m)^\s*BED_MESH_CLEAR\s*$' '(?m)^\s*_TREED_Z_HOP_BEFORE_XY MARK_Z=' "G28 must clear stale mesh before homing travel"
Assert-ContainsBefore $g28 '(?m)^\s*_TREED_PRINT_OFFSET_DISABLE\s*$' '(?m)^\s*_TREED_Z_HOP_BEFORE_XY MARK_Z=' "G28 must use raw XY coordinates before homing travel"
Assert-ContainsBefore $g28 '(?m)^\s*_TREED_UI_RESET_Z_OFFSET\s*$' '(?m)^\s*_TREED_Z_HOP_BEFORE_XY MARK_Z=' "G28 must clear stale Z offset before homing travel"
Assert-Contains $macrosHoming '(?m)^variable_stallguard_pause_ms: 2000\s*$' "Sensorless pause must default to at least 2 seconds"
Assert-Contains $sensorlessPrepare 'pause_ms < 2000' "Sensorless pause must not be shortened below 2 seconds"
Assert-ContainsBefore $sensorlessPrepare '(?m)^\s*M400\s*$' '(?m)^\s*G4 P\{pause_ms\}\s*$' "Sensorless preparation must drain moves before the pause"
Assert-Contains $homeXy '(?m)^[ \t]*_TREED_SENSORLESS_PREPARE[ \t]*\r?\n[ \t]*G28\.1 X[ \t]*$' "G28 must prepare immediately before X homing"
Assert-Contains $homeXy '(?m)^[ \t]*_TREED_SENSORLESS_PREPARE[ \t]*\r?\n[ \t]*G28\.1 Y[ \t]*$' "G28 must prepare immediately before Y homing"
Assert-NotContains $g28 '(?m)^\s*SET_TMC_(CURRENT|FIELD)\b' "G28 must not change driver settings"
Assert-NotContains $sensorlessPrepare '(?m)^\s*SET_TMC_(CURRENT|FIELD)\b' "Sensorless preparation must not change driver settings"
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
Assert-Contains $homeXy '(?m)^\s*G1 X\{xy_backoff_mm\} F\{x_retract_speed \* 60\}\s*$' "G28 X backoff must move away from X0 in the positive direction"
Assert-Contains $homeXy '(?m)^\s*G1 Y-\{xy_backoff_mm\} F\{y_retract_speed \* 60\}\s*$' "G28 Y backoff must move away from Y max in the negative direction"
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
Assert-NotContains $endPrint '(?m)^\s*G1 Z\{lift_z\}' "END_PRINT must not descend after the Z-hop clamp"
Assert-Contains $endPrint 'END_PRINT: координаты неизвестны или вне пределов; парковка пропущена' "END_PRINT must skip parking when axes are unsafe"
Assert-ContainsBefore $endPrint '(?m)^\s*TURN_OFF_HEATERS\s*$' '(?m)^\s*G1 E-2 F1800\s*$' "END_PRINT must disable heaters before any optional motion"
Assert-ContainsBefore $endPrint '(?m)^\s*TURN_OFF_HEATERS\s*$' '(?m)^\s*_TREED_Z_HOP_BEFORE_XY\s*$' "END_PRINT must disable heaters before parking"
Assert-Contains $endPrint '"x" in toolhead\.homed_axes and "y" in toolhead\.homed_axes and "z" in toolhead\.homed_axes' "END_PRINT must require homed XYZ before parking"

Assert-Contains $pauseCfg 'variable_park_x_raw: 122\.5' "PAUSE default X park is the middle of the 245 mm X travel"
Assert-Contains $pauseCfg 'variable_park_y_raw: 0\.0' "PAUSE default Y park is the front service edge"
Assert-Contains $localOverrides 'park_x_raw VALUE=122\.5' "local override example documents the PAUSE X middle default"
Assert-Contains $localOverrides 'park_y_raw VALUE=0\.0' "local override example documents the PAUSE Y0 default"
Assert-ContainsBefore $pauseExec '(?m)^\s*_TREED_Z_HOP_BEFORE_XY\s*$' '(?m)^\s*G1 X\{exec_state\.park_x\|float\} Y\{exec_state\.park_y\|float\} F6000\s*$' "PAUSE must run Z-hop before parking XY"
Assert-ContainsBefore $pauseExec 'if "z" not in toolhead\.homed_axes' '(?m)^\s*G1 X\{x_safe\} Y\{y_safe\} F6000\s*$' "PAUSE must reject unknown Z before its initial XY correction"
Assert-NotContains $pauseExec '(?m)^\s*G1 Z\{exec_state\.lift_z\|float\}' "PAUSE must not descend after the Z-hop clamp"
Assert-Contains $resumePrepWipe 'set x_right = x_max - 1\.0' "RESUME wipe must clamp the right edge inside X max"
Assert-Contains $resumePrepWipe 'set x_start = x_right - 2\.0' "RESUME wipe must move its start left when the right edge is clamped"
Assert-NotContains $resumePrepWipe 'set x_right = x_start \+ 2\.0' "RESUME wipe must not push the right edge beyond X max"

Assert-Contains $cancelPrint 'G1 Z\{target_z - current_z\} F300' "CANCEL_PRINT keeps a relative Z lift"
Assert-ContainsBefore $cancelPrint 'if target_z > current_z' 'G1 Z\{target_z - current_z\} F300' "CANCEL_PRINT must never turn its Z-hop into a downward move"
Assert-ContainsBefore $cancelPrint '(?m)^\s*TURN_OFF_HEATERS\s*$' '(?m)^\s*CANCEL_PRINT_BASE\s*$' "CANCEL_PRINT must disable heaters before base cancellation"
Assert-ContainsBefore $cancelPrint '(?m)^\s*UPDATE_DELAYED_GCODE ID=_TREED_START_HEAT_POLL DURATION=0\s*$' '(?m)^\s*CANCEL_PRINT_BASE\s*$' "CANCEL_PRINT must stop preheat polling before base cancellation"
Assert-ContainsBefore $cancelPrint '(?m)^\s*CANCEL_PRINT_BASE\s*$' 'G1 Z\{target_z - current_z\} F300' "CANCEL_PRINT must complete base cancellation before optional motion"
Assert-Contains $cancelPrint '"z" in toolhead\.homed_axes and current_z >= z_min and current_z <= z_max' "CANCEL_PRINT must skip the lift when Z is not trustworthy"
Assert-NotContains $cancelPrint '(?m)^\s*G[01]\s+.*\bX' "CANCEL_PRINT must not issue XY moves with X"
Assert-NotContains $cancelPrint '(?m)^\s*G[01]\s+.*\bY' "CANCEL_PRINT must not issue XY moves with Y"
Assert-NotContains $cancelPrint '(?m)^\s*G28\b' "CANCEL_PRINT must not home axes"

# Блок 4: Проверка KAMP-порядка перед purge.
Assert-Contains $kampSettings 'variable_smart_park_height:\s*10\.0' "SMART_PARK must wait above the bed for final heating"
Assert-Contains $startSmartPark '(?m)^\s*_TREED_KAMP_SMART_PARK\s*$' "START_PRINT должен вызвать скрытый smart park helper после mesh"
Assert-Contains $startKampPurge '(?m)^\s*_TREED_KAMP_LINE_PURGE\s*$' "START_PRINT KAMP purge must call hidden line purge helper"
Assert-ContainsBefore $smartPark '(?m)^\s*G0 X\{park_x\} Y\{park_y\} F\{travel_speed\}\s*$' '(?m)^\s*G0 Z\{z_height\} F\{travel_speed\}\s*$' "SMART_PARK must move XY before lowering to heat Z"
Assert-NotContains $smartPark '(?m)^\s*G28\s*$' "SMART_PARK must not rehome after mesh selection"
Assert-NotContains $linePurge '(?m)^\s*G28\s*$' "LINE_PURGE must not rehome after mesh selection"
Assert-Contains $smartPark 'print_offset_enabled\|int != 1' "SMART_PARK must require print coordinates before moving"
Assert-Contains $linePurge 'print_offset_enabled\|int != 1' "LINE_PURGE must require print coordinates before moving"
Assert-ContainsBefore $startAfterPreheat '(?m)^\s*_TREED_START_SMART_PARK\s*$' '(?m)^\s*_TREED_START_FINAL_HEAT\s*$' "Final heat must start after smart park"
Assert-ContainsBefore $heatPoll 'hotend_now >= wait_min' '(?m)^\s*_TREED_START_KAMP_PURGE\s*$' "LINE_PURGE must wait for nozzle temperature"
Assert-ContainsBefore $heatPoll '(?m)^\s*SAVE_GCODE_STATE NAME=PAUSE_STATE\s*$' '(?m)^\s*RESUME_BASE\s*$' "Print must resume from its current state"
Assert-Contains $startPrint '(?m)^\s*PAUSE_BASE\s*$' "START_PRINT must pause virtual SD while heating"
Assert-NotContains $macrosFlow '(?m)^\s*(?:M109|M190|TEMPERATURE_WAIT)\b' "START_PRINT must not block cancellation on heater waits"
Assert-Contains $finalHeat '(?m)^\s*M104 S\{TARGET\}\s*$' "Final heat must set nozzle target without strict M109 wait"
Assert-NotContains $finalHeat '(?m)^\s*M109\b' "Final heat must not block on M109 thermal settling"
Assert-NotContains $finalHeat '\bMAXIMUM=' "Final heat must not wait for overshoot cooldown"
Assert-ContainsBefore $linePurge '(?m)^\s*G0 Z\{purge_height\}\s*$' '(?m)^\s*G0 X\{purge_x_center\} Y\{purge_y_origin\}\s*$' "LINE_PURGE must raise to purge height before horizontal XY move"
Assert-ContainsBefore $linePurge '(?m)^\s*G0 Z\{purge_height\}\s*$' '(?m)^\s*G0 X\{purge_x_origin\} Y\{purge_y_center\}\s*$' "LINE_PURGE must raise to purge height before vertical XY move"

Write-Output "PASS: klipper parking contracts"
