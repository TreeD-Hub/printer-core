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
SNAP_URL="http://127.0.0.1:8080/?action=snapshot"

# Блок 2: Проверка наличия активной сессии.
[[ -f "${SESSION_FILE}" ]] || exit 0
dir="$(cat "${SESSION_FILE}" 2>/dev/null || true)"
[[ -n "${dir}" ]] || exit 0

# Блок 3: Подготовка каталога и имени файла снимка.
mkdir -p "${dir}"

ts="$(date +%Y%m%d_%H%M%S)"
out="${dir}/img_${ts}_$RANDOM.jpg"

# Блок 4: Получение кадра (ошибка камеры не блокирует вызывающий контур).
curl -fsS "${SNAP_URL}" -o "${out}" >/dev/null 2>&1 || exit 0
