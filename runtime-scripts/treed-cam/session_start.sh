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
SNAP_URL="http://127.0.0.1:8080/?action=snapshot"

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
curl -fsS "${SNAP_URL}" -o "${dir}/img_${ts}_start.jpg" >/dev/null 2>&1 || true
