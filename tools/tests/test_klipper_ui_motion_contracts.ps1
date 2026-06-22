param(
  [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
)

# ==========================================
# CONTRACT-TEST: KLIPPER UI MOTION
# ==========================================
# Назначение:
# - фиксирует публичный TREED_UI_MOVE_AXIS для TreeD Shell;
# - проверяет server-side safety guards и восстановление G-code state.
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

# Блок 2: Загрузка и проверка подключения public macro.
$macros = Get-Content -Encoding UTF8 -LiteralPath (Join-Path $RepoRoot "klipper/profiles/treed_v2_corexy_v1/macros.cfg") -Raw
$motion = Get-Content -Encoding UTF8 -LiteralPath (Join-Path $RepoRoot "klipper/profiles/treed_v2_corexy_v1/macros_ui_motion.cfg") -Raw

Assert-Contains $macros '(?m)^\[include macros_ui_motion\.cfg\]\s*$' "macros.cfg must include UI motion macros"
Assert-Contains $motion '(?m)^\[gcode_macro TREED_UI_MOVE_AXIS\]\s*$' "TREED_UI_MOVE_AXIS must exist"

# Блок 3: Trust-boundary проверки до относительного движения.
Assert-Contains $motion 'params\.AXIS is not defined' "motion macro must require AXIS"
Assert-Contains $motion 'params\.DISTANCE is not defined' "motion macro must require DISTANCE"
Assert-Contains $motion 'AXIS not in \["X", "Y", "Z"\]' "motion macro must restrict axis values"
Assert-Contains $motion 'DISTANCE < -MAX_DISTANCE or DISTANCE > MAX_DISTANCE' "motion macro must bound one move"
Assert-Contains $motion 'PRINT_STATE in \["printing", "paused"\]' "motion macro must reject active print states"
Assert-Contains $motion 'AXIS\|lower not in HOMED' "motion macro must require selected axis homing"
Assert-Contains $motion 'printer\.configfile\.settings\.stepper_[xyz]\.position_(min|max)' "motion macro must use profile axis bounds"
Assert-Contains $motion 'TARGET < POSITION_MIN or TARGET > POSITION_MAX' "motion macro must reject out-of-bounds targets"
Assert-Contains $motion 'FEEDRATE < 60 or FEEDRATE > MAX_FEEDRATE' "motion macro must validate feedrate"

# Блок 4: Изоляция parser state и axis-specific move.
Assert-Contains $motion '(?m)^\s*SAVE_GCODE_STATE NAME=TREED_UI_MOVE_AXIS_STATE\s*$' "motion macro must save G-code state"
Assert-Contains $motion '(?m)^\s*G91\s*$' "motion macro must use relative mode locally"
Assert-Contains $motion '(?m)^\s*G1 X\{DISTANCE\} F\{FEEDRATE\}\s*$' "motion macro must map X move"
Assert-Contains $motion '(?m)^\s*G1 Y\{DISTANCE\} F\{FEEDRATE\}\s*$' "motion macro must map Y move"
Assert-Contains $motion '(?m)^\s*G1 Z\{DISTANCE\} F\{FEEDRATE\}\s*$' "motion macro must map Z move"
Assert-Contains $motion '(?m)^\s*RESTORE_GCODE_STATE NAME=TREED_UI_MOVE_AXIS_STATE MOVE=0\s*$' "motion macro must restore G-code state"

Write-Host "PASS: Klipper UI motion contract"
