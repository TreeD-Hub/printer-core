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

function Get-ConfigNumber {
  param([string]$Content, [string]$Key)

  $match = [regex]::Match($Content, "(?m)^\s*$([regex]::Escape($Key)):\s*(-?[0-9.]+)\s*$")
  if (-not $match.Success) { throw "FAIL: config value $Key not found" }
  return [double]::Parse($match.Groups[1].Value, [Globalization.CultureInfo]::InvariantCulture)
}

# Блок 2: Загрузка Eddy-профиля и профильного compatibility include.
$RemovedZ0Adjust = "z0" + "_adjust"
$probeEddy = Read-RepoFile "klipper/profiles/treed_v2_corexy_v1/probe_eddy_duo.cfg"
$geometry = Read-RepoFile "klipper/profiles/treed_v2_corexy_v1/geometry.cfg"
$steppers = Read-RepoFile "klipper/profiles/treed_v2_corexy_v1/steppers.cfg"
$forceMoveCompat = Read-RepoFile "klipper/profiles/treed_v2_corexy_v1/eddy_force_move_calibration.cfg"
$macrosFlow = Read-RepoFile "klipper/profiles/treed_v2_corexy_v1/macros_print_flow.cfg"
$profileReadme = Read-RepoFile "klipper/profiles/treed_v2_corexy_v1/README.md"

$eddyHomeZ = Get-GcodeMacroBlock $probeEddy "_TREED_EDDY_HOME_Z"
$eddyZ0Cfg = Get-GcodeMacroBlock $probeEddy "_TREED_EDDY_Z0_CFG"
$reloadZOffset = Get-GcodeMacroBlock $probeEddy "_RELOAD_Z_OFFSET_FROM_PROBE"
$setZFromProbe = Get-GcodeMacroBlock $probeEddy "SET_Z_FROM_PROBE"
$captureLiveZ = Get-GcodeMacroBlock $probeEddy "_TREED_EDDY_CAPTURE_LIVE_Z_OFFSET"
$eddyMeshCfg = Get-GcodeMacroBlock $probeEddy "_TREED_EDDY_MESH_CFG"
$eddyMesh = Get-GcodeMacroBlock $probeEddy "TREED_BED_MESH_CALIBRATE_EDDY"
$eddyUiMesh = Get-GcodeMacroBlock $probeEddy "TREED_EDDY_BED_MESH_CALIBRATE"
$bedMeshAlias = Get-GcodeMacroBlock $probeEddy "CALIBRATE_BED_MESH"
$startAdaptiveMesh = Get-GcodeMacroBlock $macrosFlow "_TREED_START_ADAPTIVE_MESH"
$startSmartPark = Get-GcodeMacroBlock $macrosFlow "_TREED_START_SMART_PARK"
$startPrep = Get-GcodeMacroBlock $macrosFlow "_TREED_START_PREP_STATE"
$startMachinePrep = Get-GcodeMacroBlock $macrosFlow "_TREED_START_MACHINE_PREP"
$startPrint = Get-GcodeMacroBlock $macrosFlow "START_PRINT"

# Блок 3: Проверка штатного Eddy Z-home без постоянного z0_adjust.
Assert-Contains $probeEddy '(?ms)^\[force_move\]\s+enable_force_move:\s*True' "Eddy runtime profile must enable SET_KINEMATIC_POSITION"
Assert-Contains $probeEddy '(?ms)^\[temperature_probe btt_eddy\]\s+sensor_type:\s*Generic 3950\s+sensor_pin:\s*eddy:gpio26\s+min_temp:\s*10\s+max_temp:\s*100\s+horizontal_move_z:\s*2' "Eddy profile must expose linked temperature_probe for thermal drift calibration"
Assert-NotContains $probeEddy '(?m)^\[temperature_sensor _btt_eddy_mcu\]\s*$' "Eddy MCU diagnostic temperature must not be exposed as a UI temperature sensor"
Assert-Contains $profileReadme 'TEMPERATURE_PROBE_CALIBRATE PROBE=btt_eddy TARGET=56 STEP=4' "Eddy README must document temperature drift calibration"
Assert-NotContains $forceMoveCompat '(?m)^\[force_move\]' "legacy Eddy calibration include must not duplicate runtime force_move section"
Assert-Contains $probeEddy '(?m)^\s*y_offset:\s*-30\.0\s*$' "Eddy Y offset must place the sensing point 30mm closer to Y0 than the nozzle at Y245"

Assert-NotContains $probeEddy $RemovedZ0Adjust "Eddy profile must not use fixed base Z adjustment"
Assert-NotContains $probeEddy 'Eddy Z0 adjust applied' "Eddy profile must not report fixed SET_GCODE_OFFSET Z adjustment"
Assert-NotContains $captureLiveZ 'homing_origin\.z\|float\s*-' "autosave capture must not subtract a removed base z0_adjust"

Assert-ContainsBefore $eddyHomeZ '(?m)^\s*G28\.1 Z\s*$' '(?m)^\s*SET_Z_FROM_PROBE\s*$' "Eddy home must run correction immediately after G28.1 Z"
Assert-ContainsBefore $eddyHomeZ '(?m)^\s*G28 X Y\s*$' '(?m)^\s*G1 X\{zero_tool_x\} Y\{zero_tool_y\} F12000\s*$' "Eddy home restores X/Y homing when needed"
Assert-ContainsBefore $eddyHomeZ 'if "z" in printer\.toolhead\.homed_axes' '(?m)^\s*G1 Z\{z_hop\} F1500\s*$' "Eddy home lifts only known Z before XY travel"
Assert-ContainsBefore $eddyHomeZ '(?m)^\s*SET_GCODE_OFFSET Z=0 MOVE=0\s*$' '(?m)^\s*G1 X\{zero_tool_x\} Y\{zero_tool_y\} F12000\s*$' "Eddy home clears Z offset before XY travel"
Assert-ContainsBefore $eddyHomeZ '(?m)^\s*SET_Z_FROM_PROBE\s*$' '(?m)^\s*_TREED_PRINT_OFFSET_ENABLE\s*$' "Eddy home restores an enabled print offset"
Assert-Contains $eddyZ0Cfg '(?m)^\s*variable_home_probe_speed:\s*2\.0\s*$' "Eddy Z-home must slow precise PROBE descent"
Assert-Contains $eddyZ0Cfg '(?m)^\s*variable_home_probe_clearance:\s*2\.0\s*$' "Eddy Z-home must keep post-home clearance inside saved calibration range"
Assert-Contains $eddyZ0Cfg '(?m)^\s*variable_home_lift_speed:\s*5\.0\s*$' "Eddy Z-home must slow lift between precise PROBE samples"
Assert-ContainsBefore $setZFromProbe '(?m)^\s*G1 Z\{clearance_z\} F1500\s*$' '(?m)^\s*PROBE\b' "SET_Z_FROM_PROBE must clear triggered Eddy state before precise PROBE"
Assert-ContainsBefore $setZFromProbe '(?m)^\s*M400\s*$' '(?m)^\s*PROBE\b' "SET_Z_FROM_PROBE must wait for clearance move before precise PROBE"
Assert-Contains $setZFromProbe '(?m)^\s*PROBE\s+PROBE_SPEED=\{cfg\.home_probe_speed\|float\}\s+SAMPLES=' "SET_Z_FROM_PROBE must pass explicit slow PROBE_SPEED"
Assert-ContainsBefore $setZFromProbe '(?m)^\s*PROBE\b' '(?m)^\s*_RELOAD_Z_OFFSET_FROM_PROBE\s*$' "SET_Z_FROM_PROBE must probe before reloading Z"
Assert-Contains $reloadZOffset 'printer\.probe\.last_probe_position\.z' "Z reload must use the last PROBE result"
Assert-NotContains $reloadZOffset 'SET_HOMED=NONE' "Z reload restores the previous kinematic homing behavior"
Assert-Contains $reloadZOffset '(?m)^\s*SET_KINEMATIC_POSITION Z=\{z - printer\.probe\.last_probe_position\.z\}\s*$' "Z reload restores the earlier SET_KINEMATIC_POSITION command"

# Блок 4: START_PRINT очищает старую mesh до изменений состояния и строит новую после сервисных движений.
Assert-ContainsBefore $startPrint '(?m)^\s*_TREED_START_PREP_STATE \{rawparams\}\s*$' '(?m)^\s*_TREED_START_MACHINE_PREP\s*$' "START_PRINT должен проверить параметры до нагрева"
Assert-Contains $startPrep 'params\.MESH is defined and params\.MESH\|lower != "adaptive"' "START_PRINT должен отклонить старые mesh-режимы"
Assert-Contains $startPrep 'params\.MESH_METHOD is defined and params\.MESH_METHOD\|lower != "rapid_scan"' "START_PRINT должен разрешать только rapid_scan"
Assert-Contains $startPrep 'params\.MESH_PROFILE is defined or params\.MESH_MIN is defined or params\.MESH_MAX is defined' "Сервисные mesh-параметры запрещены в START_PRINT"
Assert-ContainsBefore $startPrep '(?m)^\s*_TREED_KAMP_REQUIRE_READY\s*$' '(?m)^\s*BED_MESH_CLEAR\s*$' "Object metadata нужно проверить до очистки mesh"
Assert-ContainsBefore $startPrep '(?m)^\s*BED_MESH_CLEAR\s*$' '(?m)^\s*SET_GCODE_VARIABLE MACRO=_TREED_START_STATE\b' "Очистка mesh должна предшествовать изменению state"
Assert-ContainsBefore $startPrep '(?m)^\s*BED_MESH_CLEAR\s*$' '(?m)^\s*_TREED_PRINT_OFFSET_DISABLE\s*$' "Сначала сбрасывается старая mesh"
Assert-NotContains $startMachinePrep '(?m)^\s*_TREED_HOME_ALL\s*$' "Подготовка машины не должна делать homing до прогрева"
Assert-ContainsBefore $startPrint '(?m)^\s*_TREED_START_PREHEAT\s*$' '(?m)^\s*_TREED_HOME_ALL\s*$' "Homing выполняется после прогрева стола"
Assert-ContainsBefore $startPrint '(?m)^\s*_TREED_HOME_ALL\s*$' '(?m)^\s*_TREED_START_INPUT_SHAPER\s*$' "Shaper выполняется после homing"
Assert-ContainsBefore $startPrint '(?m)^\s*_TREED_START_INPUT_SHAPER\s*$' '(?m)^\s*_TREED_START_ADAPTIVE_MESH\s*$' "Mesh строится после сервисной калибровки"
Assert-ContainsBefore $startPrint '(?m)^\s*_TREED_START_ADAPTIVE_MESH\s*$' '(?m)^\s*_TREED_START_SMART_PARK\s*$' "Smart park выполняется после mesh"
Assert-Contains $startAdaptiveMesh 'TREED_BED_MESH_CALIBRATE_EDDY PROFILE=treed_adaptive METHOD=rapid_scan ADAPTIVE=1 ADAPTIVE_MARGIN=\{st\.adaptive_margin\}' "Обычная печать всегда строит rapid adaptive mesh"
Assert-NotContains $startPrint 'BED_MESH_PROFILE LOAD' "START_PRINT не должен загружать старую mesh"
Assert-ContainsBefore $startSmartPark '(?m)^\s*_TREED_PRINT_OFFSET_ENABLE\s*$' '(?m)^\s*_TREED_KAMP_SMART_PARK\s*$' "Smart park должен работать в print coords"
Assert-ContainsBefore $eddyMesh '(?m)^\s*_TREED_PRINT_OFFSET_DISABLE\s*$' '(?m)^\s*BED_MESH_CLEAR\s*$' "Сервисный Eddy mesh переходит в raw coords перед очисткой"
Assert-NotContains $eddyMesh '(?m)^\s*_TREED_PRINT_OFFSET_ENABLE\s*$' "Сервисный Eddy mesh не восстанавливает старые print coords"

# Блок 5: Eddy mesh использует отдельную scan area без изменения механики.
Assert-Contains $geometry '(?m)^variable_print_size_y:\s*245\.0\s*$' "Print area Y must stay 245"
Assert-Contains $geometry '(?m)^variable_bed_size_y:\s*245\.0\s*$' "Bed area Y must stay 245"
Assert-Contains $steppers '(?ms)^\[stepper_y\].*?^position_max:\s*245\s*$' "Stepper Y max must stay 245"
Assert-Contains $eddyMeshCfg '(?m)^\s*variable_scan_min_x:\s*10\.0\s*$' "Eddy scan min X must be explicit"
Assert-Contains $eddyMeshCfg '(?m)^\s*variable_scan_min_y:\s*10\.0\s*$' "Eddy scan min Y must be explicit"
Assert-Contains $eddyMeshCfg '(?m)^\s*variable_scan_max_x:\s*235\.0\s*$' "Eddy scan max X must be explicit"
Assert-Contains $eddyMeshCfg '(?m)^\s*variable_scan_max_y:\s*210\.0\s*$' "Eddy scan max Y must be explicit"
Assert-NotContains $eddyMeshCfg '(?m)^\s*variable_speed:' "Eddy mesh must not expose an unused speed variable"
Assert-Contains $probeEddy '(?ms)^\[bed_mesh\].*?^mesh_min:\s*10,10\s*$' "bed_mesh parser fallback must use Eddy scan min"
Assert-Contains $probeEddy '(?ms)^\[bed_mesh\].*?^mesh_max:\s*235,210\s*$' "bed_mesh parser fallback must use Eddy scan max"
Assert-Contains $eddyMesh 'params\.MESH_MIN\|default\(default_mesh_min\)' "Eddy mesh must honor MESH_MIN with a safe default"
Assert-Contains $eddyMesh 'params\.MESH_MAX\|default\(default_mesh_max\)' "Eddy mesh must honor MESH_MAX with a safe default"
Assert-Contains $eddyMesh 'mesh_min_raw\.split\(","\)' "Eddy mesh must parse MESH_MIN coordinates"
Assert-Contains $eddyMesh 'mesh_max_raw\.split\(","\)' "Eddy mesh must parse MESH_MAX coordinates"
Assert-Contains $eddyMesh 'scan_min_x < safe_min_x or scan_max_x > safe_max_x' "Eddy mesh must reject probe bounds outside the safe scan area"
Assert-Contains $eddyMesh 'scan_min_y < safe_min_y or scan_max_y > safe_max_y' "Eddy mesh must reject Y bounds outside the safe scan area"
Assert-Contains $eddyMesh 'tool_scan_min_x = scan_min_x - probe_x_offset' "Eddy mesh must convert X from probe to tool coordinates"
Assert-Contains $eddyMesh 'tool_scan_max_x = scan_max_x - probe_x_offset' "Eddy mesh must convert max X from probe to tool coordinates"
Assert-Contains $eddyMesh 'tool_scan_min_y = scan_min_y - probe_y_offset' "Eddy mesh must convert Y from probe to tool coordinates"
Assert-Contains $eddyMesh 'tool_scan_max_y = scan_max_y - probe_y_offset' "Eddy mesh must convert max Y from probe to tool coordinates"
Assert-Contains $eddyMesh 'tool_scan_min_y < th\.axis_minimum\.y\|float or tool_scan_max_y > th\.axis_maximum\.y\|float' "Eddy mesh must validate converted Y movement bounds"
Assert-Contains $eddyMesh 'METHOD=\{method\} MESH_MIN=\{scan_min_x\},\{scan_min_y\} MESH_MAX=\{scan_max_x\},\{scan_max_y\}' "Eddy mesh must pass effective bounds to Klipper"
Assert-Contains $eddyMesh 'METHOD=\{method\}.*?SCAN_SPEED=\{effective_speed\}' "rapid_scan must receive SCAN_SPEED"
Assert-Contains $eddyMesh 'params\.SCAN_SPEED is defined' "non-rapid methods must reject SCAN_SPEED instead of pretending to use it"
Assert-Contains $eddyMesh 'params\.SPEED is defined' "legacy SPEED must fail explicitly"
Assert-Contains $eddyMesh 'Eddy mesh: profile=\{profile\} method=\{method\} probe=' "Eddy mesh must report effective parameters"
Assert-Contains $eddyMesh 'Eddy safe scan area: X\{safe_min_x\}\.\.\{safe_max_x\} Y\{safe_min_y\}\.\.\{safe_max_y\}' "Eddy mesh must report new safe scan bounds"
Assert-Contains $eddyMesh 'Eddy scan area is smaller than print area because probe cannot physically cover full bed\.' "Eddy mesh must retain the scan area explanation"
Assert-Contains $eddyMesh 'Print area: X\{print_min_x\}\.\.\{print_max_x\} Y\{print_min_y\}\.\.\{print_max_y\}' "Eddy mesh must report the unchanged print area"
Assert-ContainsBefore $eddyUiMesh '(?m)^\s*SAVE_VARIABLE VARIABLE=treed_eddy_mesh_done VALUE=0\s*$' '(?m)^\s*TREED_BED_MESH_CALIBRATE_EDDY PROFILE=default METHOD=scan\{mesh_bounds\}\s*$' "UI mesh workflow must clear previous mesh success before a new scan"
Assert-ContainsBefore $eddyUiMesh '(?m)^\s*TREED_BED_MESH_CALIBRATE_EDDY PROFILE=default METHOD=scan\{mesh_bounds\}\s*$' '(?m)^\s*SAVE_VARIABLE VARIABLE=treed_eddy_mesh_done VALUE=1\s*$' "UI mesh workflow must mark success only after the scan"
Assert-Contains $eddyUiMesh 'MESH_MIN=" ~ params\.MESH_MIN if params\.MESH_MIN is defined' "UI mesh must forward an explicit min only when supplied"
Assert-Contains $eddyUiMesh 'MESH_MAX=" ~ params\.MESH_MAX if params\.MESH_MAX is defined' "UI mesh must forward an explicit max only when supplied"
Assert-Contains $bedMeshAlias 'TREED_EDDY_BED_MESH_CALIBRATE \{rawparams\}' "CALIBRATE_BED_MESH must forward explicit bounds"
Assert-NotContains $startAdaptiveMesh 'MESH_MIN|MESH_MAX|MESH_PROFILE' "Production mesh не должен передавать сервисные границы или профиль"

$safeMinX = Get-ConfigNumber $eddyMeshCfg 'variable_scan_min_x'
$safeMinY = Get-ConfigNumber $eddyMeshCfg 'variable_scan_min_y'
$safeMaxX = Get-ConfigNumber $eddyMeshCfg 'variable_scan_max_x'
$safeMaxY = Get-ConfigNumber $eddyMeshCfg 'variable_scan_max_y'
$bedMin = [regex]::Match($probeEddy, '(?m)^mesh_min:\s*([0-9.]+),([0-9.]+)\s*$')
$bedMax = [regex]::Match($probeEddy, '(?m)^mesh_max:\s*([0-9.]+),([0-9.]+)\s*$')
if (-not $bedMin.Success -or -not $bedMax.Success) { throw 'FAIL: bed_mesh bounds not found' }
$invariant = [Globalization.CultureInfo]::InvariantCulture
if ([double]::Parse($bedMin.Groups[1].Value, $invariant) -ne $safeMinX -or
    [double]::Parse($bedMin.Groups[2].Value, $invariant) -ne $safeMinY -or
    [double]::Parse($bedMax.Groups[1].Value, $invariant) -ne $safeMaxX -or
    [double]::Parse($bedMax.Groups[2].Value, $invariant) -ne $safeMaxY) {
  throw 'FAIL: bed_mesh and Eddy macro bounds differ'
}

foreach ($case in @(
  @{ Name='full safe area'; Min=@(10,10); Max=@(235,210); Accept=$true },
  @{ Name='narrower area'; Min=@(40,40); Max=@(205,180); Accept=$true },
  @{ Name='previous X min'; Min=@(7.5,10); Max=@(235,210); Accept=$false },
  @{ Name='previous X max'; Min=@(10,10); Max=@(237.5,210); Accept=$false },
  @{ Name='old X min'; Min=@(5,10); Max=@(235,210); Accept=$false },
  @{ Name='old X max'; Min=@(10,10); Max=@(240,210); Accept=$false },
  @{ Name='old Y min'; Min=@(10,5); Max=@(235,210); Accept=$false },
  @{ Name='old Y max'; Min=@(10,10); Max=@(235,215); Accept=$false },
  @{ Name='outside Y min'; Min=@(10,9.9); Max=@(235,210); Accept=$false },
  @{ Name='outside Y max'; Min=@(10,10); Max=@(235,210.1); Accept=$false }
)) {
  $accepted = $case.Min[0] -ge $safeMinX -and $case.Min[1] -ge $safeMinY -and
              $case.Max[0] -le $safeMaxX -and $case.Max[1] -le $safeMaxY -and
              $case.Min[0] -lt $case.Max[0] -and $case.Min[1] -lt $case.Max[1]
  if ($accepted -ne $case.Accept) { throw "FAIL: Eddy safe area case $($case.Name)" }
}
$offsetX = Get-ConfigNumber $probeEddy 'x_offset'
$offsetY = Get-ConfigNumber $probeEddy 'y_offset'
if ($offsetX -ne 0 -or $offsetY -ne -30 -or
    $safeMinX - $offsetX -ne 10 -or $safeMaxX - $offsetX -ne 235 -or
    $safeMinY - $offsetY -ne 40 -or $safeMaxY - $offsetY -ne 240) {
  throw 'FAIL: Eddy probe-to-tool coordinates differ from the safe area contract'
}

# Блок 6: Bed mesh не должен безусловно переhome-ить уже известные оси.
Assert-NotContains $eddyMesh '(?ms)^\s*BED_MESH_CLEAR\s*$\s*^\s*G28\s*$' "Eddy mesh must not unconditionally run full G28 after a successful START_PRINT homing"
Assert-Contains $eddyMesh 'printer\.toolhead\.homed_axes\|lower' "Eddy mesh must inspect current homed axes before deciding on homing"
Assert-ContainsBefore $eddyMesh '(?m)^\s*{% if ''x'' not in homed or ''y'' not in homed %}\s*$' '(?m)^\s*G28\s*$' "Eddy mesh must full-home only when X or Y is unknown"
Assert-ContainsBefore $eddyMesh '(?m)^\s*{% elif ''z'' not in homed %}\s*$' '(?m)^\s*G28 Z\s*$' "Eddy mesh must home only Z when X/Y are already known"

Write-Output "PASS: klipper eddy contracts"
