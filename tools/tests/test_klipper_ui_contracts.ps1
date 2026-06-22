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

# Блок 2: Загрузка device contract и include-агрегатора.
$macros = Get-Content -Encoding UTF8 -LiteralPath (Join-Path $RepoRoot "klipper/profiles/treed_v2_corexy_v1/macros.cfg") -Raw
$contract = Get-Content -Encoding UTF8 -LiteralPath (Join-Path $RepoRoot "klipper/profiles/treed_v2_corexy_v1/macros_ui_contract.cfg") -Raw

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

Write-Host "PASS: Klipper UI device contract"
