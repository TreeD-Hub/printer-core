#!/bin/bash
set -euo pipefail

# ==========================================
# RUNTIME SCRIPT: TREED CAM SESSION STOP
# ==========================================
# Назначение:
# - Завершает активную сессию TreeD Cam.
# - Закрывает её метаданные для оценки печати (best-effort).
# - Удаляет marker-файл текущей сессии.
# Контур:
# - идемпотентный (повторный вызов безопасен).

# Блок 1: Пути соответствуют раскладке loader и явному PI_HOME.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PI_HOME="${PI_HOME:-$(cd "${SCRIPT_DIR}/../../.." && pwd)}"
SESSION_FILE="/tmp/treed_cam_session_dir"
session_token="${1:-${PARAMS:-}}"

# Блок 2: Закрытие именно активного каталога; повторный stop ничего не меняет.
if [[ -f "${SESSION_FILE}" ]]; then
  dir="$(cat "${SESSION_FILE}")"
  if python3 "${SCRIPT_DIR}/session_metadata.py" stop "${PI_HOME}/treed/cam/prints" "${dir}" "${session_token}"; then
    # Старый stop не удаляет marker, который уже переключили на другую сессию.
    if [[ -f "${SESSION_FILE}" && "$(cat "${SESSION_FILE}")" == "${dir}" ]]; then
      rm -f "${SESSION_FILE}"
    fi
  else
    result=$?
    if [[ "${result}" == 3 ]]; then
      exit 0
    fi
    printf '[treed-cam] Не удалось закрыть метаданные; оценка недоступна\n' >&2
  fi
fi
