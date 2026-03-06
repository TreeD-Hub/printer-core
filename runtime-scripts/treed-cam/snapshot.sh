#!/bin/bash
set -euo pipefail

# ==========================================
# RUNTIME SCRIPT: TREED CAM SNAPSHOT
# ==========================================
# Назначение:
# - Сохраняет очередной кадр в текущую активную сессию TreeD Cam.
# - Работает в safe-skip режиме: при отсутствии сессии тихо завершает работу.

# Блок 1: Константы marker-файла и загрузка общего camera-env.
SESSION_FILE="/tmp/treed_cam_session_dir"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=runtime-scripts/treed-cam/cam_env.sh
source "${SCRIPT_DIR}/cam_env.sh"
treed_cam_load_runtime_env
SNAP_URL="$(treed_cam_snapshot_url)"

# Блок 2: Проверка наличия активной сессии.
[[ -f "${SESSION_FILE}" ]] || exit 0
dir="$(cat "${SESSION_FILE}" 2>/dev/null || true)"
[[ -n "${dir}" ]] || exit 0

# Блок 3: Подготовка каталога и имени файла снимка.
mkdir -p "${dir}"

ts="$(date +%Y%m%d_%H%M%S)"
out="${dir}/img_${ts}_$RANDOM.jpg"

# Блок 4: Получение кадра (ошибка камеры не блокирует вызывающий контур).
if ! curl -fsS "${SNAP_URL}" -o "${out}" >/dev/null 2>&1; then
  treed_cam_warn_snapshot_failure "snapshot_tick" "${SNAP_URL}"
  exit 0
fi
