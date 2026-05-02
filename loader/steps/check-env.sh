#!/bin/bash
set -euo pipefail

# ==========================================
# ШАГ LOADER: CHECK ENV
# ==========================================
# Назначение:
# - Валидирует базовое окружение loader перед provisioning.
# - Проверяет обязательные переменные PI_USER/PI_HOME и доступность home-каталога.
# Контур:
# - required (любой сбой останавливает provisioning).

# Блок 1: Библиотеки и базовая инициализация.
. "${REPO_DIR}/loader/lib/common.sh"

# Блок 2: Старт шага и проверка системных предусловий.
log_info "Step check-env: verifying environment"
ensure_root

# Блок 3: Валидация обязательного контракта loader-переменных.
# Контракт loader: PI_USER/PI_HOME обязаны быть определены до запуска step-скриптов.
if [ -z "${PI_USER:-}" ] || [ -z "${PI_HOME:-}" ]; then
  log_error "PI_USER or PI_HOME is not set"
  exit 1
fi

# Блок 4: Проверка доступности целевого home-каталога.
# Проверяем, что целевой home реально существует и пригоден для дальнейшего deploy.
if [ ! -d "${PI_HOME}" ]; then
  log_error "Home directory not found: ${PI_HOME}"
  exit 1
fi

# Блок 5: Диагностика ОС (не блокирует шаг).
if [ -f /etc/os-release ]; then
  . /etc/os-release
  log_info "Detected OS: ${PRETTY_NAME:-unknown}"
  if ! printf '%s' "${PRETTY_NAME:-}" | grep -qi "Armbian"; then
    log_warn "check-env: V2 baseline is Armbian Debian 12, current OS='${PRETTY_NAME:-unknown}'"
  fi
else
  log_warn "/etc/os-release not found; cannot detect OS"
fi

# Блок 6: Контракт V2 для main MCU / CAN / Eddy.
TREED_MAIN_MCU_SERIAL_BY_ID="${TREED_MAIN_MCU_SERIAL_BY_ID:-}"
TREED_MAIN_MCU_SERIAL_MASK="${TREED_MAIN_MCU_SERIAL_MASK:-/dev/serial/by-id/*stm32*}"
TREED_EBB_CANBUS_UUID="${TREED_EBB_CANBUS_UUID:-}"
TREED_EDDY_ENABLED="${TREED_EDDY_ENABLED:-0}"
TREED_EDDY_CANBUS_UUID="${TREED_EDDY_CANBUS_UUID:-}"
TREED_CAN_IFACE="${TREED_CAN_IFACE:-can0}"
TREED_CAN_BITRATE="${TREED_CAN_BITRATE:-1000000}"
TREED_CAN_TXQUEUE="${TREED_CAN_TXQUEUE:-1024}"
TREED_CAN_RESTART_MS="${TREED_CAN_RESTART_MS:-100}"
TREED_Z_ENDSTOP_PIN="${TREED_Z_ENDSTOP_PIN:-PG10}"
TREED_Z_POSITION_ENDSTOP="${TREED_Z_POSITION_ENDSTOP:-0.5}"
TREED_BOOT_BACKEND="${TREED_BOOT_BACKEND:-}"
TREED_NONINTERACTIVE="${TREED_NONINTERACTIVE:-1}"
TREED_KLIPPERSCREEN_INSTALL_SERVICE="${TREED_KLIPPERSCREEN_INSTALL_SERVICE:-1}"
TREED_KLIPPERSCREEN_BACKEND="${TREED_KLIPPERSCREEN_BACKEND:-X}"
TREED_KLIPPERSCREEN_NETWORK_MANAGER="${TREED_KLIPPERSCREEN_NETWORK_MANAGER:-N}"
TREED_KLIPPERSCREEN_START_AFTER_INSTALL="${TREED_KLIPPERSCREEN_START_AFTER_INSTALL:-0}"

TREED_FIRMWARE_BUILD_ENABLED="${TREED_FIRMWARE_BUILD_ENABLED:-1}"
TREED_KLIPPER_SRC_DIR="${TREED_KLIPPER_SRC_DIR:-${PI_HOME}/klipper}"
TREED_FIRMWARE_ARTIFACTS_DIR="${TREED_FIRMWARE_ARTIFACTS_DIR:-${PI_HOME}/treed/firmware-artifacts/treed-v2}"
TREED_FW_MAIN_CONFIG="${TREED_FW_MAIN_CONFIG:-${REPO_DIR}/firmware/configs/treed_v2/main_octopus_pro_f446_usb.config}"
TREED_FW_EBB_CONFIG="${TREED_FW_EBB_CONFIG:-${REPO_DIR}/firmware/configs/treed_v2/ebb42_can_stm32g0b1.config}"
TREED_FW_EDDY_CONFIG="${TREED_FW_EDDY_CONFIG:-${REPO_DIR}/firmware/configs/treed_v2/eddy_can_rp2040.config}"

if [ -n "${TREED_MAIN_MCU_SERIAL_BY_ID}" ]; then
  case "${TREED_MAIN_MCU_SERIAL_BY_ID}" in
    /dev/serial/by-id/*) ;;
    *)
      log_error "check-env: TREED_MAIN_MCU_SERIAL_BY_ID must be /dev/serial/by-id/*, got: ${TREED_MAIN_MCU_SERIAL_BY_ID}"
      exit 1
      ;;
  esac
fi

if [ -z "${TREED_MAIN_MCU_SERIAL_MASK}" ]; then
  log_error "check-env: TREED_MAIN_MCU_SERIAL_MASK must not be empty"
  exit 1
fi

case "${TREED_NONINTERACTIVE}" in
  0|1) ;;
  *)
    log_error "check-env: TREED_NONINTERACTIVE must be 0 or 1, got: ${TREED_NONINTERACTIVE}"
    exit 1
    ;;
esac

case "${TREED_KLIPPERSCREEN_INSTALL_SERVICE}" in
  0|1|y|Y|n|N) ;;
  *)
    log_error "check-env: TREED_KLIPPERSCREEN_INSTALL_SERVICE must be 0|1|Y|N, got: ${TREED_KLIPPERSCREEN_INSTALL_SERVICE}"
    exit 1
    ;;
esac

case "${TREED_KLIPPERSCREEN_BACKEND}" in
  X|x|W|w) ;;
  *)
    log_error "check-env: TREED_KLIPPERSCREEN_BACKEND must be X or W, got: ${TREED_KLIPPERSCREEN_BACKEND}"
    exit 1
    ;;
esac

case "${TREED_KLIPPERSCREEN_NETWORK_MANAGER}" in
  0|1|y|Y|n|N) ;;
  *)
    log_error "check-env: TREED_KLIPPERSCREEN_NETWORK_MANAGER must be 0|1|Y|N, got: ${TREED_KLIPPERSCREEN_NETWORK_MANAGER}"
    exit 1
    ;;
esac

case "${TREED_KLIPPERSCREEN_START_AFTER_INSTALL}" in
  0|1) ;;
  *)
    log_error "check-env: TREED_KLIPPERSCREEN_START_AFTER_INSTALL must be 0 or 1, got: ${TREED_KLIPPERSCREEN_START_AFTER_INSTALL}"
    exit 1
    ;;
esac

if [ -z "${TREED_EBB_CANBUS_UUID}" ]; then
  log_warn "check-env: TREED_EBB_CANBUS_UUID is empty, klipper-profiles will try auto-detect via canbus_query"
else
  case "${TREED_EBB_CANBUS_UUID}" in
    *[!0-9A-Fa-f]*)
      log_error "check-env: TREED_EBB_CANBUS_UUID must be hex, got: ${TREED_EBB_CANBUS_UUID}"
      exit 1
      ;;
  esac
fi

case "${TREED_EDDY_ENABLED}" in
  0|1) ;;
  *)
    log_error "check-env: TREED_EDDY_ENABLED must be 0 or 1, got: ${TREED_EDDY_ENABLED}"
    exit 1
    ;;
esac

if [ "${TREED_EDDY_ENABLED}" = "1" ]; then
  if [ -z "${TREED_EDDY_CANBUS_UUID}" ]; then
    log_error "check-env: TREED_EDDY_CANBUS_UUID is required when TREED_EDDY_ENABLED=1"
    exit 1
  fi
  case "${TREED_EDDY_CANBUS_UUID}" in
    *[!0-9A-Fa-f]*)
      log_error "check-env: TREED_EDDY_CANBUS_UUID must be hex, got: ${TREED_EDDY_CANBUS_UUID}"
      exit 1
      ;;
  esac
else
  if [ -z "${TREED_Z_ENDSTOP_PIN}" ] || ! printf '%s' "${TREED_Z_ENDSTOP_PIN}" | grep -Eq '^[!^~]*[A-Za-z0-9_.:-]+$'; then
    log_error "check-env: TREED_Z_ENDSTOP_PIN has invalid format: ${TREED_Z_ENDSTOP_PIN}"
    exit 1
  fi
  if [ "${TREED_Z_ENDSTOP_PIN}" = "probe:z_virtual_endstop" ]; then
    log_error "check-env: TREED_Z_ENDSTOP_PIN=probe:z_virtual_endstop requires TREED_EDDY_ENABLED=1"
    exit 1
  fi
  if ! printf '%s' "${TREED_Z_POSITION_ENDSTOP}" | grep -Eq '^-?([0-9]+([.][0-9]+)?|[.][0-9]+)$'; then
    log_error "check-env: TREED_Z_POSITION_ENDSTOP must be numeric, got: ${TREED_Z_POSITION_ENDSTOP}"
    exit 1
  fi
fi

if ! printf '%s' "${TREED_CAN_IFACE}" | grep -Eq '^[A-Za-z0-9_.:-]+$'; then
  log_error "check-env: TREED_CAN_IFACE has invalid format: ${TREED_CAN_IFACE}"
  exit 1
fi

if [ -n "${TREED_BOOT_BACKEND}" ]; then
  case "${TREED_BOOT_BACKEND}" in
    rpi|armbian|extlinux) ;;
    *)
      log_error "check-env: TREED_BOOT_BACKEND must be rpi|armbian|extlinux when set, got: ${TREED_BOOT_BACKEND}"
      exit 1
      ;;
  esac
fi

case "${TREED_CAN_BITRATE}" in
  ''|*[!0-9]*)
    log_error "check-env: TREED_CAN_BITRATE must be a positive integer, got: ${TREED_CAN_BITRATE}"
    exit 1
    ;;
esac
case "${TREED_CAN_TXQUEUE}" in
  ''|*[!0-9]*)
    log_error "check-env: TREED_CAN_TXQUEUE must be a positive integer, got: ${TREED_CAN_TXQUEUE}"
    exit 1
    ;;
esac
case "${TREED_CAN_RESTART_MS}" in
  ''|*[!0-9]*)
    log_error "check-env: TREED_CAN_RESTART_MS must be a non-negative integer, got: ${TREED_CAN_RESTART_MS}"
    exit 1
    ;;
esac
if [ "${TREED_CAN_BITRATE}" -le 0 ] || [ "${TREED_CAN_TXQUEUE}" -le 0 ]; then
  log_error "check-env: TREED_CAN_BITRATE and TREED_CAN_TXQUEUE must be > 0"
  exit 1
fi
if [ "${TREED_CAN_RESTART_MS}" -lt 0 ]; then
  log_error "check-env: TREED_CAN_RESTART_MS must be >= 0"
  exit 1
fi

case "${TREED_FIRMWARE_BUILD_ENABLED}" in
  0|1) ;;
  *)
    log_error "check-env: TREED_FIRMWARE_BUILD_ENABLED must be 0 or 1, got: ${TREED_FIRMWARE_BUILD_ENABLED}"
    exit 1
    ;;
esac

for abs_path_var in TREED_KLIPPER_SRC_DIR TREED_FIRMWARE_ARTIFACTS_DIR TREED_FW_MAIN_CONFIG TREED_FW_EBB_CONFIG TREED_FW_EDDY_CONFIG; do
  abs_path_val="$(eval "printf '%s' \"\${${abs_path_var}}\"")"
  case "${abs_path_val}" in
    /*) ;;
    *)
      log_error "check-env: ${abs_path_var} must be absolute path, got: ${abs_path_val}"
      exit 1
      ;;
  esac
done

log_info "check-env: OK"
