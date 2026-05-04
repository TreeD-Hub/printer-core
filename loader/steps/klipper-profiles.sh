#!/bin/bash
set -euo pipefail

# ==========================================
# ШАГ LOADER: KLIPPER PROFILES
# ==========================================
# Назначение:
# - Применяет фиксированный V2-профиль и runtime-идентификаторы MCU.
# - Генерирует machine-specific include для main USB serial, EBB CAN UUID и optional Eddy UUID.
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
STEPPERS_CFG="${PROFILE_DIR}/steppers.cfg"
MACHINE_MCUS_CFG="${KLIPPER_DIR}/generated/treed_machine_mcus.cfg"
MACHINE_MCUS_INCLUDE_PATH="generated/treed_machine_mcus.cfg"

MAIN_MCU_SERIAL_BY_ID="${TREED_MAIN_MCU_SERIAL_BY_ID:-}"
MAIN_MCU_SERIAL_MASK="${TREED_MAIN_MCU_SERIAL_MASK:-/dev/serial/by-id/*stm32*}"
EBB_CANBUS_UUID="${TREED_EBB_CANBUS_UUID:-}"
EDDY_ENABLED="${TREED_EDDY_ENABLED:-0}"
EDDY_CANBUS_UUID="${TREED_EDDY_CANBUS_UUID:-}"
Z_ENDSTOP_PIN="${TREED_Z_ENDSTOP_PIN:-PG10}"
Z_POSITION_ENDSTOP="${TREED_Z_POSITION_ENDSTOP:-0.5}"
CAN_IFACE="${TREED_CAN_IFACE:-can0}"
CAN_BITRATE="${TREED_CAN_BITRATE:-1000000}"
CAN_TXQUEUE="${TREED_CAN_TXQUEUE:-1024}"
CAN_RESTART_MS="${TREED_CAN_RESTART_MS:-100}"
CAN_AUTOBITRATE="${TREED_CAN_AUTOBITRATE:-1}"
CAN_AUTOBITRATE_LIST="${TREED_CAN_AUTOBITRATE_LIST:-1000000 500000 250000 125000}"
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

for required_file in "${MAIN_MCU_CFG}" "${EBB_CFG}" "${EDDY_CFG}" "${STEPPERS_CFG}"; do
  if [ ! -f "${required_file}" ]; then
    log_error "klipper-profiles: required file not found: ${required_file}"
    exit 1
  fi
done

# Блок 2a: CAN-инвентарь и резолв ролей через canbus_query.
CAN_QUERY_OUTPUT=""
CAN_QUERY_RC=0
CAN_QUERY_UUID_COUNT=0
CAN_QUERY_LAST_UUID=""
CAN_QUERY_UUIDS=""
CAN_QUERY_READY=0
CAN_UNKNOWN_UUID_COUNT=0
CAN_UNKNOWN_UUID_LAST=""

normalize_uuid() {
  printf '%s' "$1" | tr 'A-F' 'a-f'
}

query_canbus_candidates() {
  local query_python="$1"
  local query_script="$2"
  local query_output=""
  local query_rc=0
  local detected_count=0
  local detected_uuid=""
  local detected_uuids=""
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
      detected_uuid="$(normalize_uuid "${detected_uuid}")"
      case " ${detected_uuids} " in
        *" ${detected_uuid} "*) continue ;;
      esac
      detected_uuids="${detected_uuids}${detected_uuid} "
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
  CAN_QUERY_UUIDS="${detected_uuids% }"
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
  ip link set "${CAN_IFACE}" txqueuelen "${CAN_TXQUEUE}"
  ip link set "${CAN_IFACE}" up type can bitrate "${bitrate}" restart-ms "${CAN_RESTART_MS}"
}

persist_can_setup_env() {
  local iface="$1"
  local bitrate="$2"
  local txqueue="$3"
  local restart_ms="$4"

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
  if grep -qE '^TREED_CAN_RESTART_MS=' "${CAN_SETUP_ENV_FILE}"; then
    sed -i -E "s|^TREED_CAN_RESTART_MS=.*$|TREED_CAN_RESTART_MS=${restart_ms}|" "${CAN_SETUP_ENV_FILE}"
  else
    printf 'TREED_CAN_RESTART_MS=%s\n' "${restart_ms}" >> "${CAN_SETUP_ENV_FILE}"
  fi

  if command -v systemctl >/dev/null 2>&1 && systemctl cat "${CAN_SETUP_UNIT}" >/dev/null 2>&1; then
    systemctl restart "${CAN_SETUP_UNIT}" || log_warn "klipper-profiles: failed to restart ${CAN_SETUP_UNIT} after bitrate update"
  fi
}

refresh_canbus_inventory() {
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
  )

  for candidate in "${script_candidates[@]}"; do
    if [ -f "${candidate}" ]; then
      query_script="${candidate}"
      break
    fi
  done

  if [ -z "${query_script}" ]; then
    log_error "klipper-profiles: canbus_query.py is not found"
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
    log_error "klipper-profiles: python interpreter for canbus_query is not available"
    log_error "klipper-profiles: checked interpreters: ${python_candidates[*]}"
    return 1
  fi

  log_info "klipper-profiles: trying CAN bitrate auto-detect on ${CAN_IFACE}: ${CAN_BITRATE}"
  if setup_can_iface_bitrate "${CAN_BITRATE}"; then
    last_applied_bitrate="${CAN_BITRATE}"
    query_canbus_candidates "${query_python}" "${query_script}"
  else
    log_warn "klipper-profiles: failed to switch ${CAN_IFACE} to bitrate ${CAN_BITRATE}"
    CAN_QUERY_OUTPUT=""
    CAN_QUERY_RC=1
    CAN_QUERY_UUID_COUNT=0
  fi

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
    persist_can_setup_env "${CAN_IFACE}" "${CAN_BITRATE}" "${CAN_TXQUEUE}" "${CAN_RESTART_MS}"
  elif [ -z "${detected_bitrate}" ] && [ "${CAN_QUERY_UUID_COUNT}" -eq 0 ] && [ "${last_applied_bitrate}" != "${initial_bitrate}" ]; then
    setup_can_iface_bitrate "${initial_bitrate}" || true
  fi

  CAN_QUERY_READY=1
  log_info "klipper-profiles: CAN inventory on ${CAN_IFACE}: ${CAN_QUERY_UUID_COUNT} candidate(s)"
}

ensure_canbus_inventory() {
  if [ "${CAN_QUERY_READY}" = "1" ]; then
    return 0
  fi
  refresh_canbus_inventory
}

uuid_is_visible_on_can() {
  local uuid
  uuid="$(normalize_uuid "$1")"
  case " ${CAN_QUERY_UUIDS} " in
    *" ${uuid} "*) return 0 ;;
    *) return 1 ;;
  esac
}

select_single_unknown_can_uuid() {
  local exclude_a="${1:-}"
  local exclude_b="${2:-}"
  local uuid=""

  exclude_a="$(normalize_uuid "${exclude_a}")"
  exclude_b="$(normalize_uuid "${exclude_b}")"
  CAN_UNKNOWN_UUID_COUNT=0
  CAN_UNKNOWN_UUID_LAST=""

  for uuid in ${CAN_QUERY_UUIDS}; do
    if [ -n "${exclude_a}" ] && [ "${uuid}" = "${exclude_a}" ]; then
      continue
    fi
    if [ -n "${exclude_b}" ] && [ "${uuid}" = "${exclude_b}" ]; then
      continue
    fi
    CAN_UNKNOWN_UUID_COUNT=$((CAN_UNKNOWN_UUID_COUNT + 1))
    CAN_UNKNOWN_UUID_LAST="${uuid}"
  done

  [ "${CAN_UNKNOWN_UUID_COUNT}" -eq 1 ]
}

dump_canbus_query_output() {
  local line=""

  if [ -z "${CAN_QUERY_OUTPUT}" ]; then
    return 0
  fi

  log_error "klipper-profiles: canbus_query output follows:"
  while IFS= read -r line; do
    log_error "  ${line}"
  done <<EOF
${CAN_QUERY_OUTPUT}
EOF
}

resolve_ebb_canbus_uuid_auto() {
  if ! ensure_canbus_inventory; then
    return 1
  fi

  if ! select_single_unknown_can_uuid "${EDDY_CANBUS_UUID}"; then
    case "${CAN_UNKNOWN_UUID_COUNT}" in
      0)
        log_error "klipper-profiles: TREED_EBB_CANBUS_UUID is empty and no unknown CAN UUID is available on ${CAN_IFACE}"
        ;;
      *)
        log_error "klipper-profiles: TREED_EBB_CANBUS_UUID is empty and ${CAN_UNKNOWN_UUID_COUNT} unknown CAN UUIDs are visible on ${CAN_IFACE}"
        log_error "klipper-profiles: connect only EBB or set TREED_EBB_CANBUS_UUID explicitly"
        ;;
    esac
    dump_canbus_query_output
    return 1
  fi

  EBB_CANBUS_UUID="${CAN_UNKNOWN_UUID_LAST}"
  log_info "klipper-profiles: auto-detected EBB canbus_uuid=${EBB_CANBUS_UUID}"
}

resolve_eddy_canbus_uuid_auto() {
  if ! ensure_canbus_inventory; then
    return 1
  fi

  if ! select_single_unknown_can_uuid "${EBB_CANBUS_UUID}"; then
    case "${CAN_UNKNOWN_UUID_COUNT}" in
      0)
        log_error "klipper-profiles: TREED_EDDY_CANBUS_UUID is empty and no unknown CAN UUID remains after EBB=${EBB_CANBUS_UUID}"
        ;;
      *)
        log_error "klipper-profiles: TREED_EDDY_CANBUS_UUID is empty and ${CAN_UNKNOWN_UUID_COUNT} unknown CAN UUIDs remain on ${CAN_IFACE}"
        log_error "klipper-profiles: connect only Eddy as the new CAN device or set TREED_EDDY_CANBUS_UUID explicitly"
        ;;
    esac
    dump_canbus_query_output
    return 1
  fi

  EDDY_CANBUS_UUID="${CAN_UNKNOWN_UUID_LAST}"
  log_info "klipper-profiles: auto-detected Eddy canbus_uuid=${EDDY_CANBUS_UUID}"
}

# Блок 3: Резолв main MCU serial (override -> auto by vendor-mask).
set_stepper_z_endstop() {
  local endstop_pin="$1"
  local position_endstop="${2:-}"
  local keep_position_endstop="$3"
  local tmp=""

  if ! grep -qE '^[[:space:]]*\[stepper_z\][[:space:]]*$' "${STEPPERS_CFG}"; then
    log_error "klipper-profiles: [stepper_z] section not found in ${STEPPERS_CFG}"
    exit 1
  fi

  tmp="$(mktemp)"
  awk -v endstop_pin="${endstop_pin}" \
      -v position_endstop="${position_endstop}" \
      -v keep_position_endstop="${keep_position_endstop}" '
    BEGIN {
      in_z = 0
      saw_z = 0
      saw_endstop = 0
      wrote_position = 0
    }
    function emit_position_if_needed() {
      if (in_z && keep_position_endstop == "1" && !wrote_position) {
        print "position_endstop: " position_endstop
        wrote_position = 1
      }
    }
    /^[[:space:]]*\[stepper_z\][[:space:]]*$/ {
      in_z = 1
      saw_z = 1
      wrote_position = 0
      print
      next
    }
    /^[[:space:]]*\[[^]]+\][[:space:]]*$/ {
      emit_position_if_needed()
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
    { print }
    END {
      emit_position_if_needed()
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

write_machine_mcus_cfg() {
  ensure_dir "$(dirname "${MACHINE_MCUS_CFG}")"

  cat > "${MACHINE_MCUS_CFG}" <<EOF
# ==========================================
# GENERATED: TREE D MACHINE MCU IDS
# ==========================================
# Назначение:
# - Machine-specific MCU identities for this printer.
# - Generated by loader/steps/klipper-profiles.sh.
# Контур:
# - runtime-only; do not commit real values.

[mcu]
serial: ${MAIN_SERIAL_PATH}
restart_method: command

[mcu EBBCan]
canbus_uuid: ${EBB_CANBUS_UUID}
canbus_interface: ${CAN_IFACE}
EOF

  if [ "${EDDY_ENABLED}" = "1" ]; then
    cat >> "${MACHINE_MCUS_CFG}" <<EOF

[mcu eddy]
canbus_uuid: ${EDDY_CANBUS_UUID}
canbus_interface: ${CAN_IFACE}
EOF
  fi
}

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

# Блок 4: Валидация CAN-контракта и интерфейса.
if ! printf '%s' "${CAN_IFACE}" | grep -Eq '^[A-Za-z0-9_.:-]+$'; then
  log_error "klipper-profiles: TREED_CAN_IFACE has invalid format: ${CAN_IFACE}"
  exit 1
fi

case "${CAN_BITRATE}" in
  ''|*[!0-9]*)
    log_error "klipper-profiles: TREED_CAN_BITRATE must be a positive integer, got: ${CAN_BITRATE}"
    exit 1
    ;;
esac
if [ "${CAN_BITRATE}" -le 0 ]; then
  log_error "klipper-profiles: TREED_CAN_BITRATE must be > 0"
  exit 1
fi
case "${CAN_RESTART_MS}" in
  ''|*[!0-9]*)
    log_error "klipper-profiles: TREED_CAN_RESTART_MS must be a non-negative integer, got: ${CAN_RESTART_MS}"
    exit 1
    ;;
esac
if [ "${CAN_RESTART_MS}" -lt 0 ]; then
  log_error "klipper-profiles: TREED_CAN_RESTART_MS must be >= 0"
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

if ! grep -qE "^[[:space:]]*\\[include[[:space:]]+${MACHINE_MCUS_INCLUDE_PATH//\//\\/}\\][[:space:]]*$" "${PRINTER_CFG}"; then
  log_error "klipper-profiles: cannot find machine MCU include in ${PRINTER_CFG}: ${MACHINE_MCUS_INCLUDE_PATH}"
  exit 1
fi

# Блок 6: Резолв и проверка CAN UUID по ролям.
if [ -n "${EBB_CANBUS_UUID}" ]; then
  case "${EBB_CANBUS_UUID}" in
    *[!0-9A-Fa-f]*)
      log_error "klipper-profiles: TREED_EBB_CANBUS_UUID must be hex, got: ${EBB_CANBUS_UUID}"
      exit 1
      ;;
  esac
  EBB_CANBUS_UUID="$(normalize_uuid "${EBB_CANBUS_UUID}")"
else
  if ! resolve_ebb_canbus_uuid_auto; then
    exit 1
  fi
fi

if ! ensure_canbus_inventory; then
  exit 1
fi
if ! uuid_is_visible_on_can "${EBB_CANBUS_UUID}"; then
  log_error "klipper-profiles: EBB UUID ${EBB_CANBUS_UUID} is not visible on ${CAN_IFACE}"
  dump_canbus_query_output
  exit 1
fi

if [ "${EDDY_ENABLED}" = "1" ]; then
  if [ -z "${EDDY_CANBUS_UUID}" ]; then
    if ! resolve_eddy_canbus_uuid_auto; then
      exit 1
    fi
  else
    case "${EDDY_CANBUS_UUID}" in
      *[!0-9A-Fa-f]*)
        log_error "klipper-profiles: TREED_EDDY_CANBUS_UUID must be hex, got: ${EDDY_CANBUS_UUID}"
        exit 1
        ;;
    esac
    EDDY_CANBUS_UUID="$(normalize_uuid "${EDDY_CANBUS_UUID}")"
  fi
  if [ "${EDDY_CANBUS_UUID}" = "${EBB_CANBUS_UUID}" ]; then
    log_error "klipper-profiles: Eddy UUID must differ from EBB UUID (${EDDY_CANBUS_UUID})"
    exit 1
  fi
  if ! uuid_is_visible_on_can "${EDDY_CANBUS_UUID}"; then
    log_error "klipper-profiles: Eddy UUID ${EDDY_CANBUS_UUID} is not visible on ${CAN_IFACE}"
    dump_canbus_query_output
    exit 1
  fi
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

# Блок 7: Генерация machine-specific MCU include.
write_machine_mcus_cfg
log_info "klipper-profiles: main MCU serial -> ${MAIN_SERIAL_PATH}"
log_info "klipper-profiles: EBB canbus_uuid -> ${EBB_CANBUS_UUID}"
log_info "klipper-profiles: EBB canbus_interface -> ${CAN_IFACE}"
log_info "klipper-profiles: machine MCU config -> ${MACHINE_MCUS_CFG}"

# Блок 8: Управление optional-контуром Eddy.
if [ "${EDDY_ENABLED}" = "1" ]; then
  sed -i -E "s|^[[:space:]]*#?[[:space:]]*\\[include[[:space:]]+${EDDY_INCLUDE_PATH//\//\\/}\\][[:space:]]*$|[include ${EDDY_INCLUDE_PATH}]|" "${PRINTER_CFG}"

  set_stepper_z_endstop "probe:z_virtual_endstop" "" "0"
  log_info "klipper-profiles: Eddy enabled, canbus_uuid -> ${EDDY_CANBUS_UUID}"
  log_info "klipper-profiles: Eddy canbus_interface -> ${CAN_IFACE}"
  log_info "klipper-profiles: stepper_z endstop -> probe:z_virtual_endstop"
else
  sed -i -E "s|^[[:space:]]*#?[[:space:]]*\\[include[[:space:]]+${EDDY_INCLUDE_PATH//\//\\/}\\][[:space:]]*$|# [include ${EDDY_INCLUDE_PATH}]|" "${PRINTER_CFG}"
  set_stepper_z_endstop "${Z_ENDSTOP_PIN}" "${Z_POSITION_ENDSTOP}" "1"
  log_info "klipper-profiles: Eddy disabled (include commented)"
  log_info "klipper-profiles: stepper_z endstop -> ${Z_ENDSTOP_PIN}, position_endstop=${Z_POSITION_ENDSTOP}"
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
