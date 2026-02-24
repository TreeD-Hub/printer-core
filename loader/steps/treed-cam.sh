#!/bin/bash
set -euo pipefail

# ==========================================
# ШАГ LOADER: TREED CAM
# ==========================================
# Назначение:
# - Разворачивает runtime-скрипты камеры TreeD в домашний каталог пользователя.
# - Гарантирует права, структуру каталогов и исполняемость скриптов.
# Контур:
# - required для camera runtime-утилит, но без изменения системных сервисов.

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
LIB_DIR="${REPO_DIR}/loader/lib"
# Блок 1: Библиотеки и базовая инициализация.
source "${LIB_DIR}/common.sh"

# Блок 2: Старт шага и чтение пользовательского контекста.
log_info "Step treed-cam"


PI_USER="${PI_USER:-pi}"
PI_HOME="${PI_HOME:-/home/${PI_USER}}"
if ! grp="$(pi_primary_group "${PI_USER}")"; then
  exit 1
fi

# Блок 3: Расчет путей source/destination.
SRC_DIR="${REPO_DIR}/runtime-scripts/treed-cam"
DST_ROOT="${PI_HOME}/treed/cam"
DST_BIN="${DST_ROOT}/bin"
DST_DATA="${DST_ROOT}/prints"
DST_CONFIG="${DST_ROOT}/config"
DST_LOGS="${DST_ROOT}/logs"

# Блок 4: Подготовка директории назначения.
ensure_dir "${DST_BIN}"
ensure_dir "${DST_DATA}"
ensure_dir "${DST_CONFIG}"
ensure_dir "${DST_LOGS}"

if [[ ! -d "${SRC_DIR}" ]]; then
  log_error "Missing runtime scripts directory: ${SRC_DIR}"
  exit 1
fi

# Блок 5: Полная синхронизация runtime-скриптов камеры.
log_info "Sync runtime scripts: ${SRC_DIR} -> ${DST_BIN}"
# Runtime-скрипты камеры синхронизируем полным снапшотом.
find "${DST_BIN}" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
cp -a "${SRC_DIR}/." "${DST_BIN}/"

# Блок 6: Исполняемость shell-скриптов и права владельца.
find "${DST_BIN}" -type f -name '*.sh' -exec chmod +x {} \;

chown -R "${PI_USER}:${grp}" "${DST_ROOT}" || true

# Блок 7: Отложенный старт zoom-sidecar после раскладки runtime-скриптов.
# Шаг crowsnest-webcam выполняется раньше и может задеплоить unit до появления bin/zoom-sidecar.sh.
if systemctl cat treed-cam-zoom.service >/dev/null 2>&1; then
  log_info "Restarting treed-cam-zoom after runtime scripts sync"
  systemctl enable treed-cam-zoom.service >/dev/null 2>&1 || true
  systemctl restart treed-cam-zoom.service || true
fi

log_info "treed-cam: DONE (data at ${DST_DATA})"
