#!/usr/bin/env bash
set -euo pipefail

# ==========================================
# TREE D PI BOOTSTRAP
# ==========================================
# Назначение:
# - Дает короткий one-command запуск installer на Pi/Rock Pi.
# - Клонирует одноразовый installer checkout и запускает loader в auto-режиме.
# Контур:
# - required для первичного входа с чистой ОС.

REPO_URL="${REPO_URL:-https://github.com/TreeD-Hub/treed-mainshellOS.git}"
INSTALL_REF="${INSTALL_REF:-treed-v2}"
BASE="${BASE:-/home/pi/treed}"
REPO_DIR="${REPO_DIR:-${BASE}/treed-mainshellOS}"
STATE_FILE="${TREED_STATE_FILE:-/run/treed-loader/state.env}"
TREED_REBOOT_AFTER_FRESH="${TREED_REBOOT_AFTER_FRESH:-1}"

RUN_USER="${SUDO_USER:-$(id -un)}"
RUN_UID="$(id -u "${RUN_USER}")"
RUN_GID="$(id -g "${RUN_USER}")"

if ! command -v git >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y git ca-certificates
fi

sudo mkdir -p "${BASE}"
sudo chown "${RUN_UID}:${RUN_GID}" "${BASE}"

case "${REPO_DIR}" in
  "${BASE}/"*)
    sudo rm -rf "${REPO_DIR}"
    ;;
  *)
    echo "[bootstrap-pi] ERROR: REPO_DIR must be inside BASE (${BASE}), got: ${REPO_DIR}" >&2
    exit 1
    ;;
esac

git clone --branch "${INSTALL_REF}" --depth 1 "${REPO_URL}" "${REPO_DIR}"

cd "${REPO_DIR}"
sudo TREED_DEPLOY_MODE=auto TREED_NONINTERACTIVE=1 bash install.sh

TREED_DEVICE_STATE=""
if [ -f "${STATE_FILE}" ]; then
  # shellcheck disable=SC1090
  . "${STATE_FILE}"
fi

# Reboot gate: TREED_DEVICE_STATE=fresh.
if [ "${TREED_DEVICE_STATE:-}" = "fresh" ] && [ "${TREED_REBOOT_AFTER_FRESH}" = "1" ]; then
  sudo reboot
fi
