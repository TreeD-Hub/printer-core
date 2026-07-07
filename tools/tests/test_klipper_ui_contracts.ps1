param(
  [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
)

# ==========================================
# CONTRACT-TEST: KLIPPER UI DEVICE CONTRACT
# ==========================================
# Назначение:
# - фиксирует versioned handshake активного профиля с TreeD Shell;
# - проверяет аппаратные лимиты, capability и required macro surface.
# Контур:
# - read-only: проверяет только текст активного профиля Klipper.

$ErrorActionPreference = "Stop"

# Блок 1: Общий assert-хелпер статического контракта.
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

# Блок 2: Загрузка device contract и include-агрегатора.
$macros = Get-Content -Encoding UTF8 -LiteralPath (Join-Path $RepoRoot "klipper/profiles/treed_v2_corexy_v1/macros.cfg") -Raw
$contract = Get-Content -Encoding UTF8 -LiteralPath (Join-Path $RepoRoot "klipper/profiles/treed_v2_corexy_v1/macros_ui_contract.cfg") -Raw
$probeEddy = Get-Content -Encoding UTF8 -LiteralPath (Join-Path $RepoRoot "klipper/profiles/treed_v2_corexy_v1/probe_eddy_duo.cfg") -Raw
$utils = Get-Content -Encoding UTF8 -LiteralPath (Join-Path $RepoRoot "klipper/profiles/treed_v2_corexy_v1/macros_utils.cfg") -Raw
$kamp = Get-Content -Encoding UTF8 -LiteralPath (Join-Path $RepoRoot "klipper/profiles/treed_v2_corexy_v1/macros_kamp.cfg") -Raw

Assert-Contains $macros '(?m)^\[include macros_ui_contract\.cfg\]\s*$' "macros.cfg must include UI device contract"
Assert-Contains $contract '(?m)^\[gcode_macro _TREED_UI_CONTRACT\]\s*$' "UI device contract macro must exist"
Assert-Contains $contract '(?m)^variable_contract_version:\s*"1\.0"\s*$' "contract version must be 1.0"
Assert-Contains $contract '(?m)^variable_profile:\s*"treed_v2_corexy_v1"\s*$' "contract profile must match active profile"

# Блок 3: Аппаратные лимиты и capability активного профиля.
Assert-Contains $contract '(?m)^variable_nozzle_max_c:\s*280\.0\s*$' "contract must publish nozzle max temperature"
Assert-Contains $contract '(?m)^variable_bed_max_c:\s*120\.0\s*$' "contract must publish bed max temperature"
foreach ($axis in @("x", "y", "z")) {
  Assert-Contains $contract "(?m)^variable_axis_${axis}_min:" "contract must publish $axis minimum"
  Assert-Contains $contract "(?m)^variable_axis_${axis}_max:" "contract must publish $axis maximum"
}
foreach ($capability in @("print", "motion", "thermal", "fan", "filament", "console", "eddy", "shaper", "motion_test", "network", "camera", "system_power", "service_commands")) {
  Assert-Contains $contract "(?m)^variable_capability_${capability}:\s*[01]\s*$" "contract must publish $capability capability"
}

# Блок 4: Обязательные safety macro для live UI.
foreach ($macro in @(
  "_TREED_EDDY_HOME_Z",
  "_TREED_CAMERA",
  "TREED_UI_MOVE_AXIS",
  "TREED_UI_SET_SPEED_FACTOR",
  "TREED_UI_SET_FLOW_FACTOR",
  "TREED_UI_SET_ACCEL",
  "TREED_UI_SET_PRESSURE_ADVANCE",
  "TREED_UI_SET_RETRACTION",
  "TREED_UI_ADJUST_Z_OFFSET",
  "LOAD_FILAMENT",
  "UNLOAD_FILAMENT",
  "TREED_Z_PARK_ZERO_EDDY",
  "TREED_SHAPER_CALIBRATE_LIGHT",
  "TREED_SHAPER_CALIBRATE_FULL",
  "TREED_XY_MOTION_TEST"
)) {
  Assert-Contains $contract ([regex]::Escape($macro)) "required macro list must include $macro"
}

# Блок 5: Workflow-контракт калибровки Eddy для TreeD Shell.
Assert-Contains $probeEddy '(?m)^\[save_variables\]\s*$' "Eddy workflow progress must use save_variables"
foreach ($macro in @(
  "_TREED_EDDY_CALIBRATION_STATE",
  "TREED_EDDY_CALIBRATE_DRIVE_CURRENT",
  "TREED_EDDY_PRIMARY_HEIGHT_START",
  "TREED_EDDY_PRIMARY_ACCEPT_SAVE",
  "TREED_EDDY_TEMPERATURE_START",
  "TREED_EDDY_TEMPERATURE_ACCEPT_SAVE",
  "TREED_EDDY_CHECK_Z0",
  "TREED_EDDY_SCREWS_TILT_START",
  "TREED_EDDY_SCREWS_TILT_DONE",
  "TREED_EDDY_BED_MESH_CALIBRATE"
)) {
  Assert-Contains $probeEddy "(?m)^\[gcode_macro $([regex]::Escape($macro))\]\s*$" "Eddy workflow macro must exist: $macro"
  Assert-Contains $contract ([regex]::Escape($macro)) "required macro list must include $macro"
}

# Блок 6: Видимая сервисная поверхность Fluidd и скрытые KAMP helper-ы.
foreach ($macro in @(
  "CALIBRATE_SCREWS",
  "CALIBRATE_BED_MESH",
  "CALIBRATE_EDDY_DRIVE",
  "CALIBRATE_EDDY_HEIGHT",
  "CALIBRATE_EDDY_TEMP",
  "CHECK_Z0"
)) {
  Assert-Contains $probeEddy "(?m)^\[gcode_macro $([regex]::Escape($macro))\]\s*$" "Fluidd service alias must exist: $macro"
}
foreach ($macro in @("MOTION_TEST", "MOTION_LIMITS_DEFAULT")) {
  Assert-Contains $utils "(?m)^\[gcode_macro $([regex]::Escape($macro))\]\s*$" "Fluidd motion alias must exist: $macro"
}
Assert-Contains $kamp '(?m)^\[gcode_macro _TREED_KAMP_SMART_PARK\]\s*$' "KAMP smart park helper must be hidden from Fluidd"
Assert-Contains $kamp '(?m)^\[gcode_macro _TREED_KAMP_LINE_PURGE\]\s*$' "KAMP line purge helper must be hidden from Fluidd"
Assert-NotContains $kamp '(?m)^\[gcode_macro SMART_PARK\]\s*$' "KAMP smart park must not be public in active profile"
Assert-NotContains $kamp '(?m)^\[gcode_macro LINE_PURGE\]\s*$' "KAMP line purge must not be public in active profile"

Write-Host "PASS: Klipper UI device contract"
