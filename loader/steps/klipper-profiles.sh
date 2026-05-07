#!/bin/bash
set -euo pipefail

# ==========================================
# ШАГ LOADER: KLIPPER PROFILES
# ==========================================
# Назначение:
# - Применяет фиксированный V2-профиль и runtime-идентификаторы MCU.
# - Подставляет CAN UUID и CAN-интерфейс для Octopus Pro, EBB и Eddy.
# Контур:
# - required (без корректных идентификаторов Klipper не стартует).

# Блок 1: Библиотеки и пути профиля.
. "${REPO_DIR}/loader/lib/common.sh"

KLIPPER_DIR="${PI_HOME}/treed/klipper"
PROFILE_NAME="treed_v2_corexy_v1"
PROFILE_DIR="${KLIPPER_DIR}/profiles/${PROFILE_NAME}"
PRINTER_CFG="${KLIPPER_DIR}/printer.cfg"
MAIN_MCU_CFG="${PROFILE_DIR}/mcu_main_octopus_can.cfg"
EBB_CFG="${PROFILE_DIR}/ebb42_can.cfg"
EDDY_CFG="${PROFILE_DIR}/probe_eddy_duo_optional.cfg"
STEPPERS_CFG="${PROFILE_DIR}/steppers.cfg"

MAIN_MCU_CANBUS_UUID="${TREED_MAIN_MCU_CANBUS_UUID:-d372e54bf965}"
EBB_CANBUS_UUID="${TREED_EBB_CANBUS_UUID:-efaf957ab20f}"
EDDY_ENABLED="${TREED_EDDY_ENABLED:-1}"
EDDY_CANBUS_UUID="${TREED_EDDY_CANBUS_UUID:-95485b93332a}"
Z_ENDSTOP_PIN="${TREED_Z_ENDSTOP_PIN:-PG10}"
Z_POSITION_ENDSTOP="${TREED_Z_POSITION_ENDSTOP:-0.5}"
Z_SENSORLESS_ENDSTOP_PIN="tmc5160_stepper_z:virtual_endstop"
Z_SENSORLESS_POSITION_ENDSTOP="200"
CAN_IFACE="${TREED_CAN_IFACE:-can0}"
EDDY_INCLUDE_PATH="profiles/${PROFILE_NAME}/probe_eddy_duo_optional.cfg"

log_info "Step klipper-profiles: apply V2 profile ${PROFILE_NAME}"

# Блок 2: Валидация staging и обязательных файлов.
if [ ! -d "${KLIPPER_DIR}" ] || [ ! -f "${PRINTER_CFG}" ]; then
  log_error "klipper-profiles: staging missing/incomplete: ${KLIPPER_DIR}"
  exit 1
fi

for required_file in "${MAIN_MCU_CFG}" "${EBB_CFG}" "${EDDY_CFG}" "${STEPPERS_CFG}"; do
  if [ ! -f "${required_file}" ]; then
    log_error "klipper-profiles: required file not found: ${required_file}"
    exit 1
  fi
done

# Блок 3: Вспомогательные функции правки профильных конфигов.
set_stepper_z_endstop() {
  local endstop_pin="$1"
  local position_endstop="${2:-}"
  local keep_position_endstop="$3"
  local homing_positive_dir="${4:-}"
  local keep_homing_positive_dir="$5"
  local tmp=""

  if ! grep -qE '^[[:space:]]*\[stepper_z\][[:space:]]*$' "${STEPPERS_CFG}"; then
    log_error "klipper-profiles: [stepper_z] section not found in ${STEPPERS_CFG}"
    exit 1
  fi

  tmp="$(mktemp)"
  awk -v endstop_pin="${endstop_pin}" \
      -v position_endstop="${position_endstop}" \
      -v homing_positive_dir="${homing_positive_dir}" \
      -v keep_position_endstop="${keep_position_endstop}" \
      -v keep_homing_positive_dir="${keep_homing_positive_dir}" '
    BEGIN {
      in_z = 0
      saw_z = 0
      saw_endstop = 0
      wrote_position = 0
      wrote_homing_dir = 0
    }
    function emit_z_tail_if_needed() {
      if (in_z && keep_position_endstop == "1" && !wrote_position) {
        print "position_endstop: " position_endstop
        wrote_position = 1
      }
      if (in_z && keep_homing_positive_dir == "1" && !wrote_homing_dir) {
        print "homing_positive_dir: " homing_positive_dir
        wrote_homing_dir = 1
      }
    }
    /^[[:space:]]*\[stepper_z\][[:space:]]*$/ {
      in_z = 1
      saw_z = 1
      wrote_position = 0
      wrote_homing_dir = 0
      print
      next
    }
    /^[[:space:]]*\[[^]]+\][[:space:]]*$/ {
      emit_z_tail_if_needed()
      in_z = 0
      print
      next
    }
    in_z && /^[[:space:]]*endstop_pin[[:space:]]*:/ {
      print "endstop_pin: " endstop_pin
      saw_endstop = 1
      next
    }
    in_z && /^[[:space:]]*position_endstop[[:space:]]*:/ {
      if (keep_position_endstop == "1") {
        print "position_endstop: " position_endstop
        wrote_position = 1
      }
      next
    }
    in_z && /^[[:space:]]*homing_positive_dir[[:space:]]*:/ {
      if (keep_homing_positive_dir == "1") {
        print "homing_positive_dir: " homing_positive_dir
        wrote_homing_dir = 1
      }
      next
    }
    { print }
    END {
      emit_z_tail_if_needed()
      if (!saw_z || !saw_endstop) {
        exit 2
      }
    }
  ' "${STEPPERS_CFG}" > "${tmp}" || {
    rm -f "${tmp}"
    log_error "klipper-profiles: failed to update stepper_z endstop in ${STEPPERS_CFG}"
    exit 1
  }
  mv "${tmp}" "${STEPPERS_CFG}"
}

# Блок 4: Валидация явного CAN UUID для main MCU (required).
if [ -z "${MAIN_MCU_CANBUS_UUID}" ]; then
  log_error "klipper-profiles: TREED_MAIN_MCU_CANBUS_UUID is required; set explicit Octopus Pro CAN UUID"
  exit 1
fi
case "${MAIN_MCU_CANBUS_UUID}" in
  *[!0-9A-Fa-f]*)
    log_error "klipper-profiles: TREED_MAIN_MCU_CANBUS_UUID must be hex, got: ${MAIN_MCU_CANBUS_UUID}"
    exit 1
    ;;
esac

# Блок 5: Валидация явного CAN UUID для EBB (required).
if [ -z "${EBB_CANBUS_UUID}" ]; then
  log_error "klipper-profiles: TREED_EBB_CANBUS_UUID is required; set explicit EBB42 CAN UUID"
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

# Блок 6: Валидация режима Eddy и include-тоггла.
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
else
  if [ -z "${Z_ENDSTOP_PIN}" ] || ! printf '%s' "${Z_ENDSTOP_PIN}" | grep -Eq '^[!^~]*[A-Za-z0-9_.:-]+$'; then
    log_error "klipper-profiles: TREED_Z_ENDSTOP_PIN has invalid format: ${Z_ENDSTOP_PIN}"
    exit 1
  fi
  if [ "${Z_ENDSTOP_PIN}" = "probe:z_virtual_endstop" ]; then
    log_error "klipper-profiles: TREED_Z_ENDSTOP_PIN=probe:z_virtual_endstop requires TREED_EDDY_ENABLED=1"
    exit 1
  fi
  if ! printf '%s' "${Z_POSITION_ENDSTOP}" | grep -Eq '^-?([0-9]+([.][0-9]+)?|[.][0-9]+)$'; then
    log_error "klipper-profiles: TREED_Z_POSITION_ENDSTOP must be numeric, got: ${Z_POSITION_ENDSTOP}"
    exit 1
  fi
fi

# Блок 7: Идемпотентная запись CAN UUID/interface main MCU и EBB.
if ! grep -qE '^[[:space:]]*canbus_uuid:[[:space:]]*' "${MAIN_MCU_CFG}"; then
  log_error "klipper-profiles: canbus_uuid line not found in ${MAIN_MCU_CFG}"
  exit 1
fi
sed -i -E "s|^([[:space:]]*canbus_uuid:[[:space:]]*)[^[:space:]#]+(.*)$|\\1${MAIN_MCU_CANBUS_UUID}\\2|" "${MAIN_MCU_CFG}"
if ! grep -qE '^[[:space:]]*canbus_interface:[[:space:]]*' "${MAIN_MCU_CFG}"; then
  log_error "klipper-profiles: canbus_interface line not found in ${MAIN_MCU_CFG}"
  exit 1
fi
sed -i -E "s|^([[:space:]]*canbus_interface:[[:space:]]*)[^[:space:]#]+(.*)$|\\1${CAN_IFACE}\\2|" "${MAIN_MCU_CFG}"
log_info "klipper-profiles: main MCU canbus_uuid -> ${MAIN_MCU_CANBUS_UUID}"
log_info "klipper-profiles: main MCU canbus_interface -> ${CAN_IFACE}"

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

# Блок 8: Управление контуром Eddy и его UUID.
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
  set_stepper_z_endstop "${Z_SENSORLESS_ENDSTOP_PIN}" "${Z_SENSORLESS_POSITION_ENDSTOP}" "1" "true" "1"
  log_info "klipper-profiles: Eddy enabled, canbus_uuid -> ${EDDY_CANBUS_UUID}"
  log_info "klipper-profiles: Eddy canbus_interface -> ${CAN_IFACE}"
  log_info "klipper-profiles: stepper_z endstop -> ${Z_SENSORLESS_ENDSTOP_PIN}, position_endstop=${Z_SENSORLESS_POSITION_ENDSTOP}"
else
  sed -i -E "s|^[[:space:]]*#?[[:space:]]*\\[include[[:space:]]+${EDDY_INCLUDE_PATH//\//\\/}\\][[:space:]]*$|# [include ${EDDY_INCLUDE_PATH}]|" "${PRINTER_CFG}"
  set_stepper_z_endstop "${Z_ENDSTOP_PIN}" "${Z_POSITION_ENDSTOP}" "1" "false" "1"
  log_info "klipper-profiles: Eddy disabled (include commented)"
  log_info "klipper-profiles: stepper_z endstop -> ${Z_ENDSTOP_PIN}, position_endstop=${Z_POSITION_ENDSTOP}, homing_positive_dir=false"
fi

# Блок 9: Финализация владельца staging.
if [ -z "${PI_USER:-}" ]; then
  log_error "klipper-profiles: PI_USER is not set"
  exit 1
fi
if ! grp="$(pi_primary_group "${PI_USER}")"; then
  exit 1
fi
chown -R "${PI_USER}:${grp}" "${KLIPPER_DIR}"

log_info "klipper-profiles: OK"
