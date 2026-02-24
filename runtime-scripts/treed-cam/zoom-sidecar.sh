#!/bin/bash
set -euo pipefail

# ==========================================
# RUNTIME SCRIPT: TREED CAM ZOOM SIDECAR
# ==========================================
# Назначение:
# - Поднимает backend-sidecar с crop/scale поверх raw crowsnest MJPEG-потока.
# - Обновляет публичный stream и snapshot в едином zoom-профиле.
# Контур:
# - long-running wrapper для systemd unit `treed-cam-zoom.service`;
# - hot-reload профиля без sudo/systemctl через слежение за env-файлами.

# Блок 1: Подключение общего camera env-helper и базовые константы wrapper.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/cam_env.sh"

RAW_WAIT_RETRIES="${TREED_CAM_ZOOM_RAW_WAIT_RETRIES:-120}"
RAW_WAIT_SLEEP_SEC="${TREED_CAM_ZOOM_RAW_WAIT_SLEEP_SEC:-1}"
CONFIG_POLL_SEC="${TREED_CAM_ZOOM_CONFIG_POLL_SEC:-1}"
SNAPSHOT_REFRESH_FPS="${TREED_CAM_ZOOM_SNAPSHOT_REFRESH_FPS:-1}"
FFMPEG_LOGLEVEL="${TREED_CAM_ZOOM_FFMPEG_LOGLEVEL:-error}"
SCALE_FLAGS="${TREED_CAM_ZOOM_SCALE_FLAGS:-bicubic}"
FFMPEG_STREAM_PID=""
FFMPEG_SNAPSHOT_PID=""

# Блок 2: Локальный лог и service-cleanup.
log_zoom() {
  printf '[treed-cam-zoom] %s\n' "$*" >&2
}

stop_children() {
  local pid=""
  for pid in "${FFMPEG_STREAM_PID:-}" "${FFMPEG_SNAPSHOT_PID:-}"; do
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
      kill "${pid}" 2>/dev/null || true
    fi
  done

  for pid in "${FFMPEG_STREAM_PID:-}" "${FFMPEG_SNAPSHOT_PID:-}"; do
    if [[ -n "${pid}" ]]; then
      wait "${pid}" 2>/dev/null || true
    fi
  done

  FFMPEG_STREAM_PID=""
  FFMPEG_SNAPSHOT_PID=""
}

cleanup() {
  stop_children
}
trap cleanup EXIT INT TERM

# Блок 3: Проверка доступности raw upstream (crowsnest) перед стартом ffmpeg.
wait_for_raw_upstream() {
  local attempt=""
  local url=""
  url="${TREED_CAM_RAW_SNAPSHOT_URL:-}"
  if [[ -z "${url}" ]]; then
    log_zoom "raw snapshot url is empty"
    return 1
  fi
  if ! command -v curl >/dev/null 2>&1; then
    log_zoom "curl not found"
    return 1
  fi

  for attempt in $(seq 1 "${RAW_WAIT_RETRIES}"); do
    if curl -m 2 -fsS -o /dev/null "${url}" >/dev/null 2>&1; then
      return 0
    fi
    sleep "${RAW_WAIT_SLEEP_SEC}"
  done

  log_zoom "raw upstream is not ready: ${url}"
  return 1
}

# Блок 4: Формирование текущего сигнатурного состояния (active profile + mtime конфигов).
config_signature() {
  local profiles_mtime active_mtime
  profiles_mtime="$(stat -c '%Y' "${TREED_CAM_ZOOM_PROFILES_ENV}" 2>/dev/null || printf '0')"
  active_mtime="$(stat -c '%Y' "${TREED_CAM_ZOOM_ACTIVE_ENV}" 2>/dev/null || printf '0')"
  printf '%s|%s|%s|%s|%s|%s|%s|%s|%s\n' \
    "${profiles_mtime}" \
    "${active_mtime}" \
    "${TREED_CAM_ZOOM_PROFILE_ACTIVE:-}" \
    "${TREED_CAM_ROI_X:-}" \
    "${TREED_CAM_ROI_Y:-}" \
    "${TREED_CAM_ROI_W:-}" \
    "${TREED_CAM_ROI_H:-}" \
    "${TREED_CAM_ZOOM_OUTPUT_WIDTH:-}" \
    "${TREED_CAM_ZOOM_OUTPUT_HEIGHT:-}"
}

# Блок 5: Запуск ffmpeg-процессов текущего профиля (stream + snapshot).
snapshot_loop() {
  local vf="$1"
  local snapshot_dir snapshot_tmp fail_count snapshot_sleep
  snapshot_dir="$(dirname "${TREED_CAM_ZOOM_SNAPSHOT_FILE}")"
  snapshot_tmp="${TREED_CAM_ZOOM_SNAPSHOT_FILE}.tmp.jpg"
  fail_count=0
  snapshot_sleep="1"

  if [[ "${SNAPSHOT_REFRESH_FPS}" =~ ^[1-9][0-9]*$ ]]; then
    snapshot_sleep="$(awk "BEGIN { s = 1/${SNAPSHOT_REFRESH_FPS}; if (s < 0.1) s = 0.1; printf \"%.3f\", s }")"
  fi

  mkdir -p "${snapshot_dir}"
  rm -f "${TREED_CAM_ZOOM_SNAPSHOT_FILE}" "${snapshot_tmp}" 2>/dev/null || true

  while true; do
    if ffmpeg -hide_banner -loglevel "${FFMPEG_LOGLEVEL}" -nostdin -y \
      -i "${TREED_CAM_RAW_SNAPSHOT_URL}" \
      -frames:v 1 -vf "${vf}" -c:v mjpeg -q:v 7 \
      "${snapshot_tmp}" >/dev/null 2>&1; then
      if [[ -s "${snapshot_tmp}" ]]; then
        mv -f "${snapshot_tmp}" "${TREED_CAM_ZOOM_SNAPSHOT_FILE}"
        fail_count=0
      else
        rm -f "${snapshot_tmp}" 2>/dev/null || true
        fail_count=$((fail_count + 1))
      fi
    else
      rm -f "${snapshot_tmp}" 2>/dev/null || true
      fail_count=$((fail_count + 1))
    fi

    if [[ "${fail_count}" -eq 5 || ( "${fail_count}" -gt 0 && $((fail_count % 20)) -eq 0 ) ]]; then
      log_zoom "snapshot refresh failed (count=${fail_count})"
    fi

    sleep "${snapshot_sleep}"
  done
}

start_children() {
  local vf
  vf="crop=${TREED_CAM_ROI_W}:${TREED_CAM_ROI_H}:${TREED_CAM_ROI_X}:${TREED_CAM_ROI_Y},scale=${TREED_CAM_ZOOM_OUTPUT_WIDTH}:${TREED_CAM_ZOOM_OUTPUT_HEIGHT}:flags=${SCALE_FLAGS}"

  log_zoom "start profile=${TREED_CAM_ZOOM_PROFILE_ACTIVE} roi=${TREED_CAM_ROI_X},${TREED_CAM_ROI_Y},${TREED_CAM_ROI_W},${TREED_CAM_ROI_H} out=${TREED_CAM_ZOOM_OUTPUT_WIDTH}x${TREED_CAM_ZOOM_OUTPUT_HEIGHT}"

  ffmpeg -hide_banner -loglevel "${FFMPEG_LOGLEVEL}" -nostdin \
    -fflags nobuffer -flags low_delay \
    -i "${TREED_CAM_RAW_STREAM_URL}" \
    -an -vf "${vf}" -c:v mjpeg -q:v 7 \
    -f mpjpeg -listen 1 "${TREED_CAM_ZOOM_STREAM_URL_LOCAL}" >/dev/null 2>&1 &
  FFMPEG_STREAM_PID="$!"

  snapshot_loop "${vf}" &
  FFMPEG_SNAPSHOT_PID="$!"
}

# Блок 6: Основной цикл wrapper — ожидание upstream, запуск ffmpeg и hot-reload профиля.
main() {
  local current_sig last_sig stream_dead snapshot_dead

  if ! command -v ffmpeg >/dev/null 2>&1; then
    log_zoom "ffmpeg not found"
    exit 1
  fi

  while true; do
    if treed_cam_env_load_zoom; then
      :
    else
      log_zoom "zoom config is invalid; retry in 2s"
      sleep 2
      continue
    fi

    if wait_for_raw_upstream; then
      :
    else
      sleep 2
      continue
    fi

    start_children
    last_sig="$(config_signature)"

    while true; do
      sleep "${CONFIG_POLL_SEC}"

      stream_dead=0
      snapshot_dead=0
      if [[ -z "${FFMPEG_STREAM_PID}" ]] || ! kill -0 "${FFMPEG_STREAM_PID}" 2>/dev/null; then
        stream_dead=1
      fi
      if [[ -z "${FFMPEG_SNAPSHOT_PID}" ]] || ! kill -0 "${FFMPEG_SNAPSHOT_PID}" 2>/dev/null; then
        snapshot_dead=1
      fi
      if [[ "${stream_dead}" -eq 1 || "${snapshot_dead}" -eq 1 ]]; then
        log_zoom "ffmpeg child exited (stream_dead=${stream_dead}, snapshot_dead=${snapshot_dead}); restarting"
        stop_children
        break
      fi

      if treed_cam_env_load_zoom; then
        current_sig="$(config_signature)"
        if [[ "${current_sig}" != "${last_sig}" ]]; then
          log_zoom "zoom config/profile changed; reloading ffmpeg children"
          stop_children
          break
        fi
      else
        log_zoom "zoom config became invalid; restarting after retry"
        stop_children
        break
      fi
    done
  done
}

main "$@"
