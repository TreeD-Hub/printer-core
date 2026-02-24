#!/bin/bash
set -euo pipefail

# ==========================================
# RUNTIME SCRIPT: TREED CAM ZOOM PROFILE STATUS
# ==========================================
# Назначение:
# - Печатает текущий zoom-профиль и ключевые URL camera-контура.
# - Используется из Moonraker/Klipper для быстрой диагностики без правок файлов.
# Контур:
# - read-only runtime-команда.

# Блок 1: Подключение общего camera env-helper и загрузка конфигурации.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/cam_env.sh"
treed_cam_env_load_zoom

# Блок 2: Вывод краткого статуса и списка профилей.
printf 'active=%s\n' "${TREED_CAM_ZOOM_PROFILE_ACTIVE}"
printf 'profiles=%s\n' "${TREED_CAM_ZOOM_PROFILES}"
printf 'zoom_snapshot_local=%s\n' "${TREED_CAM_ZOOM_SNAPSHOT_URL_LOCAL}"
printf 'zoom_stream_local=%s\n' "${TREED_CAM_ZOOM_STREAM_URL_LOCAL}"
printf 'raw_snapshot=%s\n' "${TREED_CAM_RAW_SNAPSHOT_URL}"
printf 'raw_stream=%s\n' "${TREED_CAM_RAW_STREAM_URL}"
printf 'roi=%s\n' "${TREED_CAM_ZOOM_ACTIVE_ROI_CSV}"

