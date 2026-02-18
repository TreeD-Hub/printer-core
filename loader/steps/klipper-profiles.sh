#!/bin/bash
set -euo pipefail

. "${REPO_DIR}/loader/lib/common.sh"

KLIPPER_DIR="${PI_HOME}/treed/klipper"
PROFILES_DIR="${KLIPPER_DIR}/profiles"
PROFILE_NAME="rn12_hbot_v1"
PROFILE_DIR="${PROFILES_DIR}/${PROFILE_NAME}"
MCU_CFG="${PROFILE_DIR}/mcu_rn12.cfg"
MCU_TRANSPORT_RAW="${TREED_MCU_TRANSPORT:-uart}"
MCU_UART_DEV="${TREED_MCU_UART_DEV:-/dev/serial0}"

case "${MCU_TRANSPORT_RAW}" in
  usb|USB) MCU_TRANSPORT="usb" ;;
  uart|UART) MCU_TRANSPORT="uart" ;;
  *)
    log_error "klipper-profiles: unsupported TREED_MCU_TRANSPORT='${MCU_TRANSPORT_RAW}' (expected: usb|uart)"
    exit 1
    ;;
esac

log_info "Step klipper-profiles: fixed profile ${PROFILE_NAME}, apply MCU transport=${MCU_TRANSPORT}"

if [ ! -d "${KLIPPER_DIR}" ] || [ ! -f "${KLIPPER_DIR}/printer.cfg" ] || [ ! -d "${PROFILES_DIR}" ]; then
  log_error "klipper-profiles: staging missing or incomplete: ${KLIPPER_DIR}"
  exit 1
fi

mkdir -p "${PROFILES_DIR}"

if [ ! -f "${MCU_CFG}" ]; then
  log_error "klipper-profiles: MCU config not found: ${MCU_CFG}"
  exit 1
fi

current_serial="$(sed -nE 's|^[[:space:]]*serial:[[:space:]]*([^[:space:]#]+).*|\\1|p' "${MCU_CFG}" | head -n 1 || true)"
SERIAL_PATH=""

if [ "${MCU_TRANSPORT}" = "uart" ]; then
  # В UART-режиме используем фиксированный serial-узел и запрещаем USB by-id override.
  if [ -n "${MCU_SERIAL_BY_ID:-}" ]; then
    log_error "klipper-profiles: MCU_SERIAL_BY_ID cannot be used when TREED_MCU_TRANSPORT=uart"
    exit 1
  fi

  case "${MCU_UART_DEV}" in
    /dev/*) ;;
    *)
      log_error "klipper-profiles: TREED_MCU_UART_DEV must be an absolute /dev/* path, got: ${MCU_UART_DEV}"
      exit 1
      ;;
  esac

  if [ ! -e "${MCU_UART_DEV}" ] || [ ! -r "${MCU_UART_DEV}" ]; then
    log_error "klipper-profiles: UART device is missing/unreadable: ${MCU_UART_DEV}"
    exit 1
  fi

  SERIAL_PATH="${MCU_UART_DEV}"
else
  if [ -n "${MCU_SERIAL_BY_ID:-}" ]; then
    if [ ! -e "${MCU_SERIAL_BY_ID}" ] || [ ! -r "${MCU_SERIAL_BY_ID}" ]; then
      log_error "klipper-profiles: MCU_SERIAL_BY_ID set but invalid/unreadable: ${MCU_SERIAL_BY_ID}"
      exit 1
    fi
    case "${MCU_SERIAL_BY_ID}" in
      /dev/serial/by-id/*) ;;
      *)
        log_error "klipper-profiles: MCU_SERIAL_BY_ID must be a /dev/serial/by-id/* path, got: ${MCU_SERIAL_BY_ID}"
        exit 1
        ;;
    esac
    SERIAL_PATH="${MCU_SERIAL_BY_ID}"
  elif [ -n "${current_serial}" ] && [ -e "${current_serial}" ] && [ -r "${current_serial}" ]; then
    case "${current_serial}" in
      /dev/serial/by-id/*) SERIAL_PATH="${current_serial}" ;;
    esac
  fi
fi

if [ "${MCU_TRANSPORT}" = "usb" ] && [ -z "${SERIAL_PATH}" ]; then
  # Для USB автоподбор допустим только при однозначном /dev/serial/by-id.
  shopt -s nullglob
  by_id_paths=(/dev/serial/by-id/*)
  shopt -u nullglob

  case "${#by_id_paths[@]}" in
    0)
      log_error "klipper-profiles: no /dev/serial/by-id entries found; cannot set MCU serial"
      exit 1
      ;;
    1)
      SERIAL_PATH="${by_id_paths[0]}"
      ;;
    *)
      log_error "klipper-profiles: multiple /dev/serial/by-id entries found; ambiguous MCU serial. Set MCU_SERIAL_BY_ID."
      for p in "${by_id_paths[@]}"; do
        log_error " - ${p}"
      done
      exit 1
      ;;
  esac
fi

if [ -z "${SERIAL_PATH}" ]; then
  log_error "klipper-profiles: unable to resolve MCU serial path for transport=${MCU_TRANSPORT}"
  exit 1
fi

if ! grep -qE '^[[:space:]]*serial:[[:space:]]*' "${MCU_CFG}"; then
  log_error "klipper-profiles: serial line not found in ${MCU_CFG}"
  exit 1
fi

if [ "${current_serial}" = "${SERIAL_PATH}" ]; then
  log_info "MCU serial already correct in ${MCU_CFG}: ${SERIAL_PATH}"
else
  sed -i -E "s|^([[:space:]]*serial:[[:space:]]*)[^[:space:]#]+(.*)$|\\1${SERIAL_PATH}\\2|" "${MCU_CFG}"
  log_info "Updated MCU serial in ${MCU_CFG} to ${SERIAL_PATH}"
fi

if [ -z "${PI_USER:-}" ]; then
  log_error "klipper-profiles: PI_USER is not set"
  exit 1
fi

if ! grp="$(pi_primary_group "${PI_USER}")"; then
  exit 1
fi

# Корректируем владельца только в staging-контуре.
chown -R "${PI_USER}:${grp}" "${KLIPPER_DIR}"

log_info "klipper-profiles: OK"
