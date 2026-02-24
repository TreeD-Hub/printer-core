#!/bin/bash
set -euo pipefail

# ==========================================
# RUNTIME SCRIPT: TREED CAM ENV
# ==========================================
# Назначение:
# - Загружает единый runtime-конфиг камеры (zoom profiles + active profile).
# - Дает helper-функции для валидации профиля и получения ROI.
# Контур:
# - read-only helper для runtime-скриптов камеры; не выполняет side-effect операций.

# Блок 1: Базовые пути runtime-контура камеры и файлов конфигурации.
TREED_CAM_BIN_DIR="${TREED_CAM_BIN_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
TREED_CAM_ROOT="${TREED_CAM_ROOT:-$(cd "${TREED_CAM_BIN_DIR}/.." && pwd)}"
TREED_CAM_CONFIG_DIR="${TREED_CAM_CONFIG_DIR:-${TREED_CAM_ROOT}/config}"
TREED_CAM_ZOOM_PROFILES_ENV="${TREED_CAM_ZOOM_PROFILES_ENV:-${TREED_CAM_CONFIG_DIR}/zoom_profiles.env}"
TREED_CAM_ZOOM_ACTIVE_ENV="${TREED_CAM_ZOOM_ACTIVE_ENV:-${TREED_CAM_CONFIG_DIR}/zoom_active.env}"

# Блок 2: Внутренние helper-функции валидации.
treed_cam_env_log_err() {
  printf '[treed-cam-env] %s\n' "$*" >&2
}

treed_cam_env_require_file() {
  local path="$1"
  if [[ -f "${path}" ]]; then
    return 0
  fi
  treed_cam_env_log_err "missing config file: ${path}"
  return 1
}

treed_cam_env_is_uint() {
  [[ "${1:-}" =~ ^[0-9]+$ ]]
}

treed_cam_env_require_var() {
  local name="$1"
  if [[ -n "${!name:-}" ]]; then
    return 0
  fi
  treed_cam_env_log_err "required variable is empty: ${name}"
  return 1
}

# Блок 3: Преобразование имени профиля в shell-safe suffix для env-поля.
treed_cam_env_profile_suffix() {
  local profile="${1:-}"
  profile="${profile//-/_}"
  printf '%s' "${profile^^}"
}

# Блок 4: Доступ к ROI-профилям и проверка допустимого имени профиля.
treed_cam_env_get_profile_roi_csv() {
  local profile="${1:-}"
  local suffix var_name
  suffix="$(treed_cam_env_profile_suffix "${profile}")"
  var_name="TREED_CAM_ZOOM_PROFILE_${suffix}"
  printf '%s' "${!var_name:-}"
}

treed_cam_env_profile_exists() {
  local profile="${1:-}"
  local item=""
  for item in ${TREED_CAM_ZOOM_PROFILES:-}; do
    if [[ "${item}" = "${profile}" ]]; then
      return 0
    fi
  done
  return 1
}

treed_cam_env_validate_profile() {
  local profile="${1:-}"
  if [[ -z "${profile}" ]]; then
    treed_cam_env_log_err "empty zoom profile"
    return 1
  fi
  if ! treed_cam_env_profile_exists "${profile}"; then
    treed_cam_env_log_err "unknown zoom profile: ${profile}"
    return 1
  fi
  if [[ -z "$(treed_cam_env_get_profile_roi_csv "${profile}")" ]]; then
    treed_cam_env_log_err "ROI is not configured for profile: ${profile}"
    return 1
  fi
}

# Блок 5: Парсинг ROI CSV в переменные TREED_CAM_ROI_*.
treed_cam_env_parse_roi_csv() {
  local csv="${1:-}"
  local x y w h
  IFS=',' read -r x y w h <<< "${csv}"
  if ! treed_cam_env_is_uint "${x}" || ! treed_cam_env_is_uint "${y}" \
    || ! treed_cam_env_is_uint "${w}" || ! treed_cam_env_is_uint "${h}"; then
    treed_cam_env_log_err "invalid ROI csv: ${csv}"
    return 1
  fi
  if [[ "${w}" -eq 0 || "${h}" -eq 0 ]]; then
    treed_cam_env_log_err "ROI width/height must be > 0: ${csv}"
    return 1
  fi
  TREED_CAM_ROI_X="${x}"
  TREED_CAM_ROI_Y="${y}"
  TREED_CAM_ROI_W="${w}"
  TREED_CAM_ROI_H="${h}"
  export TREED_CAM_ROI_X TREED_CAM_ROI_Y TREED_CAM_ROI_W TREED_CAM_ROI_H
}

# Блок 6: Загрузка zoom-конфига и вычисление активного профиля/ROI.
treed_cam_env_load_zoom() {
  treed_cam_env_require_file "${TREED_CAM_ZOOM_PROFILES_ENV}" || return 1
  treed_cam_env_require_file "${TREED_CAM_ZOOM_ACTIVE_ENV}" || return 1

  # shellcheck source=/dev/null
  source "${TREED_CAM_ZOOM_PROFILES_ENV}"
  # shellcheck source=/dev/null
  source "${TREED_CAM_ZOOM_ACTIVE_ENV}"

  treed_cam_env_require_var TREED_CAM_RAW_STREAM_URL || return 1
  treed_cam_env_require_var TREED_CAM_RAW_SNAPSHOT_URL || return 1
  treed_cam_env_require_var TREED_CAM_ZOOM_STREAM_URL_LOCAL || return 1
  treed_cam_env_require_var TREED_CAM_ZOOM_SNAPSHOT_URL_LOCAL || return 1
  treed_cam_env_require_var TREED_CAM_ZOOM_SNAPSHOT_FILE || return 1
  treed_cam_env_require_var TREED_CAM_ZOOM_OUTPUT_WIDTH || return 1
  treed_cam_env_require_var TREED_CAM_ZOOM_OUTPUT_HEIGHT || return 1
  treed_cam_env_require_var TREED_CAM_ZOOM_PROFILES || return 1
  treed_cam_env_require_var TREED_CAM_ZOOM_PROFILE_DEFAULT || return 1

  if ! treed_cam_env_is_uint "${TREED_CAM_ZOOM_OUTPUT_WIDTH}" \
    || ! treed_cam_env_is_uint "${TREED_CAM_ZOOM_OUTPUT_HEIGHT}"; then
    treed_cam_env_log_err "invalid output size: ${TREED_CAM_ZOOM_OUTPUT_WIDTH}x${TREED_CAM_ZOOM_OUTPUT_HEIGHT}"
    return 1
  fi

  TREED_CAM_ZOOM_PROFILE_ACTIVE="${TREED_CAM_ZOOM_PROFILE:-${TREED_CAM_ZOOM_PROFILE_DEFAULT}}"
  if [[ -z "${TREED_CAM_ZOOM_PROFILE_ACTIVE}" ]]; then
    TREED_CAM_ZOOM_PROFILE_ACTIVE="${TREED_CAM_ZOOM_PROFILE_DEFAULT}"
  fi
  treed_cam_env_validate_profile "${TREED_CAM_ZOOM_PROFILE_ACTIVE}" || return 1

  TREED_CAM_ZOOM_ACTIVE_ROI_CSV="$(treed_cam_env_get_profile_roi_csv "${TREED_CAM_ZOOM_PROFILE_ACTIVE}")"
  treed_cam_env_parse_roi_csv "${TREED_CAM_ZOOM_ACTIVE_ROI_CSV}" || return 1

  export TREED_CAM_ZOOM_PROFILE_ACTIVE
  export TREED_CAM_ZOOM_ACTIVE_ROI_CSV
}

