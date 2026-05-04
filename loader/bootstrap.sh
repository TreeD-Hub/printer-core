#!/usr/bin/env bash
set -euo pipefail

# ==========================================
# TREE D BOOTSTRAP
# ==========================================
# Назначение:
# - держит дефолты V2 runtime-переменных вне README;
# - нормализует shell-файлы после Windows checkout;
# - запускает основной loader-оркестратор.
#
# Важно:
# - внешние env override сохраняются;
# - bootstrap не прошивает MCU сам, этим управляет loader/steps/firmware-build.sh;
# - bootstrap должен запускаться через sudo.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [ "${EUID}" -ne 0 ]; then
  echo "[bootstrap] ERROR: run as root: sudo bash install.sh" >&2
  exit 1
fi

DEPLOY_USER="${PI_USER:-${SUDO_USER:-$(id -un)}}"
DEPLOY_HOME="$(getent passwd "${DEPLOY_USER}" | cut -d: -f6 || true)"

if [ -z "${DEPLOY_HOME}" ] || [ ! -d "${DEPLOY_HOME}" ]; then
  echo "[bootstrap] ERROR: cannot determine home for user ${DEPLOY_USER}" >&2
  exit 1
fi

# ---- V2 defaults ------------------------------------------------------------
# Все значения можно переопределить снаружи:
# sudo TREED_CAN_BITRATE=500000 TREED_EDDY_ENABLED=1 bash install.sh

: "${TREED_MAIN_MCU_SERIAL_BY_ID:=}"
: "${TREED_MAIN_MCU_SERIAL_MASK:=/dev/serial/by-id/*stm32*}"

: "${TREED_CAN_IFACE:=can0}"
: "${TREED_CAN_BITRATE:=1000000}"
: "${TREED_CAN_TXQUEUE:=1024}"

: "${TREED_EBB_CANBUS_UUID:=}"
: "${TREED_EDDY_ENABLED:=0}"
: "${TREED_EDDY_CANBUS_UUID:=}"

: "${TREED_Z_ENDSTOP_PIN:=PG10}"
: "${TREED_Z_POSITION_ENDSTOP:=0.5}"

: "${TREED_NONINTERACTIVE:=1}"

: "${TREED_KLIPPERSCREEN_INSTALL_SERVICE:=1}"
: "${TREED_KLIPPERSCREEN_BACKEND:=X}"
: "${TREED_KLIPPERSCREEN_NETWORK_MANAGER:=N}"
: "${TREED_KLIPPERSCREEN_START_AFTER_INSTALL:=0}"

: "${TREED_FIRMWARE_BUILD_ENABLED:=1}"
: "${TREED_KLIPPER_SRC_DIR:=${DEPLOY_HOME}/klipper}"
: "${TREED_FIRMWARE_ARTIFACTS_DIR:=${DEPLOY_HOME}/treed/firmware-artifacts/treed-v2}"

: "${TREED_CROWSNEST_SRC_DIR:=${DEPLOY_HOME}/crowsnest}"
: "${TREED_CROWSNEST_REPO:=https://github.com/mainsail-crew/crowsnest.git}"
: "${TREED_CROWSNEST_REF:=}"
: "${TREED_CROWSNEST_INSTALL:=1}"
: "${TREED_CROWSNEST_RECREATE:=0}"
: "${TREED_CROWSNEST_UPDATE:=1}"

: "${TREED_FW_MAIN_CONFIG:=${REPO_DIR}/firmware/configs/treed_v2/main_octopus_pro_f446_usb.config}"
: "${TREED_FW_EBB_CONFIG:=${REPO_DIR}/firmware/configs/treed_v2/ebb42_can_stm32g0b1.config}"
: "${TREED_FW_EDDY_CONFIG:=${REPO_DIR}/firmware/configs/treed_v2/eddy_can_rp2040.config}"

export REPO_DIR

export TREED_MAIN_MCU_SERIAL_BY_ID
export TREED_MAIN_MCU_SERIAL_MASK

export TREED_CAN_IFACE
export TREED_CAN_BITRATE
export TREED_CAN_TXQUEUE

export TREED_EBB_CANBUS_UUID
export TREED_EDDY_ENABLED
export TREED_EDDY_CANBUS_UUID

export TREED_Z_ENDSTOP_PIN
export TREED_Z_POSITION_ENDSTOP

export TREED_NONINTERACTIVE

export TREED_KLIPPERSCREEN_INSTALL_SERVICE
export TREED_KLIPPERSCREEN_BACKEND
export TREED_KLIPPERSCREEN_NETWORK_MANAGER
export TREED_KLIPPERSCREEN_START_AFTER_INSTALL

export TREED_FIRMWARE_BUILD_ENABLED
export TREED_KLIPPER_SRC_DIR
export TREED_FIRMWARE_ARTIFACTS_DIR

export TREED_CROWSNEST_SRC_DIR
export TREED_CROWSNEST_REPO
export TREED_CROWSNEST_REF
export TREED_CROWSNEST_INSTALL
export TREED_CROWSNEST_RECREATE
export TREED_CROWSNEST_UPDATE

export TREED_FW_MAIN_CONFIG
export TREED_FW_EBB_CONFIG
export TREED_FW_EDDY_CONFIG

if [ "${TREED_NONINTERACTIVE}" = "1" ]; then
  export DEBIAN_FRONTEND="${DEBIAN_FRONTEND:-noninteractive}"
fi

echo "[bootstrap] REPO_DIR=${REPO_DIR}"
echo "[bootstrap] DEPLOY_USER=${DEPLOY_USER}"
echo "[bootstrap] DEPLOY_HOME=${DEPLOY_HOME}"
echo "[bootstrap] TREED_CAN_IFACE=${TREED_CAN_IFACE}"
echo "[bootstrap] TREED_CAN_BITRATE=${TREED_CAN_BITRATE}"
echo "[bootstrap] TREED_EDDY_ENABLED=${TREED_EDDY_ENABLED}"
echo "[bootstrap] TREED_FIRMWARE_BUILD_ENABLED=${TREED_FIRMWARE_BUILD_ENABLED}"
echo "[bootstrap] TREED_CROWSNEST_INSTALL=${TREED_CROWSNEST_INSTALL}"

# ---- Normalize loader shell files ------------------------------------------
if [ -d "${REPO_DIR}/loader" ]; then
  find "${REPO_DIR}/loader" -type f -name '*.sh' -print0 | xargs -0 -r sed -i 's/\r$//'
  chmod +x "${REPO_DIR}/loader/loader.sh"
  find "${REPO_DIR}/loader/steps" -type f -name '*.sh' -exec chmod +x {} + 2>/dev/null || true
fi

if [ ! -f "${REPO_DIR}/loader/loader.sh" ]; then
  echo "[bootstrap] ERROR: missing ${REPO_DIR}/loader/loader.sh" >&2
  exit 1
fi

echo "[bootstrap] Starting loader..."
bash "${REPO_DIR}/loader/loader.sh"
