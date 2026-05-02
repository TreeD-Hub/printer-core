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
CAN_BITRATE="${TREED_CAN_BITRATE:-500000}"
CAN_TXQUEUE="${TREED_CAN_TXQUEUE:-1024}"
CAN_AUTOBITRATE="${TREED_CAN_AUTOBITRATE:-1}"
CAN_AUTOBITRATE_LIST="${TREED_CAN_AUTOBITRATE_LIST:-500000 1000000 250000 125000}"
CAN_SETUP_ENV_FILE="${TREED_CAN_SETUP_ENV_FILE:-/etc/default/treed-can-setup}"
CAN_SETUP_UNIT="${TREED_CAN_SETUP_UNIT:-treed-can-setup.service}"
EDDY_INCLUDE_PATH="profiles/${PROFILE_NAME}/probe_eddy_duo_optional.cfg"
KLIPPER_SRC_DIR="${TREED_KLIPPER_SRC_DIR:-${PI_HOME}/klipper}"
CANBUS_QUERY_SCRIPT="${TREED_CANBUS_QUERY_SCRIPT:-}"
KLIPPY_ENV_DIR="${TREED_KLIPPY_ENV_DIR:-${PI_HOME}/klippy-env}"
CANBUS_QUERY_PYTHON="${TREED_CANBUS_QUERY_PYTHON:-}"

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

# Блок 2a: Авто-резолв EBB UUID через canbus_query (если UUID не передан явно).
CAN_QUERY_OUTPUT=""
CAN_QUERY_RC=0
CAN_QUERY_UUID_COUNT=0
CAN_QUERY_LAST_UUID=""

query_canbus_candidates() {
  local query_python="$1"
  local query_script="$2"
  local query_output=""
  local query_rc=0
  local detected_count=0
  local detected_uuid=""
  local detected_line=""

  if query_output="$("${query_python}" "${query_script}" "${CAN_IFACE}" 2>&1)"; then
    query_rc=0
  else
    query_rc=$?
    log_warn "klipper-profiles: canbus_query exited with code ${query_rc}, parsing output for UUID candidates"
  fi

  while IFS= read -r detected_line; do
    detected_uuid="$(printf '%s\n' "${detected_line}" | sed -n -E 's/.*canbus_uuid=([0-9A-Fa-f]+).*/\1/p')"
    if [ -n "${detected_uuid}" ]; then
      detected_count=$((detected_count + 1))
      CAN_QUERY_LAST_UUID="${detected_uuid}"
      log_info "klipper-profiles: auto-detect candidate #${detected_count}: ${detected_uuid}"
    fi
  done <<EOF
${query_output}
EOF

  CAN_QUERY_OUTPUT="${query_output}"
  CAN_QUERY_RC="${query_rc}"
  CAN_QUERY_UUID_COUNT="${detected_count}"
}

setup_can_iface_bitrate() {
  local bitrate="$1"

  if ! command -v ip >/dev/null 2>&1; then
    log_warn "klipper-profiles: ip command not found, cannot switch CAN bitrate to ${bitrate}"
    return 1
  fi

  if ! ip link set "${CAN_IFACE}" down >/dev/null 2>&1; then
    log_warn "klipper-profiles: cannot set ${CAN_IFACE} down before bitrate switch"
  fi
  ip link set "${CAN_IFACE}" type can bitrate "${bitrate}"
  ip link set "${CAN_IFACE}" txqueuelen "${CAN_TXQUEUE}"
  ip link set "${CAN_IFACE}" up
}

persist_can_setup_env() {
  local iface="$1"
  local bitrate="$2"
  local txqueue="$3"

  if [ ! -f "${CAN_SETUP_ENV_FILE}" ]; then
    log_warn "klipper-profiles: cannot persist detected CAN bitrate, file is missing: ${CAN_SETUP_ENV_FILE}"
    return 0
  fi

  sed -i -E "s|^TREED_CAN_IFACE=.*$|TREED_CAN_IFACE=${iface}|" "${CAN_SETUP_ENV_FILE}"
  if grep -qE '^TREED_CAN_BITRATE=' "${CAN_SETUP_ENV_FILE}"; then
    sed -i -E "s|^TREED_CAN_BITRATE=.*$|TREED_CAN_BITRATE=${bitrate}|" "${CAN_SETUP_ENV_FILE}"
  else
    printf 'TREED_CAN_BITRATE=%s\n' "${bitrate}" >> "${CAN_SETUP_ENV_FILE}"
  fi
  if grep -qE '^TREED_CAN_TXQUEUE=' "${CAN_SETUP_ENV_FILE}"; then
    sed -i -E "s|^TREED_CAN_TXQUEUE=.*$|TREED_CAN_TXQUEUE=${txqueue}|" "${CAN_SETUP_ENV_FILE}"
  else
    printf 'TREED_CAN_TXQUEUE=%s\n' "${txqueue}" >> "${CAN_SETUP_ENV_FILE}"
  fi

  if command -v systemctl >/dev/null 2>&1 && systemctl cat "${CAN_SETUP_UNIT}" >/dev/null 2>&1; then
    systemctl restart "${CAN_SETUP_UNIT}" || log_warn "klipper-profiles: failed to restart ${CAN_SETUP_UNIT} after bitrate update"
  fi
}

resolve_ebb_canbus_uuid_auto() {
  local query_script=""
  local query_python=""
  local script_candidates=()
  local python_candidates=()
  local candidate=""
  local initial_bitrate="${CAN_BITRATE}"
  local last_applied_bitrate="${CAN_BITRATE}"
  local detected_bitrate=""
  local scan_bitrate=""
  local scan_bitrate_list=""
  local seen_scan_bitrates=" "

  if [ -n "${CANBUS_QUERY_SCRIPT}" ]; then
    script_candidates+=("${CANBUS_QUERY_SCRIPT}")
  fi
  script_candidates+=(
    "${KLIPPER_SRC_DIR}/scripts/canbus_query.py"
    "${PI_HOME}/klipper/scripts/canbus_query.py"
    "/home/pi/klipper/scripts/canbus_query.py"
  )

  for candidate in "${script_candidates[@]}"; do
    if [ -f "${candidate}" ]; then
      query_script="${candidate}"
      break
    fi
  done

  if [ -z "${query_script}" ]; then
    log_error "klipper-profiles: TREED_EBB_CANBUS_UUID is empty and canbus_query.py is not found"
    log_error "klipper-profiles: checked paths: ${script_candidates[*]}"
    return 1
  fi

  if [ -n "${CANBUS_QUERY_PYTHON}" ]; then
    python_candidates+=("${CANBUS_QUERY_PYTHON}")
  fi
  python_candidates+=(
    "${KLIPPY_ENV_DIR}/bin/python3"
    "${KLIPPY_ENV_DIR}/bin/python"
  )

  if command -v python3 >/dev/null 2>&1; then
    python_candidates+=("$(command -v python3)")
  fi

  if command -v python >/dev/null 2>&1; then
    python_candidates+=("$(command -v python)")
  fi

  for candidate in "${python_candidates[@]}"; do
    if [ -x "${candidate}" ]; then
      query_python="${candidate}"
      break
    fi
  done

  if [ -z "${query_python}" ]; then
    log_error "klipper-profiles: TREED_EBB_CANBUS_UUID is empty and python interpreter for canbus_query is not available"
    log_error "klipper-profiles: checked interpreters: ${python_candidates[*]}"
    return 1
  fi

  query_canbus_candidates "${query_python}" "${query_script}"

  if [ "${CAN_QUERY_UUID_COUNT}" -eq 0 ] && [ "${CAN_AUTOBITRATE}" = "1" ]; then
    scan_bitrate_list="${CAN_AUTOBITRATE_LIST}"
    for scan_bitrate in ${scan_bitrate_list}; do
      case "${scan_bitrate}" in
        ''|*[!0-9]*) continue ;;
      esac
      if [ "${scan_bitrate}" -le 0 ]; then
        continue
      fi
      if printf '%s' "${seen_scan_bitrates}" | grep -Fq " ${scan_bitrate} "; then
        continue
      fi
      seen_scan_bitrates="${seen_scan_bitrates}${scan_bitrate} "

      if [ "${scan_bitrate}" = "${CAN_BITRATE}" ]; then
        continue
      fi

      log_info "klipper-profiles: trying CAN bitrate auto-detect on ${CAN_IFACE}: ${scan_bitrate}"
      if ! setup_can_iface_bitrate "${scan_bitrate}"; then
        log_warn "klipper-profiles: failed to switch ${CAN_IFACE} to bitrate ${scan_bitrate}"
        continue
      fi
      last_applied_bitrate="${scan_bitrate}"

      query_canbus_candidates "${query_python}" "${query_script}"
      if [ "${CAN_QUERY_UUID_COUNT}" -gt 0 ]; then
        detected_bitrate="${scan_bitrate}"
        break
      fi
    done
  fi

  if [ -n "${detected_bitrate}" ] && [ "${detected_bitrate}" != "${CAN_BITRATE}" ]; then
    CAN_BITRATE="${detected_bitrate}"
    log_warn "klipper-profiles: detected active CAN bitrate ${CAN_BITRATE} on ${CAN_IFACE}, persisting to ${CAN_SETUP_ENV_FILE}"
    persist_can_setup_env "${CAN_IFACE}" "${CAN_BITRATE}" "${CAN_TXQUEUE}"
  elif [ -z "${detected_bitrate}" ] && [ "${CAN_QUERY_UUID_COUNT}" -eq 0 ] && [ "${last_applied_bitrate}" != "${initial_bitrate}" ]; then
    setup_can_iface_bitrate "${initial_bitrate}" || true
  fi

  case "${CAN_QUERY_UUID_COUNT}" in
    0)
      log_error "klipper-profiles: TREED_EBB_CANBUS_UUID is empty and auto-detect found no UUID on ${CAN_IFACE}"
      if [ -n "${CAN_QUERY_OUTPUT}" ]; then
        log_error "klipper-profiles: canbus_query output follows:"
        while IFS= read -r detected_line; do
          log_error "  ${detected_line}"
        done <<EOF
${CAN_QUERY_OUTPUT}
EOF
      fi
      return 1
      ;;
    1)
      EBB_CANBUS_UUID="${CAN_QUERY_LAST_UUID}"
      log_info "klipper-profiles: auto-detected EBB canbus_uuid=${EBB_CANBUS_UUID}"
      return 0
      ;;
    *)
      log_error "klipper-profiles: TREED_EBB_CANBUS_UUID is empty and auto-detect found multiple UUIDs on ${CAN_IFACE}"
      log_error "klipper-profiles: set TREED_EBB_CANBUS_UUID explicitly"
      return 1
      ;;
  esac
}

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

# Блок 4: Валидация и подстановка CAN UUID для EBB (required/auto-detect).
if [ -z "${EBB_CANBUS_UUID}" ]; then
  if ! resolve_ebb_canbus_uuid_auto; then
    exit 1
  fi
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
