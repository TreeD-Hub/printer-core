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
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Loader устанавливает скрипты в ${PI_HOME}/treed/cam/bin; окружение сервиса может не содержать PI_HOME.
PI_HOME="${PI_HOME:-$(cd "${SCRIPT_DIR}/../../.." && pwd)}"

# shellcheck source=runtime-scripts/treed-cam/cam_env.sh
source "${SCRIPT_DIR}/cam_env.sh"
treed_cam_load_runtime_env

BASE_DIR="${PI_HOME}/treed/cam/prints"
SESSION_FILE="/tmp/treed_cam_session_dir"
UNAVAILABLE_FILE="${SESSION_FILE}.feedback-unavailable"
SNAP_URL="$(treed_cam_snapshot_url)"

# Блок 2: Production передаёт token:filename одним аргументом; ручное имя сохраняет снимки.
raw="${1:-${PARAMS:-}}"
session_token=""
if [[ "${raw}" =~ ^[0-9]+: ]]; then
  session_token="${raw%%:*}"
  raw="${raw#*:}"
fi
filename="${raw}"
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

# Блок 4: Инвалидация прежней оценки до открытия новой сессии.
if ! printf 'metadata pending\n' > "${UNAVAILABLE_FILE}"; then
  printf '[treed-cam] Не удалось заблокировать прежнюю оценку\n' >&2
fi
if ! rm -f "${BASE_DIR}/.latest_session"; then
  printf '[treed-cam] Не удалось удалить указатель прежней сессии\n' >&2
fi

# Блок 5: Уникальный каталог даже при повторной печати в ту же секунду.
ts="$(date +%Y%m%d_%H%M%S)"
mkdir -p "${BASE_DIR}"
dir="$(mktemp -d "${BASE_DIR}/${safe:0:80}__${ts}_XXXXXX")"
printf '%s\n' "${dir}" > "${SESSION_FILE}"
if python3 "${SCRIPT_DIR}/session_metadata.py" start "${BASE_DIR}" "${dir}" "${filename}" "${session_token}"; then
  rm -f "${UNAVAILABLE_FILE}" || true
else
  printf '[treed-cam] Метаданные сессии недоступны; оценка отключена\n' >&2
fi

# Блок 6: Best-effort стартовый снимок в каталог сессии.
if ! curl -fsS "${SNAP_URL}" -o "${dir}/img_${ts}_start.jpg" >/dev/null 2>&1; then
  treed_cam_warn_snapshot_failure "session_start" "${SNAP_URL}"
fi
