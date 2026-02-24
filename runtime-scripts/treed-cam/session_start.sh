#!/bin/bash
set -euo pipefail

# ==========================================
# RUNTIME SCRIPT: TREED CAM SESSION START
# ==========================================
# Назначение:
# - Открывает новую сессию снимков TreeD Cam.
# - Создает каталог сессии и сохраняет в него стартовый кадр (best-effort).
# Контур:
# - non-blocking для снимка: ошибка curl не ломает запуск сессии.

# Блок 1: Базовые параметры пользователя и путей.
PI_USER="${PI_USER:-pi}"
PI_HOME="${PI_HOME:-/home/${PI_USER}}"

BASE_DIR="${PI_HOME}/treed/cam/prints"
SESSION_FILE="/tmp/treed_cam_session_dir"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SNAP_URL=""

# Блок 1.1: Подключение общего camera env-helper (если zoom-контур развернут).
if [[ -f "${SCRIPT_DIR}/cam_env.sh" ]]; then
  # shellcheck source=/dev/null
  source "${SCRIPT_DIR}/cam_env.sh" || true
fi
if declare -F treed_cam_env_load_zoom >/dev/null 2>&1; then
  if treed_cam_env_load_zoom >/dev/null 2>&1; then
    SNAP_URL="${TREED_CAM_ZOOM_SNAPSHOT_URL_LOCAL}"
  fi
fi

# Блок 2: Чтение входного имени задания (arg1 или PARAMS) и нормализация.
raw="${1:-${PARAMS:-}}"
raw="${raw//$'\r'/}"
raw="${raw//$'\n'/}"

if [[ -z "${raw}" ]]; then
  raw="unknown"
fi

# Блок 3: Санитизация имени сессии для безопасного пути каталога.
name="$(basename -- "${raw}")"
name="${name%.*}"
safe="$(printf '%s' "${name}" | tr ' ' '_' | tr -cd 'A-Za-z0-9._-')"
if [[ -z "${safe}" ]]; then
  safe="unknown"
fi

# Блок 4: Формирование каталога сессии и запись session-marker файла.
ts="$(date +%Y%m%d_%H%M%S)"
dir="${BASE_DIR}/${safe}__${ts}"

mkdir -p "${dir}"
printf '%s\n' "${dir}" > "${SESSION_FILE}"

# Блок 5: Best-effort стартовый снимок в каталог сессии.
if [[ -n "${SNAP_URL}" ]]; then
  curl -fsS "${SNAP_URL}" -o "${dir}/img_${ts}_start.jpg" >/dev/null 2>&1 || true
fi
