#!/bin/bash
set -euo pipefail

# ==========================================
# ШАГ LOADER: CROWSNEST WEBCAM
# ==========================================
# Назначение:
# - Настраивает crowsnest и moonraker webcam-фрагмент для runtime.
# - Применяет безопасные fallback-проверки устройства камеры.
# Контур:
# - required при TREED_CAMERA_REQUIRED=1, иначе best-effort.

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
LIB_DIR="${REPO_DIR}/loader/lib"
# Блок 1: Библиотеки и базовая инициализация.
source "${LIB_DIR}/common.sh"

# Блок 2: Старт шага и расчет пользовательских путей.
log_info "Step crowsnest-webcam: fixed 1920x1080@10 for single USB cam"

PI_USER="${PI_USER:-pi}"
PI_HOME="${PI_HOME:-/home/${PI_USER}}"
if ! grp="$(pi_primary_group "${PI_USER}")"; then
  exit 1
fi

CONFIG_DIR="${PI_HOME}/printer_data/config"
DATA_DIR="${PI_HOME}/printer_data"
CROWSNEST_CONF="${CONFIG_DIR}/crowsnest.conf"
MOONRAKER_CONF="${CONFIG_DIR}/moonraker.conf"
MOONRAKER_DIR="${CONFIG_DIR}/moonraker"
MOONRAKER_GENERATED_DIR="${MOONRAKER_DIR}/generated"
MOONRAKER_WEBCAM_FRAGMENT="${MOONRAKER_GENERATED_DIR}/50-webcam-treed.conf"
MOONRAKER_ASVC="${DATA_DIR}/moonraker.asvc"

CAM_DEVICE_DEFAULT="/dev/video0"
CAM_DEVICE="${CAM_DEVICE:-}"
CAM_REQUIRED="${TREED_CAMERA_REQUIRED:-0}"
# Дефолтный режим камеры:
# - после перевода MCU на UART USB-шина разгружена, можно поднять качество потока.
# - при признаках нестабильности верните 640x480@10 через env без правки кода.
CAM_RESOLUTION="${TREED_CAM_RESOLUTION:-1920x1080}"
CAM_FPS="${TREED_CAM_FPS:-10}"
CAM_PORT="8080"

# Блок 3: Подготовка каталогов конфигурации.
ensure_dir "${CONFIG_DIR}"
ensure_dir "${MOONRAKER_GENERATED_DIR}"

# Блок 4: Вспомогательные функции выбора камеры и деплоя конфигов.
resolve_cam_device() {
  local byid_dir="/dev/v4l/by-id"
  local allow_video0_fallback="${CAM_ALLOW_VIDEO0_FALLBACK:-0}"
  local -a candidates=()
  local candidate=""

  # Приоритет выбора: явный CAM_DEVICE -> уникальный by-id -> опциональный /dev/video0.
  if [ -n "${CAM_DEVICE}" ]; then
    if [ -e "${CAM_DEVICE}" ] || [ -L "${CAM_DEVICE}" ]; then
      log_info "Using CAM_DEVICE override: ${CAM_DEVICE}"
      return 0
    fi
    log_error "CAM_DEVICE override '${CAM_DEVICE}' not found"
    return 2
  fi

  if [ -d "${byid_dir}" ]; then
    mapfile -t candidates < <(find "${byid_dir}" -maxdepth 1 -type l -name '*-video-index0' 2>/dev/null | sort)
    if [ "${#candidates[@]}" -eq 1 ]; then
      CAM_DEVICE="${candidates[0]}"
      log_info "Auto-selected camera device via /dev/v4l/by-id: ${CAM_DEVICE}"
      return 0
    fi
    if [ "${#candidates[@]}" -gt 1 ]; then
      log_warn "Multiple /dev/v4l/by-id/*-video-index0 cameras detected; set CAM_DEVICE explicitly"
      for candidate in "${candidates[@]}"; do
        log_warn "camera candidate: ${candidate}"
      done
      return 1
    fi

    mapfile -t candidates < <(find "${byid_dir}" -maxdepth 1 -type l 2>/dev/null | sort)
    if [ "${#candidates[@]}" -eq 1 ]; then
      CAM_DEVICE="${candidates[0]}"
      log_info "Auto-selected camera device via /dev/v4l/by-id: ${CAM_DEVICE}"
      return 0
    fi
    if [ "${#candidates[@]}" -gt 1 ]; then
      log_warn "Multiple cameras detected in /dev/v4l/by-id; set CAM_DEVICE explicitly"
      for candidate in "${candidates[@]}"; do
        log_warn "camera candidate: ${candidate}"
      done
      return 1
    fi
  fi

  if [ "${allow_video0_fallback}" = "1" ]; then
    CAM_DEVICE="${CAM_DEVICE_DEFAULT}"
    if [ -e "${CAM_DEVICE}" ] || [ -L "${CAM_DEVICE}" ]; then
      log_warn "No unique /dev/v4l/by-id camera found; using fallback ${CAM_DEVICE}"
      return 0
    fi
    log_warn "CAM_ALLOW_VIDEO0_FALLBACK=1 but fallback device is missing: ${CAM_DEVICE}"
    return 1
  fi

  log_warn "Cannot resolve unique camera in /dev/v4l/by-id; set CAM_DEVICE or CAM_ALLOW_VIDEO0_FALLBACK=1"
  return 1
}

skip_webcam_deploy() {
  local removed_fragment=0

  # Если камера не определена, очищаем ранее развернутый контур и корректно выходим.
  log_warn "crowsnest-webcam: camera is not resolved, skipping webcam deployment (set TREED_CAMERA_REQUIRED=1 for fail-fast)"

  if [ -f "${MOONRAKER_WEBCAM_FRAGMENT}" ]; then
    rm -f "${MOONRAKER_WEBCAM_FRAGMENT}"
    removed_fragment=1
    log_info "Removed stale Moonraker webcam fragment: ${MOONRAKER_WEBCAM_FRAGMENT}"
  fi

  if systemctl cat crowsnest.service >/dev/null 2>&1; then
    systemctl stop crowsnest.service >/dev/null 2>&1 || true
    log_info "Stopped crowsnest.service (no camera deployed)"
  fi

  if [ "${removed_fragment}" -eq 1 ] && systemctl cat moonraker.service >/dev/null 2>&1; then
    systemctl restart moonraker.service || true
    log_info "Restarted moonraker.service after removing webcam fragment"
  fi

  log_info "crowsnest-webcam: SKIPPED"
}

ensure_moonraker_generated_include() {
  # generated/*.conf обязателен, иначе фрагмент вебкамеры не будет подхвачен Moonraker.
  if [[ ! -f "${MOONRAKER_CONF}" ]]; then
    log_warn "moonraker.conf not found at ${MOONRAKER_CONF}; generated webcam fragment may be ignored"
    return 0
  fi

  if grep -qE '^\[include[[:space:]]+moonraker/generated/\*\.conf\][[:space:]]*$' "${MOONRAKER_CONF}"; then
    return 0
  fi

  log_warn "moonraker.conf is missing include [include moonraker/generated/*.conf]"
}

write_moonraker_webcam_fragment() {
  log_info "Writing Moonraker webcam fragment -> ${MOONRAKER_WEBCAM_FRAGMENT}"
  cat > "${MOONRAKER_WEBCAM_FRAGMENT}" <<'EOF'
#### treed-generated: crowsnest-webcam
[webcam treed]
location: printer
service: mjpegstreamer
target_fps: 15
target_fps_idle: 5
stream_url: /webcam/?action=stream
snapshot_url: /webcam/?action=snapshot
enabled: True
icon: mdiWebcam
EOF
}

write_crowsnest_conf() {
  log_info "Writing crowsnest.conf -> ${CROWSNEST_CONF}"
  cat > "${CROWSNEST_CONF}" <<EOF
#### treed-managed: crowsnest-webcam
#### одиночная USB-камера, 1920x1080@10 (override: TREED_CAM_RESOLUTION/TREED_CAM_FPS)

[crowsnest]
log_path: ${PI_HOME}/printer_data/logs/crowsnest.log

[cam 1]
mode: ustreamer
port: ${CAM_PORT}
device: ${CAM_DEVICE}
resolution: ${CAM_RESOLUTION}
max_fps: ${CAM_FPS}
EOF
}

ensure_crowsnest_allowed_service() {
  if [ ! -f "${MOONRAKER_ASVC}" ]; then
    log_warn "moonraker.asvc not found at ${MOONRAKER_ASVC}; cannot whitelist crowsnest yet"
    return 0
  fi

  if grep -qE '^[[:space:]]*crowsnest([.]service)?[[:space:]]*$' "${MOONRAKER_ASVC}"; then
    log_info "moonraker.asvc already allows crowsnest"
    return 0
  fi

  printf 'crowsnest\n' >> "${MOONRAKER_ASVC}"
  log_info "Added crowsnest to ${MOONRAKER_ASVC}"
}

apply_services() {
  # Применяем изменения через перезапуск crowsnest и moonraker с коротким readiness-циклом API.
  if systemctl cat crowsnest.service >/dev/null 2>&1; then
    log_info "Enabling and restarting crowsnest"
    systemctl enable crowsnest.service >/dev/null 2>&1 || true
    systemctl restart crowsnest.service
  else
    log_warn "crowsnest.service not found; skipping restart"
  fi

  if systemctl cat moonraker.service >/dev/null 2>&1; then
    log_info "Restarting moonraker"
    systemctl restart moonraker.service
    if command -v curl >/dev/null 2>&1; then
      local retries="${MOONRAKER_READY_RETRIES:-30}"
      local code=""
      local attempt
      for attempt in $(seq 1 "${retries}"); do
        code="$(curl -m 2 -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:7125/server/info" || true)"
        if [ "${code}" = "200" ]; then
          log_info "Moonraker API is ready on 127.0.0.1:7125"
          return 0
        fi
        sleep 1
      done
      log_warn "Moonraker API not ready after ${retries}s (last_http=${code:-n/a})"
    fi
  else
    log_warn "moonraker.service not found; skipping restart"
  fi
}

# Блок 5: Основной сценарий — resolve камеры, деплой конфигов и рестарты сервисов.
ensure_moonraker_generated_include
if resolve_cam_device; then
  :
else
  rc=$?
  if [ "${rc}" -eq 2 ]; then
    log_error "crowsnest-webcam: invalid CAM_DEVICE override, aborting"
    exit 1
  fi
  if [ "${CAM_REQUIRED}" = "1" ]; then
    log_error "crowsnest-webcam: camera is required (TREED_CAMERA_REQUIRED=1), aborting"
    exit 1
  fi
  skip_webcam_deploy
  exit 0
fi
write_moonraker_webcam_fragment
write_crowsnest_conf
ensure_crowsnest_allowed_service
chown "${PI_USER}:${grp}" "${CROWSNEST_CONF}" || true
chown "${PI_USER}:${grp}" "${MOONRAKER_WEBCAM_FRAGMENT}" || true
chown "${PI_USER}:${grp}" "${MOONRAKER_ASVC}" || true

apply_services

log_info "crowsnest-webcam: DONE (device=${CAM_DEVICE}, res=${CAM_RESOLUTION}, fps=${CAM_FPS})"
