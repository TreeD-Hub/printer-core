#!/bin/bash
set -euo pipefail

# ==========================================
# ШАГ LOADER: TREED CAM
# ==========================================
# Назначение:
# - Разворачивает runtime-скрипты камеры TreeD в домашний каталог пользователя.
# - Гарантирует права, структуру каталогов и исполняемость скриптов.
# Контур:
# - required для camera runtime-утилит; сервис детекции работает только с локальным detect.env.

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

# Блок 4: Подготовка директории назначения.
ensure_dir "${DST_BIN}"
ensure_dir "${DST_DATA}"
ensure_dir "${DST_ROOT}/config"

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

# Блок 7: Опциональный мониторинг; ключ и URL задаются только в локальном detect.env.
cat > /etc/systemd/system/treed-detection.service <<EOF
[Unit]
Description=TreeD spaghetti detection
After=network-online.target moonraker.service crowsnest.service
Wants=network-online.target
ConditionPathExists=${DST_ROOT}/config/detect.env

[Service]
User=${PI_USER}
EnvironmentFile=${DST_ROOT}/config/detect.env
ExecStart=/usr/bin/python3 -u ${DST_BIN}/stream_detect.py --react --fps 5
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable treed-detection.service
if [[ -f "${DST_ROOT}/config/detect.env" ]]; then
  systemctl restart treed-detection.service || log_warn "treed-detection: не удалось запустить мониторинг"
fi

log_info "treed-cam: DONE (data at ${DST_DATA})"
