#!/bin/bash
set -euo pipefail

# ==========================================
# RUNTIME SCRIPT: TREED CAM ZOOM PROFILE SET
# ==========================================
# Назначение:
# - Меняет активный zoom-профиль камеры (`wide|medium|close`) атомарной записью.
# - Используется Moonraker shell_command и Klipper-макросами без sudo.
# Контур:
# - короткая управляющая команда, не перезапускает systemd напрямую.

# Блок 1: Подключение общего camera env-helper и разбор входного параметра.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/cam_env.sh"

profile_raw="${1:-${PARAMS:-}}"
profile_raw="${profile_raw//$'\r'/}"
profile_raw="${profile_raw//$'\n'/}"
profile="${profile_raw,,}"

if [[ -z "${profile}" ]]; then
  printf 'error=missing_profile\n' >&2
  exit 1
fi

# Блок 2: Загрузка конфига и валидация requested профиля.
treed_cam_env_load_zoom
treed_cam_env_validate_profile "${profile}"

# Блок 3: Атомарное обновление active-profile файла.
tmp="$(mktemp "${TREED_CAM_CONFIG_DIR}/zoom_active.env.tmp.XXXXXX")"
printf 'TREED_CAM_ZOOM_PROFILE=%s\n' "${profile}" > "${tmp}"
mv -f "${tmp}" "${TREED_CAM_ZOOM_ACTIVE_ENV}"

# Блок 4: Вывод статуса для Moonraker/консоли.
printf 'active=%s\n' "${profile}"

