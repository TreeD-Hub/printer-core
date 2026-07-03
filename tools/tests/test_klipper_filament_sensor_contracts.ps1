$ErrorActionPreference = "Stop"

# ==========================================
# CONTRACT TEST: FILAMENT SENSOR
# ==========================================
# Назначение:
# - фиксирует публичный mode/status-контракт BTT SFS V2.0;
# - проверяет persistent restore и sensor-safe LOAD/UNLOAD.
# Контур:
# - static: выполняется локально без Klipper runtime.

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

function Read-RepoFile([string]$relativePath) {
  return Get-Content -LiteralPath (Join-Path $repoRoot $relativePath) -Raw -Encoding UTF8
}

function Assert-Contains([string]$text, [string]$pattern, [string]$message) {
  if ($text -notmatch $pattern) {
    throw $message
  }
}

function Assert-NotContains([string]$text, [string]$pattern, [string]$message) {
  if ($text -match $pattern) {
    throw $message
  }
}

$sensor = Read-RepoFile "klipper/profiles/treed_v2_corexy_v1/filament_sensor.cfg"
$macros = Read-RepoFile "klipper/profiles/treed_v2_corexy_v1/macros_filament.cfg"
$printer = Read-RepoFile "klipper/printer.cfg"
$motionRuntime = Read-RepoFile "klipper/filament_motion_runtime.cfg"
$klipperCore = Read-RepoFile "loader/steps/klipper-core.sh"
$moonrakerCore = Read-RepoFile "moonraker/base/00-core.conf"
$uiContract = Read-RepoFile "klipper/profiles/treed_v2_corexy_v1/macros_ui_contract.cfg"

Assert-Contains $sensor '(?ms)^\[filament_switch_sensor filament_switch\].*?^switch_pin:\s*PG13\s*$' "filament_switch must keep the deployed PG13 mapping"
Assert-NotContains $sensor '(?m)^\[filament_motion_sensor filament_motion\]\s*$' "filament_motion must live only in generated runtime config"
Assert-Contains $motionRuntime '(?ms)^\[filament_motion_sensor filament_motion\].*?^switch_pin:\s*PG12\s*$' "filament_motion must keep the deployed PG12 mapping"
Assert-Contains $motionRuntime '(?m)^detection_length:\s*15\.0\s*$' "loader default sensitivity must be medium"
Assert-Contains $printer '(?m)^\[include filament_motion_runtime\.cfg\]\s*$' "printer.cfg must include generated filament motion config"
Assert-Contains $klipperCore 'saving filament_motion_runtime\.cfg' "preserve deploy must save user sensitivity config"
Assert-Contains $klipperCore 'restored filament_motion_runtime\.cfg' "preserve deploy must restore user sensitivity config"
Assert-Contains $moonrakerCore '(?m)^\[treed_filament_sensor\]\s*$' "Moonraker filament sensor component must be enabled"
Assert-Contains $moonrakerCore 'config_path:\s*\{\{PI_HOME\}\}/printer_data/config/filament_motion_runtime\.cfg' "component must target the runtime config"
Assert-Contains $uiContract 'variable_capability_filament_sensor_control:\s*1' "device contract must expose filament sensor control capability"
Assert-Contains $uiContract 'variable_capability_filament_encoder_sensitivity:\s*1' "device contract must expose encoder sensitivity capability"
Assert-Contains $uiContract 'FILAMENT_SENSOR_SET_MODE,FILAMENT_SENSOR_STATUS' "device contract must require public filament sensor macros"
Assert-Contains $uiContract '_FILAMENT_SENSOR_SENSITIVITY_STATE' "device contract must require sensitivity state macro"
Assert-Contains $sensor '(?m)^\[gcode_macro FILAMENT_SENSOR_STATUS\]\s*$' "public FILAMENT_SENSOR_STATUS macro is required"
Assert-Contains $sensor '(?m)^variable_mode:\s*"presence"\s*$' "status macro must publish the selected mode"
Assert-Contains $sensor '(?m)^\[gcode_macro FILAMENT_SENSOR_SET_MODE\]\s*$' "public FILAMENT_SENSOR_SET_MODE macro is required"
Assert-Contains $sensor 'SAVE_VARIABLE\s+VARIABLE=filament_sensor_mode' "selected mode must persist through save_variables"
Assert-Contains $sensor 'SET_FILAMENT_SENSOR\s+SENSOR=filament_switch\s+ENABLE=1' "presence channel must be enabled in both modes"
Assert-Contains $sensor 'SET_FILAMENT_SENSOR\s+SENSOR=filament_motion\s+ENABLE=\{motion_enabled\}' "motion channel must follow the selected mode"
Assert-Contains $sensor '(?ms)^\[gcode_macro _FILAMENT_SENSOR_APPLY_MODE\].*?if ''filament_motion_sensor filament_motion'' in printer.*?SET_FILAMENT_SENSOR\s+SENSOR=filament_motion' "presence mode must tolerate a missing motion channel"
Assert-Contains $sensor '(?m)^\s*\{% set motion_enabled = printer\["filament_motion_sensor filament_motion"\]\.enabled\|default\(0\)\|int if ''filament_motion_sensor filament_motion'' in printer else 0 %\}\s*$' "status macro must tolerate a missing motion channel"
Assert-Contains $sensor '(?m)^\[delayed_gcode _FILAMENT_SENSOR_RESTORE_MODE\]\s*$' "startup restore macro is required"
Assert-Contains $sensor 'printer\.save_variables\.variables\.filament_sensor_mode' "startup restore must read the persisted mode"

Assert-Contains $macros '(?m)^\[gcode_macro _FILAMENT_SENSOR_GUARD_START\]\s*$' "shared sensor guard start helper is required"
Assert-Contains $macros '(?m)^\[gcode_macro _FILAMENT_SENSOR_GUARD_FINISH\]\s*$' "shared sensor guard finish helper is required"
Assert-Contains $macros 'UPDATE_DELAYED_GCODE\s+ID=_FILAMENT_SENSOR_GUARD_RESTORE\s+DURATION=\{restore_delay\}' "guard must arm a calculated restore timeout"
Assert-Contains $macros '(?ms)^\[gcode_macro _TREED_FILAMENT_MOVE\].*?^\s*M400\s*$.*?^\s*_FILAMENT_SENSOR_GUARD_FINISH\s*$' "normal restore must wait for service extrusion to finish"
Assert-Contains $macros '_FILAMENT_SENSOR_APPLY_MODE' "LOAD/UNLOAD restore must apply the selected mode"

Write-Host "Klipper filament sensor contracts: PASS"
