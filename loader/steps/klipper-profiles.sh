#!/bin/bash
set -euo pipefail

# ==========================================
# ШАГ LOADER: KLIPPER PROFILES
# ==========================================
# Назначение:
# - Применяет фиксированный V2-профиль и runtime-идентификаторы MCU.
# - Подставляет main USB serial, CAN UUID для EBB и optional UUID Eddy.
# Контур:
# - required (без корректных идентификаторов Klipper не стартует).

# Блок 1: Библиотеки и пути профиля.
. "${REPO_DIR}/loader/lib/common.sh"

KLIPPER_DIR="${PI_HOME}/treed/klipper"
PROFILE_NAME="treed_v2_corexy_v1"
PROFILE_DIR="${KLIPPER_DIR}/profiles/${PROFILE_NAME}"
PRINTER_CFG="${KLIPPER_DIR}/printer.cfg"
MAIN_MCU_CFG="${PROFILE_DIR}/mcu_main_octopus_usb.cfg"
EBB_CFG="${PROFILE_DIR}/ebb42_can.cfg"
EDDY_CFG="${PROFILE_DIR}/probe_eddy_duo_optional.cfg"

MAIN_MCU_SERIAL_BY_ID="${TREED_MAIN_MCU_SERIAL_BY_ID:-}"
MAIN_MCU_SERIAL_MASK="${TREED_MAIN_MCU_SERIAL_MASK:-/dev/serial/by-id/*stm32*}"
EBB_CANBUS_UUID="${TREED_EBB_CANBUS_UUID:-}"
EDDY_ENABLED="${TREED_EDDY_ENABLED:-0}"
EDDY_CANBUS_UUID="${TREED_EDDY_CANBUS_UUID:-}"
CAN_IFACE="${TREED_CAN_IFACE:-can0}"
EDDY_INCLUDE_PATH="profiles/${PROFILE_NAME}/probe_eddy_duo_optional.cfg"

log_info "Step klipper-profiles: apply V2 profile ${PROFILE_NAME}"

# Блок 2: Валидация staging и обязательных файлов.
if [ ! -d "${KLIPPER_DIR}" ] || [ ! -f "${PRINTER_CFG}" ]; then
  log_error "klipper-profiles: staging missing/incomplete: ${KLIPPER_DIR}"
  exit 1
fi

for required_file in "${MAIN_MCU_CFG}" "${EBB_CFG}" "${EDDY_CFG}"; do
  if [ ! -f "${required_file}" ]; then
    log_error "klipper-profiles: required file not found: ${required_file}"
    exit 1
  fi
done

# Блок 3: Резолв main MCU serial (override -> auto by vendor-mask).
MAIN_SERIAL_PATH=""
if [ -n "${MAIN_MCU_SERIAL_BY_ID}" ]; then
  case "${MAIN_MCU_SERIAL_BY_ID}" in
    /dev/serial/by-id/*) ;;
    *)
      log_error "klipper-profiles: TREED_MAIN_MCU_SERIAL_BY_ID must be /dev/serial/by-id/*, got: ${MAIN_MCU_SERIAL_BY_ID}"
      exit 1
      ;;
  esac
  if [ ! -e "${MAIN_MCU_SERIAL_BY_ID}" ] || [ ! -r "${MAIN_MCU_SERIAL_BY_ID}" ]; then
    log_error "klipper-profiles: TREED_MAIN_MCU_SERIAL_BY_ID is missing/unreadable: ${MAIN_MCU_SERIAL_BY_ID}"
    exit 1
  fi
  MAIN_SERIAL_PATH="${MAIN_MCU_SERIAL_BY_ID}"
else
  shopt -s nullglob
  main_candidates=(${MAIN_MCU_SERIAL_MASK})
  shopt -u nullglob

  case "${#main_candidates[@]}" in
    0)
      log_error "klipper-profiles: no main MCU candidates found by mask ${MAIN_MCU_SERIAL_MASK}"
      exit 1
      ;;
    1)
      MAIN_SERIAL_PATH="${main_candidates[0]}"
      ;;
    *)
      log_error "klipper-profiles: main MCU auto-resolve is ambiguous by mask ${MAIN_MCU_SERIAL_MASK}"
      for candidate in "${main_candidates[@]}"; do
        log_error " - ${candidate}"
      done
      log_error "klipper-profiles: set TREED_MAIN_MCU_SERIAL_BY_ID explicitly"
      exit 1
      ;;
  esac
fi

# Блок 4: Валидация и подстановка CAN UUID для EBB (required).
if [ -z "${EBB_CANBUS_UUID}" ]; then
  log_error "klipper-profiles: TREED_EBB_CANBUS_UUID is required"
  exit 1
fi
case "${EBB_CANBUS_UUID}" in
  *[!0-9A-Fa-f]*)
    log_error "klipper-profiles: TREED_EBB_CANBUS_UUID must be hex, got: ${EBB_CANBUS_UUID}"
    exit 1
    ;;
esac

if ! printf '%s' "${CAN_IFACE}" | grep -Eq '^[A-Za-z0-9_.:-]+$'; then
  log_error "klipper-profiles: TREED_CAN_IFACE has invalid format: ${CAN_IFACE}"
  exit 1
fi

# Блок 5: Валидация режима Eddy и include-тоггла.
case "${EDDY_ENABLED}" in
  0|1) ;;
  *)
    log_error "klipper-profiles: TREED_EDDY_ENABLED must be 0 or 1, got: ${EDDY_ENABLED}"
    exit 1
    ;;
esac

if ! grep -qE "^[[:space:]]*#?[[:space:]]*\\[include[[:space:]]+${EDDY_INCLUDE_PATH//\//\\/}\\][[:space:]]*$" "${PRINTER_CFG}"; then
  log_error "klipper-profiles: cannot find Eddy include toggle in ${PRINTER_CFG}"
  exit 1
fi

if [ "${EDDY_ENABLED}" = "1" ]; then
  if [ -z "${EDDY_CANBUS_UUID}" ]; then
    log_error "klipper-profiles: TREED_EDDY_CANBUS_UUID is required when TREED_EDDY_ENABLED=1"
    exit 1
  fi
  case "${EDDY_CANBUS_UUID}" in
    *[!0-9A-Fa-f]*)
      log_error "klipper-profiles: TREED_EDDY_CANBUS_UUID must be hex, got: ${EDDY_CANBUS_UUID}"
      exit 1
      ;;
  esac
fi

# Блок 6: Идемпотентная запись main serial и EBB UUID.
if ! grep -qE '^[[:space:]]*serial:[[:space:]]*' "${MAIN_MCU_CFG}"; then
  log_error "klipper-profiles: serial line not found in ${MAIN_MCU_CFG}"
  exit 1
fi
sed -i -E "s|^([[:space:]]*serial:[[:space:]]*)[^[:space:]#]+(.*)$|\\1${MAIN_SERIAL_PATH}\\2|" "${MAIN_MCU_CFG}"
log_info "klipper-profiles: main MCU serial -> ${MAIN_SERIAL_PATH}"

if ! grep -qE '^[[:space:]]*canbus_uuid:[[:space:]]*' "${EBB_CFG}"; then
  log_error "klipper-profiles: canbus_uuid line not found in ${EBB_CFG}"
  exit 1
fi
sed -i -E "s|^([[:space:]]*canbus_uuid:[[:space:]]*)[^[:space:]#]+(.*)$|\\1${EBB_CANBUS_UUID}\\2|" "${EBB_CFG}"
if ! grep -qE '^[[:space:]]*canbus_interface:[[:space:]]*' "${EBB_CFG}"; then
  log_error "klipper-profiles: canbus_interface line not found in ${EBB_CFG}"
  exit 1
fi
sed -i -E "s|^([[:space:]]*canbus_interface:[[:space:]]*)[^[:space:]#]+(.*)$|\\1${CAN_IFACE}\\2|" "${EBB_CFG}"
log_info "klipper-profiles: EBB canbus_uuid -> ${EBB_CANBUS_UUID}"
log_info "klipper-profiles: EBB canbus_interface -> ${CAN_IFACE}"

# Блок 7: Управление optional-контуром Eddy и его UUID.
if [ "${EDDY_ENABLED}" = "1" ]; then
  sed -i -E "s|^[[:space:]]*#?[[:space:]]*\\[include[[:space:]]+${EDDY_INCLUDE_PATH//\//\\/}\\][[:space:]]*$|[include ${EDDY_INCLUDE_PATH}]|" "${PRINTER_CFG}"

  if ! grep -qE '^[[:space:]]*canbus_uuid:[[:space:]]*' "${EDDY_CFG}"; then
    log_error "klipper-profiles: canbus_uuid line not found in ${EDDY_CFG}"
    exit 1
  fi
  sed -i -E "s|^([[:space:]]*canbus_uuid:[[:space:]]*)[^[:space:]#]+(.*)$|\\1${EDDY_CANBUS_UUID}\\2|" "${EDDY_CFG}"
  if ! grep -qE '^[[:space:]]*canbus_interface:[[:space:]]*' "${EDDY_CFG}"; then
    log_error "klipper-profiles: canbus_interface line not found in ${EDDY_CFG}"
    exit 1
  fi
  sed -i -E "s|^([[:space:]]*canbus_interface:[[:space:]]*)[^[:space:]#]+(.*)$|\\1${CAN_IFACE}\\2|" "${EDDY_CFG}"
  log_info "klipper-profiles: Eddy enabled, canbus_uuid -> ${EDDY_CANBUS_UUID}"
  log_info "klipper-profiles: Eddy canbus_interface -> ${CAN_IFACE}"
else
  sed -i -E "s|^[[:space:]]*#?[[:space:]]*\\[include[[:space:]]+${EDDY_INCLUDE_PATH//\//\\/}\\][[:space:]]*$|# [include ${EDDY_INCLUDE_PATH}]|" "${PRINTER_CFG}"
  log_info "klipper-profiles: Eddy disabled (include commented)"
fi

# Блок 8: Финализация владельца staging.
if [ -z "${PI_USER:-}" ]; then
  log_error "klipper-profiles: PI_USER is not set"
  exit 1
fi
if ! grp="$(pi_primary_group "${PI_USER}")"; then
  exit 1
fi
chown -R "${PI_USER}:${grp}" "${KLIPPER_DIR}"

log_info "klipper-profiles: OK"
