#!/bin/bash
set -euo pipefail

# ==========================================
# RUNTIME SCRIPT: TREED CAM SNAPSHOT
# ==========================================
# Назначение:
# - Сохраняет очередной кадр в текущую активную сессию TreeD Cam.
# - Работает в safe-skip режиме: при отсутствии сессии тихо завершает работу.

# Блок 1: Константы marker-файла сессии и URL snapshot endpoint.
SESSION_FILE="/tmp/treed_cam_session_dir"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SNAP_URL=""

# Блок 1.1: Подключение общего camera env-helper и получение zoom snapshot URL.
if [[ -f "${SCRIPT_DIR}/cam_env.sh" ]]; then
  # shellcheck source=/dev/null
  source "${SCRIPT_DIR}/cam_env.sh" || true
fi
if declare -F treed_cam_env_load_zoom >/dev/null 2>&1; then
  if treed_cam_env_load_zoom >/dev/null 2>&1; then
    SNAP_URL="${TREED_CAM_ZOOM_SNAPSHOT_URL_LOCAL}"
  fi
fi

# Блок 2: Проверка наличия активной сессии.
[[ -f "${SESSION_FILE}" ]] || exit 0
dir="$(cat "${SESSION_FILE}" 2>/dev/null || true)"
[[ -n "${dir}" ]] || exit 0

# Блок 3: Подготовка каталога и имени файла снимка.
mkdir -p "${dir}"

ts="$(date +%Y%m%d_%H%M%S)"
out="${dir}/img_${ts}_$RANDOM.jpg"

# Блок 4: Получение кадра (ошибка камеры не блокирует вызывающий контур).
[[ -n "${SNAP_URL}" ]] || exit 0
curl -fsS "${SNAP_URL}" -o "${out}" >/dev/null 2>&1 || exit 0
