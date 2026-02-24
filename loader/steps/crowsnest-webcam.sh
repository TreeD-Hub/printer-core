#!/bin/bash
set -euo pipefail

# ==========================================
# ШАГ LOADER: CROWSNEST WEBCAM
# ==========================================
# Назначение:
# - Настраивает raw-камеру через crowsnest/ustreamer и zoom-sidecar контур (crop/scale).
# - Генерирует Moonraker webcam-фрагмент, nginx proxy-path и runtime zoom-конфиг.
# Контур:
# - required при TREED_CAMERA_REQUIRED=1, иначе best-effort.

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
LIB_DIR="${REPO_DIR}/loader/lib"
# Блок 1: Библиотеки и базовая инициализация.
source "${LIB_DIR}/common.sh"

# Блок 2: Старт шага и расчет пользовательских путей.
log_info "Step crowsnest-webcam: 1080p capture + zoom sidecar"

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

TREED_CAM_ROOT="${PI_HOME}/treed/cam"
TREED_CAM_BIN_DIR="${TREED_CAM_ROOT}/bin"
TREED_CAM_CONFIG_DIR="${TREED_CAM_ROOT}/config"
TREED_CAM_LOGS_DIR="${TREED_CAM_ROOT}/logs"
ZOOM_PROFILES_ENV="${TREED_CAM_CONFIG_DIR}/zoom_profiles.env"
ZOOM_ACTIVE_ENV="${TREED_CAM_CONFIG_DIR}/zoom_active.env"

TREED_CAM_ZOOM_UNIT="/etc/systemd/system/treed-cam-zoom.service"
TREED_CAM_ZOOM_NGINX_SITE_FILE_OVERRIDE="${TREED_CAM_ZOOM_NGINX_SITE_FILE:-}"
TREED_CAM_ZOOM_NGINX_SITE_FILE=""
TREED_CAM_ZOOM_NGINX_MARK_BEGIN="# >>> TREED-CAM-ZOOM (managed by crowsnest-webcam.sh)"
TREED_CAM_ZOOM_NGINX_MARK_END="# <<< TREED-CAM-ZOOM (managed by crowsnest-webcam.sh)"

CAM_DEVICE_DEFAULT="/dev/video0"
CAM_DEVICE="${CAM_DEVICE:-}"
CAM_REQUIRED="${TREED_CAMERA_REQUIRED:-0}"
# Дефолтный режим камеры:
# - zoom-профили рассчитаны на 1080p raw capture;
# - при снижении разрешения вручную ROI-профили могут стать невалидными.
CAM_RESOLUTION="${TREED_CAM_RESOLUTION:-1920x1080}"
CAM_FPS="${TREED_CAM_FPS:-10}"
CAM_PORT="8080"

# Блок 2.1: Константы zoom-sidecar и профилей (единый контракт UI + runtime snapshots).
CAM_ZOOM_PORT="${TREED_CAM_ZOOM_PORT:-8081}"
CAM_ZOOM_OUTPUT_WIDTH="${TREED_CAM_ZOOM_OUTPUT_WIDTH:-1280}"
CAM_ZOOM_OUTPUT_HEIGHT="${TREED_CAM_ZOOM_OUTPUT_HEIGHT:-720}"
CAM_ZOOM_DEFAULT_PROFILE="${TREED_CAM_ZOOM_DEFAULT_PROFILE:-medium}"
CAM_ZOOM_STREAM_PATH="/webcam-treed/stream.mjpg"
CAM_ZOOM_SNAPSHOT_PATH="/webcam-treed/snapshot.jpg"
CAM_ZOOM_SNAPSHOT_FILE="/run/treed-cam-zoom/snapshot.jpg"
CAM_ZOOM_PROFILES_LIST="wide medium close"
CAM_ZOOM_PROFILE_WIDE="0,0,1920,1080"
CAM_ZOOM_PROFILE_MEDIUM="240,180,1440,810"
CAM_ZOOM_PROFILE_CLOSE="480,270,960,540"

# Блок 3: Подготовка каталогов конфигурации.
ensure_dir "${CONFIG_DIR}"
ensure_dir "${MOONRAKER_GENERATED_DIR}"
ensure_dir "${TREED_CAM_CONFIG_DIR}"
ensure_dir "${TREED_CAM_LOGS_DIR}"

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

normalize_zoom_default_profile() {
  case "${CAM_ZOOM_DEFAULT_PROFILE}" in
    wide|medium|close)
      return 0
      ;;
    *)
      log_warn "Invalid TREED_CAM_ZOOM_DEFAULT_PROFILE='${CAM_ZOOM_DEFAULT_PROFILE}', using 'medium'"
      CAM_ZOOM_DEFAULT_PROFILE="medium"
      return 0
      ;;
  esac
}

warn_if_zoom_capture_resolution_nonstandard() {
  if [ "${CAM_RESOLUTION}" != "1920x1080" ]; then
    log_warn "TREED_CAM_RESOLUTION=${CAM_RESOLUTION} differs from 1920x1080; default zoom ROI profiles are tuned for 1920x1080 raw capture"
  fi
}

skip_webcam_deploy() {
  local removed_fragment=0

  # Если камера не определена, очищаем пользовательский webcam-фрагмент и останавливаем camera-сервисы.
  log_warn "crowsnest-webcam: camera is not resolved, skipping webcam deployment (set TREED_CAMERA_REQUIRED=1 for fail-fast)"

  if [ -f "${MOONRAKER_WEBCAM_FRAGMENT}" ]; then
    rm -f "${MOONRAKER_WEBCAM_FRAGMENT}"
    removed_fragment=1
    log_info "Removed stale Moonraker webcam fragment: ${MOONRAKER_WEBCAM_FRAGMENT}"
  fi

  remove_treed_cam_zoom_nginx_block || true

  if systemctl cat treed-cam-zoom.service >/dev/null 2>&1; then
    systemctl stop treed-cam-zoom.service >/dev/null 2>&1 || true
    log_info "Stopped treed-cam-zoom.service (no camera deployed)"
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
  cat > "${MOONRAKER_WEBCAM_FRAGMENT}" <<EOF
#### treed-generated: crowsnest-webcam
[webcam treed]
location: printer
service: mjpegstreamer
target_fps: 15
target_fps_idle: 5
stream_url: ${CAM_ZOOM_STREAM_PATH}
snapshot_url: ${CAM_ZOOM_SNAPSHOT_PATH}
enabled: True
icon: mdiWebcam
EOF
}

write_crowsnest_conf() {
  log_info "Writing crowsnest.conf -> ${CROWSNEST_CONF}"
  cat > "${CROWSNEST_CONF}" <<EOF
#### treed-managed: crowsnest-webcam
#### raw USB-камера для zoom-sidecar (override: TREED_CAM_RESOLUTION/TREED_CAM_FPS)

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

write_zoom_profiles_env() {
  log_info "Writing zoom profiles env -> ${ZOOM_PROFILES_ENV}"
  cat > "${ZOOM_PROFILES_ENV}" <<EOF
#### treed-generated: crowsnest-webcam (single source of truth for camera zoom stack)
TREED_CAM_RAW_STREAM_URL=http://127.0.0.1:${CAM_PORT}/?action=stream
TREED_CAM_RAW_SNAPSHOT_URL=http://127.0.0.1:${CAM_PORT}/?action=snapshot
TREED_CAM_ZOOM_STREAM_URL_PUBLIC=${CAM_ZOOM_STREAM_PATH}
TREED_CAM_ZOOM_SNAPSHOT_URL_PUBLIC=${CAM_ZOOM_SNAPSHOT_PATH}
TREED_CAM_ZOOM_STREAM_URL_LOCAL=http://127.0.0.1:${CAM_ZOOM_PORT}/stream.mjpg
TREED_CAM_ZOOM_SNAPSHOT_URL_LOCAL=http://127.0.0.1${CAM_ZOOM_SNAPSHOT_PATH}
TREED_CAM_ZOOM_SNAPSHOT_FILE=${CAM_ZOOM_SNAPSHOT_FILE}
TREED_CAM_ZOOM_OUTPUT_WIDTH=${CAM_ZOOM_OUTPUT_WIDTH}
TREED_CAM_ZOOM_OUTPUT_HEIGHT=${CAM_ZOOM_OUTPUT_HEIGHT}
TREED_CAM_ZOOM_PROFILES="${CAM_ZOOM_PROFILES_LIST}"
TREED_CAM_ZOOM_PROFILE_DEFAULT=${CAM_ZOOM_DEFAULT_PROFILE}
TREED_CAM_ZOOM_PROFILE_WIDE=${CAM_ZOOM_PROFILE_WIDE}
TREED_CAM_ZOOM_PROFILE_MEDIUM=${CAM_ZOOM_PROFILE_MEDIUM}
TREED_CAM_ZOOM_PROFILE_CLOSE=${CAM_ZOOM_PROFILE_CLOSE}
EOF
}

write_zoom_active_env_if_missing() {
  if [ -f "${ZOOM_ACTIVE_ENV}" ]; then
    log_info "Keeping existing zoom active profile -> ${ZOOM_ACTIVE_ENV}"
    return 0
  fi
  log_info "Writing default zoom active profile -> ${ZOOM_ACTIVE_ENV}"
  cat > "${ZOOM_ACTIVE_ENV}" <<EOF
TREED_CAM_ZOOM_PROFILE=${CAM_ZOOM_DEFAULT_PROFILE}
EOF
}

write_treed_cam_zoom_service_unit() {
  log_info "Writing systemd unit -> ${TREED_CAM_ZOOM_UNIT}"
  cat > "${TREED_CAM_ZOOM_UNIT}" <<EOF
[Unit]
Description=TreeD Camera Zoom Sidecar
After=network-online.target crowsnest.service
Wants=network-online.target

[Service]
Type=simple
User=${PI_USER}
Group=${grp}
WorkingDirectory=${TREED_CAM_ROOT}
RuntimeDirectory=treed-cam-zoom
RuntimeDirectoryMode=0755
ExecStartPre=/usr/bin/test -x ${TREED_CAM_BIN_DIR}/zoom-sidecar.sh
ExecStart=${TREED_CAM_BIN_DIR}/zoom-sidecar.sh
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF
}

resolve_treed_cam_zoom_nginx_site_file() {
  local candidate=""
  local resolved_candidate=""
  local -a hits=()

  if [ -n "${TREED_CAM_ZOOM_NGINX_SITE_FILE_OVERRIDE}" ]; then
    if [ -f "${TREED_CAM_ZOOM_NGINX_SITE_FILE_OVERRIDE}" ]; then
      TREED_CAM_ZOOM_NGINX_SITE_FILE="${TREED_CAM_ZOOM_NGINX_SITE_FILE_OVERRIDE}"
      log_info "Using nginx server file override: ${TREED_CAM_ZOOM_NGINX_SITE_FILE}"
      return 0
    fi
    log_error "TREED_CAM_ZOOM_NGINX_SITE_FILE override not found: ${TREED_CAM_ZOOM_NGINX_SITE_FILE_OVERRIDE}"
    return 1
  fi

  while IFS= read -r candidate; do
    [ -n "${candidate}" ] || continue
    if resolved_candidate="$(readlink -f "${candidate}" 2>/dev/null)"; then
      candidate="${resolved_candidate}"
    fi
    if printf '%s\n' "${hits[@]:-}" | grep -Fxq "${candidate}"; then
      continue
    fi
    hits+=("${candidate}")
  done < <(
    grep -RIlE 'location[[:space:]]+/?webcam([[:space:]]|/|$)|proxy_pass[[:space:]]+http://127[.]0[.]0[.]1:8080' \
      /etc/nginx/sites-enabled /etc/nginx/sites-available 2>/dev/null | sort -u
  )

  if [ "${#hits[@]}" -eq 1 ]; then
    TREED_CAM_ZOOM_NGINX_SITE_FILE="${hits[0]}"
    log_info "Detected nginx server file for webcam routes: ${TREED_CAM_ZOOM_NGINX_SITE_FILE}"
    return 0
  fi

  if [ "${#hits[@]}" -gt 1 ]; then
    log_warn "Multiple nginx server files match webcam route; set TREED_CAM_ZOOM_NGINX_SITE_FILE explicitly"
    for candidate in "${hits[@]}"; do
      log_warn "nginx webcam route candidate: ${candidate}"
    done
    return 1
  fi

  log_warn "Cannot detect nginx server file with /webcam route; set TREED_CAM_ZOOM_NGINX_SITE_FILE explicitly"
  return 1
}

render_treed_cam_zoom_nginx_block() {
  cat <<EOF
${TREED_CAM_ZOOM_NGINX_MARK_BEGIN}
location = ${CAM_ZOOM_STREAM_PATH} {
    proxy_http_version 1.1;
    proxy_buffering off;
    proxy_request_buffering off;
    proxy_read_timeout 3600s;
    add_header Cache-Control "no-store" always;
    proxy_pass http://127.0.0.1:${CAM_ZOOM_PORT}/stream.mjpg;
}

location = ${CAM_ZOOM_SNAPSHOT_PATH} {
    alias ${CAM_ZOOM_SNAPSHOT_FILE};
    add_header Cache-Control "no-store" always;
    add_header Pragma "no-cache" always;
    expires -1;
}
${TREED_CAM_ZOOM_NGINX_MARK_END}
EOF
}

patch_treed_cam_zoom_nginx_block_in_file() {
  local target_file="$1"
  local backup_file="$2"
  local block_tmp="$3"
  local py_rc=0

  if python3 - "${target_file}" "${block_tmp}" "${TREED_CAM_ZOOM_NGINX_MARK_BEGIN}" "${TREED_CAM_ZOOM_NGINX_MARK_END}" <<'PY'
import pathlib
import re
import sys

target = pathlib.Path(sys.argv[1])
block_path = pathlib.Path(sys.argv[2])
mark_begin = sys.argv[3]
mark_end = sys.argv[4]

text = target.read_text(encoding="utf-8")
block = block_path.read_text(encoding="utf-8").rstrip("\n")

pattern = re.compile(re.escape(mark_begin) + r".*?" + re.escape(mark_end) + r"\n?", re.S)
text = pattern.sub("", text)

anchor = re.compile(r"^[ \t]*location[ \t]+=?[ \t]*/webcam(?:/|\b)", re.M)
m = anchor.search(text)
if m is None:
    sys.exit(42)

insert_pos = m.start()
if insert_pos > 0 and text[insert_pos - 1] != "\n":
    block_text = "\n" + block + "\n"
else:
    block_text = block + "\n"

new_text = text[:insert_pos] + block_text + text[insert_pos:]
target.write_text(new_text, encoding="utf-8")
PY
  then
    py_rc=0
  else
    py_rc=$?
  fi

  case "${py_rc}" in
    0)
      return 0
      ;;
    42)
      log_error "nginx webcam route anchor (/webcam) not found in ${target_file}"
      cp -a "${backup_file}" "${target_file}"
      return 1
      ;;
    *)
      log_error "failed to patch nginx server file ${target_file} (python rc=${py_rc})"
      cp -a "${backup_file}" "${target_file}"
      return 1
      ;;
  esac
}

write_treed_cam_zoom_nginx_snippet() {
  local target_file=""
  local backup=""
  local block_tmp=""

  if ! command -v nginx >/dev/null 2>&1; then
    log_error "nginx not found; zoom webcam proxy path ${CAM_ZOOM_STREAM_PATH} requires nginx"
    return 1
  fi

  if ! resolve_treed_cam_zoom_nginx_site_file; then
    return 1
  fi
  target_file="${TREED_CAM_ZOOM_NGINX_SITE_FILE}"

  backup="$(mktemp "/tmp/treed_cam_zoom_nginx_site_backup_XXXXXX.conf")"
  cp -a "${target_file}" "${backup}"
  block_tmp="$(mktemp "/tmp/treed_cam_zoom_block_XXXXXX.conf")"
  render_treed_cam_zoom_nginx_block > "${block_tmp}"

  log_info "Patching nginx webcam routes in ${target_file}"
  if ! patch_treed_cam_zoom_nginx_block_in_file "${target_file}" "${backup}" "${block_tmp}"; then
    rm -f "${backup}" "${block_tmp}"
    return 1
  fi

  if nginx -t >/dev/null 2>&1; then
    rm -f "${backup}" "${block_tmp}"
    return 0
  fi

  log_error "nginx config test failed after patching ${target_file}; rolling back"
  cp -a "${backup}" "${target_file}"
  nginx -t >/dev/null 2>&1 || true
  rm -f "${backup}" "${block_tmp}"
  return 1
}

remove_treed_cam_zoom_nginx_block() {
  local target_file=""
  local backup=""

  if ! command -v nginx >/dev/null 2>&1; then
    return 0
  fi

  if resolve_treed_cam_zoom_nginx_site_file; then
    target_file="${TREED_CAM_ZOOM_NGINX_SITE_FILE}"
  else
    return 0
  fi

  if ! grep -Fq "${TREED_CAM_ZOOM_NGINX_MARK_BEGIN}" "${target_file}" 2>/dev/null; then
    return 0
  fi

  backup="$(mktemp "/tmp/treed_cam_zoom_nginx_site_remove_backup_XXXXXX.conf")"
  cp -a "${target_file}" "${backup}"
  if ! python3 - "${target_file}" "${TREED_CAM_ZOOM_NGINX_MARK_BEGIN}" "${TREED_CAM_ZOOM_NGINX_MARK_END}" <<'PY'
import pathlib
import re
import sys
target = pathlib.Path(sys.argv[1])
mark_begin = sys.argv[2]
mark_end = sys.argv[3]
text = target.read_text(encoding="utf-8")
pattern = re.compile(re.escape(mark_begin) + r".*?" + re.escape(mark_end) + r"\n?", re.S)
target.write_text(pattern.sub("", text), encoding="utf-8")
PY
  then
    cp -a "${backup}" "${target_file}"
    rm -f "${backup}"
    return 1
  fi

  if nginx -t >/dev/null 2>&1; then
    systemctl reload nginx.service 2>/dev/null || systemctl restart nginx.service || true
    rm -f "${backup}"
    log_info "Removed managed nginx zoom block from ${target_file}"
    return 0
  fi

  cp -a "${backup}" "${target_file}"
  nginx -t >/dev/null 2>&1 || true
  rm -f "${backup}"
  log_warn "Failed to remove nginx zoom block cleanly; restored previous nginx config"
  return 1
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

http_ready_check() {
  local check_name="$1"
  local url="$2"
  local retries="${3:-3}"
  local attempt code tmp

  if ! command -v curl >/dev/null 2>&1; then
    log_warn "${check_name}: curl not found, readiness check skipped"
    return 0
  fi

  tmp="$(mktemp "/tmp/treed_cam_webcam_check_XXXXXX.bin")"
  code=""
  for attempt in $(seq 1 "${retries}"); do
    code="$(curl -m 3 -sS -o "${tmp}" -w '%{http_code}' "${url}" || true)"
    if [ "${code}" = "200" ] && [ -s "${tmp}" ]; then
      rm -f "${tmp}"
      log_info "${check_name}: ready"
      return 0
    fi
    sleep 1
  done
  rm -f "${tmp}"
  log_warn "${check_name}: not ready (http=${code:-n/a})"
  return 1
}

apply_camera_transport_services() {
  local cam_http_retries="${TREED_CAM_HTTP_RETRIES:-3}"
  local can_start_zoom=0

  # Применяем транспортный контур камеры: nginx proxy, raw crowsnest и zoom-sidecar.
  if command -v nginx >/dev/null 2>&1; then
    if nginx -t >/dev/null 2>&1; then
      log_info "Reloading nginx"
      systemctl reload nginx.service 2>/dev/null || systemctl restart nginx.service
    else
      log_error "nginx -t failed; aborting camera deploy apply"
      return 1
    fi
  else
    log_warn "nginx not found; webcam proxy path ${CAM_ZOOM_STREAM_PATH} may be unavailable"
  fi

  if systemctl cat crowsnest.service >/dev/null 2>&1; then
    log_info "Enabling and restarting crowsnest"
    systemctl enable crowsnest.service >/dev/null 2>&1 || true
    systemctl restart crowsnest.service
  else
    log_warn "crowsnest.service not found; skipping restart"
  fi

  systemctl daemon-reload >/dev/null 2>&1 || true
  if systemctl cat treed-cam-zoom.service >/dev/null 2>&1; then
    systemctl enable treed-cam-zoom.service >/dev/null 2>&1 || true
    if [ -x "${TREED_CAM_BIN_DIR}/zoom-sidecar.sh" ]; then
      log_info "Restarting treed-cam-zoom"
      systemctl restart treed-cam-zoom.service
      can_start_zoom=1
    else
      log_info "treed-cam-zoom start deferred: ${TREED_CAM_BIN_DIR}/zoom-sidecar.sh is not deployed yet (step treed-cam runs later)"
    fi
  fi

  http_ready_check "raw webcam snapshot (:${CAM_PORT})" "http://127.0.0.1:${CAM_PORT}/?action=snapshot" "${cam_http_retries}" || true
  if [ "${can_start_zoom}" -eq 1 ]; then
    http_ready_check "zoom webcam snapshot (${CAM_ZOOM_SNAPSHOT_PATH})" "http://127.0.0.1${CAM_ZOOM_SNAPSHOT_PATH}" "${cam_http_retries}" || true
  fi
}

restart_moonraker_after_webcam_fragment() {
  # После успешной подготовки camera transport переключаем Moonraker на новый webcam endpoint.
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
          break
        fi
        sleep 1
      done
      if [ "${code:-}" != "200" ]; then
        log_warn "Moonraker API not ready after ${retries}s (last_http=${code:-n/a})"
      fi
    fi
  else
    log_warn "moonraker.service not found; skipping restart"
  fi
}

# Блок 5: Основной сценарий — resolve камеры, деплой конфигов и рестарты сервисов.
normalize_zoom_default_profile
warn_if_zoom_capture_resolution_nonstandard
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

write_crowsnest_conf
write_zoom_profiles_env
write_zoom_active_env_if_missing
write_treed_cam_zoom_service_unit
write_treed_cam_zoom_nginx_snippet
ensure_crowsnest_allowed_service

chown "${PI_USER}:${grp}" "${CROWSNEST_CONF}" || true
chown "${PI_USER}:${grp}" "${ZOOM_PROFILES_ENV}" || true
chown "${PI_USER}:${grp}" "${ZOOM_ACTIVE_ENV}" || true
chown "${PI_USER}:${grp}" "${MOONRAKER_ASVC}" || true

apply_camera_transport_services

write_moonraker_webcam_fragment
chown "${PI_USER}:${grp}" "${MOONRAKER_WEBCAM_FRAGMENT}" || true
restart_moonraker_after_webcam_fragment

log_info "crowsnest-webcam: DONE (device=${CAM_DEVICE}, res=${CAM_RESOLUTION}, fps=${CAM_FPS}, zoom=${CAM_ZOOM_DEFAULT_PROFILE}, out=${CAM_ZOOM_OUTPUT_WIDTH}x${CAM_ZOOM_OUTPUT_HEIGHT})"
