$ErrorActionPreference = "Stop"

# ==========================================
# CONTRACT TEST: ДОПУСК ОПЕРАЦИЙ KLIPPER
# ==========================================
# Назначение:
# - проверяет общий guard и переходы фаз до сервисных движений.
# Контур:
# - read-only: статическая проверка активного профиля.

$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
function Read-File([string]$path) {
  Get-Content -Encoding UTF8 -LiteralPath (Join-Path $root $path) -Raw
}
function Assert-Has([string]$body, [string]$pattern, [string]$message) {
  if ($body -notmatch $pattern) { throw "FAIL: $message" }
}
function Assert-Before([string]$body, [string]$first, [string]$second, [string]$message) {
  $a = [regex]::Match($body, $first)
  $b = [regex]::Match($body, $second)
  if (-not $a.Success -or -not $b.Success -or $a.Index -ge $b.Index) { throw "FAIL: $message" }
}
function Get-Macro([string]$body, [string]$name) {
  $found = [regex]::Match($body, "(?ms)^\[gcode_macro $([regex]::Escape($name))\].*?(?=^\[gcode_macro |\z)")
  if (-not $found.Success) { throw "FAIL: macro $name is missing" }
  return $found.Value
}

$core = Read-File "klipper/profiles/treed_v2_corexy_v1/macros_core.cfg"
$flow = Read-File "klipper/profiles/treed_v2_corexy_v1/macros_print_flow.cfg"
$pause = Read-File "klipper/profiles/treed_v2_corexy_v1/macros_pause_resume.cfg"
$filament = Read-File "klipper/profiles/treed_v2_corexy_v1/macros_filament.cfg"
$shaper = Read-File "klipper/profiles/treed_v2_corexy_v1/macros_input_shaper.cfg"
$motion = Read-File "klipper/profiles/treed_v2_corexy_v1/macros_ui_motion.cfg"
$features = Read-File "klipper/profiles/treed_v2_corexy_v1/gcode_features.cfg"
$start = Get-Macro $flow "START_PRINT"
$startShaper = Get-Macro $flow "_TREED_START_INPUT_SHAPER"
$cancel = Get-Macro $pause "CANCEL_PRINT"
$pauseExec = Get-Macro $pause "_TREED_PAUSE_EXEC"
$resumeExec = Get-Macro $pause "_TREED_RESUME_HEAT_PURGE_WIPE"
$shaperRun = Get-Macro $shaper "_TREED_SHAPER_CALIBRATE_RUN"
$m600 = Get-Macro $features "M600"
$m600Unload = Get-Macro $features "_TREED_M600_UNLOAD"

Assert-Has $core '"idle", "preparing", "printing", "paused", "calibrating", "start_calibrating", "auto_remove"' "all operational phases must be represented"
Assert-Has $core 'state != "idle" and not \(state == "paused" and \(paused == 1 or print_state == "paused"\)\)' "filament must be allowed only at idle or native pause"
Assert-Has $core 'state != "printing" or print_state != "printing"' "PAUSE must require an active print"
Assert-Has $core 'для парковки нужны достоверные X/Y/Z в пределах осей' "PAUSE must require known bounded XYZ"
Assert-Has $core 'state != "idle" and state != "start_calibrating"' "shaper must require idle or internal start phase"
Assert-Has $core 'phase not in \["calibrating", "start_calibrating"\]' "shaper runner must require an active calibration phase"

Assert-Before $start '(?m)^\s*_TREED_OPERATION_REQUIRE OP=start\s*$' '(?m)^\s*_TREED_START_MACHINE_PREP\s*$' "print start must check admission before heating"
Assert-Before $start "VALUE=`"'preparing'`"" '(?m)^\s*_TREED_START_PREHEAT\s*$' "print start must mark preparation before heating"
Assert-Before $startShaper "VALUE=`"'start_calibrating'`"" 'TREED_SHAPER_CALIBRATE MODE=' "internal calibration must enter its own phase"
Assert-Has $startShaper 'phase\|string\|lower != "preparing"' "internal shaper entry must reject direct calls outside print preparation"
Assert-Before $startShaper 'TREED_SHAPER_CALIBRATE MODE=' "VALUE=`"'preparing'`"" "internal calibration must return to preparation"
Assert-Has $flow "VALUE=`"'printing'`"" "print start must end in printing phase"
Assert-Before $pause '(?m)^\s*_TREED_OPERATION_REQUIRE OP=pause\s*$' '(?m)^\s*_TREED_PAUSE_PREP_STATE\s*$' "PAUSE must check admission before side effects"
Assert-Before $pause '(?m)^\s*_TREED_OPERATION_REQUIRE OP=resume\s*$' '(?m)^\s*_TREED_RESUME_PREP_WIPE\s*$' "RESUME must check admission before service moves"
Assert-Before $pauseExec '_TREED_OPERATION_REQUIRE OP=pause' '(?m)^\s*PAUSE_BASE\s*$' "direct pause helper must not bypass admission"
Assert-Before $resumeExec '_TREED_OPERATION_REQUIRE OP=resume' '(?m)^\s*G1 E50 F600\s*$' "direct resume helper must not bypass admission"
Assert-Before $filament '_TREED_OPERATION_REQUIRE OP=filament' '_FILAMENT_SENSOR_GUARD_START DURATION=' "filament admission must precede sensor changes and extrusion"
Assert-Before $m600 '(?m)^\s*PAUSE\s*$' '(?m)^\s*M400\s*$' "M600 must finish parking before checking temperature"
Assert-Before $m600 '(?m)^\s*M400\s*$' '(?m)^\s*_TREED_M600_UNLOAD UNLOAD_TEMP=' "M600 must invoke unload helper after parking"
if ($m600 -match 'can_extrude|M109|UNLOAD_FILAMENT LENGTH=') { throw "FAIL: M600 must defer temperature check and unload to its helper" }
Assert-Before $m600Unload 'if not printer\.extruder\.can_extrude' '(?m)^\s*M109 S\{unload_temp\}\s*$' "M600 helper must read current temperature before heating"
Assert-Before $m600Unload '(?m)^\s*M109 S\{unload_temp\}\s*$' '(?m)^\s*UNLOAD_FILAMENT LENGTH=' "M600 helper must heat before service unload"
Assert-Before $motion '_TREED_OPERATION_REQUIRE OP=ui_move' 'SAVE_GCODE_STATE NAME=TREED_UI_MOVE_AXIS_STATE' "UI move must check admission before motion"
Assert-Has $shaper '_TREED_OPERATION_REQUIRE OP=shaper' "shaper must use shared admission"
Assert-Before $shaperRun '_TREED_OPERATION_REQUIRE OP=shaper_run' 'SHAPER_CALIBRATE AXIS=X' "direct shaper runner must not bypass admission"
if ($shaper -match 'SOURCE\s*!=\s*"start_print"|SOURCE=start_print|set SOURCE =') { throw "FAIL: SOURCE must not authorize calibration" }
Assert-Before $cancel "VALUE=`"'idle'`"" '(?m)^\s*G1 Z\{target_z - current_z\} F300\s*$' "cancel must clear operation phase before optional motion"

Write-Host "PASS: Klipper operation contracts"
