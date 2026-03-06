#!/bin/bash

# ==========================================
# RUNTIME LIB: TREED CAM ENV
# ==========================================
# Назначение:
# - Централизует runtime-настройки камеры для скриптов session/snapshot.
# - Дает единый источник endpoint и вспомогательное throttled-логирование ошибок.
# Контур:
# - библиотека подключается через `source`, не исполняется как standalone-процесс.

# Блок 1: Подгрузка опционального runtime env-файла с пользовательскими override.
treed_cam_load_runtime_env() {
  local pi_user="${PI_USER:-pi}"
  local pi_home="${PI_HOME:-/home/${pi_user}}"
  local cfg_dir="${TREED_CAM_CONFIG_DIR:-${pi_home}/treed/cam/config}"
  local runtime_env="${TREED_CAM_RUNTIME_ENV:-${cfg_dir}/runtime.env}"

  if [[ -f "${runtime_env}" ]]; then
    # shellcheck disable=SC1090
    source "${runtime_env}"
  fi
}

# Блок 2: Единая точка выдачи snapshot endpoint.
treed_cam_snapshot_url() {
  local default_url="http://127.0.0.1:8080/?action=snapshot"
  printf '%s\n' "${TREED_CAM_SNAPSHOT_URL:-${default_url}}"
}

# Блок 3: Ограниченное предупреждение о сбоях snapshot (не на каждый тик).
treed_cam_warn_snapshot_failure() {
  local reason="${1:-snapshot}"
  local snap_url="${2:-$(treed_cam_snapshot_url)}"
  local interval="${TREED_CAM_SNAPSHOT_WARN_INTERVAL_SEC:-300}"
  local state_file="${TREED_CAM_SNAPSHOT_WARN_STATE:-/tmp/treed_cam_snapshot_warn.state}"
  local now last_ts suppressed

  if [[ ! "${interval}" =~ ^[0-9]+$ ]]; then
    interval=300
  fi

  now="$(date +%s)"
  last_ts=0
  suppressed=0

  if [[ -f "${state_file}" ]]; then
    read -r last_ts suppressed < "${state_file}" 2>/dev/null || true
  fi

  if (( now - last_ts >= interval )); then
    if (( suppressed > 0 )); then
      echo "treed-cam: snapshot fetch failed (${reason}); url=${snap_url}; suppressed_failures=${suppressed}" >&2
    else
      echo "treed-cam: snapshot fetch failed (${reason}); url=${snap_url}" >&2
    fi
    printf '%s %s\n' "${now}" "0" > "${state_file}" 2>/dev/null || true
  else
    suppressed=$((suppressed + 1))
    printf '%s %s\n' "${last_ts}" "${suppressed}" > "${state_file}" 2>/dev/null || true
  fi
}
