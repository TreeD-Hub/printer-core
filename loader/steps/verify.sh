#!/bin/bash
set -euo pipefail

# ==========================================
# ШАГ LOADER: VERIFY
# ==========================================
# Назначение:
# - Выполняет финальные post-configuration проверки provisioning-контура.
# - Сохраняет паритет dev-отчета (boot/time/camera/ui/services) в V2-модели.
# - Валидирует V2 runtime: CAN Octopus/EBB(required), Eddy, Input Shaper.
# Контур:
# - required (непрошедшие проверки завершают loader с ошибкой).

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"

# Блок 1: Библиотеки и базовая инициализация шага.
. "${REPO_DIR}/loader/lib/common.sh"
. "${REPO_DIR}/loader/lib/boot-env.sh"

log_info "Step verify: running V2 post-configuration checks (parity mode)"

ok=0
fail=0
VERIFY_DIAGNOSTIC_FAILS=0
MOONRAKER_HTTP_OK=0
MOONRAKER_PROXY_HTTP_OK=0
MOONRAKER_PRINTER_INFO_OK=0
MOONRAKER_READY_OK=0
KLIPPER_STATE=""
KLIPPER_STATE_CLASS=""
KLIPPER_STATE_MESSAGE=""

# Блок 2: Вспомогательные функции подсчета/парсинга/проверок.
pass() {
  log_info "VERIFY $1: ok"
  ok=$((ok+1))
}

failf() {
  log_warn "VERIFY $1: FAIL"
  fail=$((fail+1))
}

diagnostic_failf() {
  log_warn "VERIFY $1: DIAGNOSTIC"
  VERIFY_DIAGNOSTIC_FAILS=$((VERIFY_DIAGNOSTIC_FAILS+1))
}

camera_failf() {
  if is_true "${TREED_CAMERA_REQUIRED:-0}"; then
    failf "$1"
  else
    diagnostic_failf "$1"
  fi
}

is_true() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

read_extlinux_append() {
  local cfg="$1"
  awk '
    BEGIN { in_l0 = 0; fallback = "" }
    /^[[:space:]]*label[[:space:]]+l0([[:space:]]|$)/ { in_l0 = 1; next }
    in_l0 && /^[[:space:]]*label[[:space:]]+/ { in_l0 = 0 }
    in_l0 && /^[[:space:]]*append[[:space:]]+/ {
      line = $0
      sub(/^[[:space:]]*append[[:space:]]+/, "", line)
      print line
      exit
    }
    /^[[:space:]]*append[[:space:]]+/ {
      line = $0
      sub(/^[[:space:]]*append[[:space:]]+/, "", line)
      fallback = line
    }
    END {
      if (fallback != "") {
        print fallback
      }
    }
  ' "${cfg}"
}

normalize_hdmi_mode() {
  local mode="${1:-auto}"
  case "${mode}" in
    auto|fixed|off)
      printf '%s\n' "${mode}"
      ;;
    *)
      log_warn "VERIFY invalid TREED_HDMI_MODE='${mode}', fallback to auto"
      printf 'auto\n'
      ;;
  esac
}

verify_video_token_policy() {
  local scope="$1"
  local args="$2"
  local mode="$3"
  local expected_video_mode="${4:-}"
  local expected_video_token=""

  if [ -n "${expected_video_mode}" ]; then
    expected_video_token="video=${expected_video_mode}"
  fi

  case "${mode}" in
    auto)
      if printf '%s\n' "${args}" | grep -qE "(^| )video=[^ ]+( |$)"; then
        failf "${scope} has no forced video= in TREED_HDMI_MODE=auto"
      else
        pass "${scope} has no forced video= in TREED_HDMI_MODE=auto"
      fi
      ;;
    fixed)
      if [ -n "${expected_video_token}" ]; then
        if printf '%s\n' "${args}" | grep -qE "(^| )${expected_video_token}( |$)"; then
          pass "${scope} ${expected_video_token}"
        else
          failf "${scope} ${expected_video_token}"
        fi
      else
        if printf '%s\n' "${args}" | grep -qE "(^| )video=[^ ]+( |$)"; then
          pass "${scope} has video= token (TREED_HDMI_MODE=fixed)"
        else
          failf "${scope} has video= token (TREED_HDMI_MODE=fixed)"
        fi
      fi
      ;;
    off)
      pass "${scope} video check skipped (TREED_HDMI_MODE=off)"
      ;;
  esac
}

check_required_service_active() {
  local unit="$1"
  local substate_allowed="${2:-running}"
  local active_failure_mode="${3:-fatal}"
  local state=""
  local substate=""
  local allowed=""

  if systemctl cat "${unit}" >/dev/null 2>&1; then
    pass "${unit} present"
  else
    failf "${unit} present"
    return 0
  fi

  if systemctl is-active --quiet "${unit}"; then
    pass "${unit} active"
  else
    state="$(systemctl is-active "${unit}" 2>/dev/null || true)"
    if [ "${active_failure_mode}" = "diagnostic" ]; then
      diagnostic_failf "${unit} active (state=${state:-unknown})"
    else
      failf "${unit} active (state=${state:-unknown})"
    fi
    return 0
  fi

  substate="$(systemctl show -p SubState --value "${unit}" 2>/dev/null | tr -d '\r\n')"
  for allowed in ${substate_allowed}; do
    if [ "${substate}" = "${allowed}" ]; then
      pass "${unit} substate ${substate}"
      return 0
    fi
  done

  if [ "${substate_allowed}" = "running" ]; then
    if [ "${active_failure_mode}" = "diagnostic" ]; then
      diagnostic_failf "${unit} substate running (state=${substate:-unknown})"
    else
      failf "${unit} substate running (state=${substate:-unknown})"
    fi
  else
    if [ "${active_failure_mode}" = "diagnostic" ]; then
      diagnostic_failf "${unit} substate one-of(${substate_allowed// /|}) (state=${substate:-unknown})"
    else
      failf "${unit} substate one-of(${substate_allowed// /|}) (state=${substate:-unknown})"
    fi
  fi
}

json_string_value() {
  local key="$1"
  local file="$2"
  sed -nE "s|.*\"${key}\"[[:space:]]*:[[:space:]]*\"([^\"]*)\".*|\\1|p" "${file}" \
    | head -n 1 \
    | sed 's|\\n| |g; s|\\"|"|g; s|\\\\|\\|g' || true
}

moonraker_server_info_check() {
  local check_name="$1"
  local url="$2"
  local scope="${3:-direct}"
  local tmp code retries attempt

  if ! command -v curl >/dev/null 2>&1; then
    failf "${check_name} (curl missing)"
    return 0
  fi

  tmp="$(mktemp "/tmp/treed_verify_server_info_XXXXXX.json")"
  retries="${TREED_MOONRAKER_HTTP_RETRIES:-30}"
  code=""

  for attempt in $(seq 1 "${retries}"); do
    if ! systemctl is-active --quiet moonraker.service; then
      sleep 1
      continue
    fi

    code="$(curl -m "${TREED_CAM_HTTP_TIMEOUT:-8}" -sS -o "${tmp}" -w '%{http_code}' "${url}" || true)"
    if [ "${code}" = "200" ] && grep -qE '"result"[[:space:]]*:' "${tmp}"; then
      pass "${check_name}"
      case "${scope}" in
        direct) MOONRAKER_HTTP_OK=1 ;;
        proxy) MOONRAKER_PROXY_HTTP_OK=1 ;;
      esac
      rm -f "${tmp}"
      return 0
    fi
    sleep 1
  done

  failf "${check_name} (http=${code:-n/a}, retries=${retries})"
  rm -f "${tmp}"
}

printer_info_check() {
  local check_name="$1"
  local url="$2"
  local tmp code retries attempt state state_message

  if ! command -v curl >/dev/null 2>&1; then
    failf "${check_name} (curl missing)"
    return 0
  fi

  tmp="$(mktemp "/tmp/treed_verify_printer_info_XXXXXX.json")"
  retries="${TREED_MOONRAKER_HTTP_RETRIES:-30}"
  code=""
  state=""
  state_message=""
  MOONRAKER_PRINTER_INFO_OK=0
  MOONRAKER_READY_OK=0

  for attempt in $(seq 1 "${retries}"); do
    code="$(curl -m "${TREED_CAM_HTTP_TIMEOUT:-8}" -sS -o "${tmp}" -w '%{http_code}' "${url}" || true)"
    if [ "${code}" = "200" ] && grep -qE '"result"[[:space:]]*:' "${tmp}"; then
      MOONRAKER_PRINTER_INFO_OK=1
      state="$(json_string_value "state" "${tmp}" | tr -d '\r\n')"
      state_message="$(json_string_value "state_message" "${tmp}" | tr -d '\r\n')"
      KLIPPER_STATE="${state:-unknown}"
      KLIPPER_STATE_MESSAGE="${state_message:-}"
      KLIPPER_STATE_CLASS="${KLIPPER_STATE}"
      if printf '%s\n' "${KLIPPER_STATE} ${KLIPPER_STATE_MESSAGE}" | grep -Eiq 'shutdown|thermal|heater|temperature|adc'; then
        KLIPPER_STATE_CLASS="shutdown"
      fi
      break
    fi
    sleep 1
  done

  if [ "${MOONRAKER_PRINTER_INFO_OK}" != "1" ]; then
    if is_true "${TREED_REQUIRE_KLIPPER_READY:-0}"; then
      failf "${check_name} (http=${code:-n/a}, retries=${retries})"
    else
      log_warn "VERIFY ${check_name}: unavailable (http=${code:-n/a}, retries=${retries}, TREED_REQUIRE_KLIPPER_READY=0)"
      pass "Klipper ready not required (TREED_REQUIRE_KLIPPER_READY=0, printer_info unavailable)"
    fi
    rm -f "${tmp}"
    return 0
  fi

  case "${KLIPPER_STATE_CLASS:-${KLIPPER_STATE}}" in
    ready)
      pass "${check_name} ready"
      MOONRAKER_READY_OK=1
      ;;
    startup|error|shutdown|unknown|"")
      log_warn "VERIFY ${check_name}: Klipper state=${KLIPPER_STATE:-unknown}, class=${KLIPPER_STATE_CLASS:-unknown}, state_message=${KLIPPER_STATE_MESSAGE:-missing}"
      if is_true "${TREED_REQUIRE_KLIPPER_READY:-0}"; then
        failf "Klipper ready required (state=${KLIPPER_STATE:-unknown}, class=${KLIPPER_STATE_CLASS:-unknown}, state_message=${KLIPPER_STATE_MESSAGE:-missing})"
      else
        pass "Klipper ready not required (TREED_REQUIRE_KLIPPER_READY=0, state=${KLIPPER_STATE:-unknown}, class=${KLIPPER_STATE_CLASS:-unknown})"
      fi
      ;;
    *)
      log_warn "VERIFY ${check_name}: unexpected Klipper state=${KLIPPER_STATE}, class=${KLIPPER_STATE_CLASS:-unknown}, state_message=${KLIPPER_STATE_MESSAGE:-missing}"
      if is_true "${TREED_REQUIRE_KLIPPER_READY:-0}"; then
        failf "Klipper ready required (unexpected state=${KLIPPER_STATE}, class=${KLIPPER_STATE_CLASS:-unknown})"
      else
        pass "Klipper ready not required (TREED_REQUIRE_KLIPPER_READY=0, state=${KLIPPER_STATE}, class=${KLIPPER_STATE_CLASS:-unknown})"
      fi
      ;;
  esac

  rm -f "${tmp}"
}

klipper_mcu_journal_clean_check() {
  local check_name="$1"
  local unit="klipper.service"
  local since=""
  local tmp=""
  local patterns=""

  if ! command -v journalctl >/dev/null 2>&1; then
    diagnostic_failf "${check_name} (journalctl missing)"
    return 0
  fi

  since="$(systemctl show -p ActiveEnterTimestamp --value "${unit}" 2>/dev/null | tr -d '\r\n')"
  case "${since}" in
    ""|"n/a") since="-20 min" ;;
  esac

  tmp="$(mktemp "/tmp/treed_verify_klipper_journal_XXXXXX.log")"
  if journalctl -u "${unit}" --since "${since}" --no-pager > "${tmp}" 2>/dev/null; then
    :
  else
    diagnostic_failf "${check_name} (cannot read journal since=${since})"
    rm -f "${tmp}"
    return 0
  fi

  patterns="Lost communication with MCU|Timeout with MCU|MCU 'mcu' shutdown|MCU 'EBBCan' shutdown|mcu[.]error|Error configuring printer|Unable to open serial port|mcu 'mcu': Unable to connect|mcu 'EBBCan': Unable to connect"
  if grep -Eiq "${patterns}" "${tmp}"; then
    if is_true "${TREED_REQUIRE_KLIPPER_READY:-0}"; then
      failf "${check_name} (mcu errors found since=${since})"
    else
      log_warn "VERIFY ${check_name}: runtime MCU errors found since=${since} (TREED_REQUIRE_KLIPPER_READY=0, not blocking)"
    fi
  else
    pass "${check_name}"
  fi

  rm -f "${tmp}"
}

klipper_can_mcus_connected_check() {
  local check_name="$1"
  local list_url="http://127.0.0.1:7125/printer/objects/list"
  local query_base_url="http://127.0.0.1:7125/printer/objects/query"
  local retries="${TREED_MOONRAKER_HTTP_RETRIES:-30}"
  local list_tmp query_tmp code attempt query_string encoded
  local -a expected_mcus
  local mcu_name

  if ! command -v curl >/dev/null 2>&1; then
    failf "${check_name} (curl missing)"
    return 0
  fi

  expected_mcus=("mcu" "mcu EBBCan")
  if [ "${TREED_EDDY_ENABLED:-1}" = "1" ]; then
    expected_mcus+=("mcu eddy")
  fi

  list_tmp="$(mktemp "/tmp/treed_verify_mcu_objects_XXXXXX.json")"
  code=""
  for attempt in $(seq 1 "${retries}"); do
    code="$(curl -m "${TREED_CAM_HTTP_TIMEOUT:-8}" -sS -o "${list_tmp}" -w '%{http_code}' "${list_url}" || true)"
    if [ "${code}" = "200" ] && grep -qE '"objects"[[:space:]]*:' "${list_tmp}"; then
      break
    fi
    sleep 1
  done

  if [ "${code}" != "200" ] || ! grep -qE '"objects"[[:space:]]*:' "${list_tmp}"; then
    failf "${check_name}: cannot read object list (http=${code:-n/a}, retries=${retries})"
    rm -f "${list_tmp}"
    return 0
  fi

  for mcu_name in "${expected_mcus[@]}"; do
    if grep -Fq "\"${mcu_name}\"" "${list_tmp}"; then
      pass "${check_name}: object '${mcu_name}' present"
    else
      failf "${check_name}: object '${mcu_name}' missing"
    fi
  done

  query_string=""
  for mcu_name in "${expected_mcus[@]}"; do
    encoded="${mcu_name// /%20}"
    if [ -n "${query_string}" ]; then
      query_string="${query_string}&"
    fi
    query_string="${query_string}${encoded}"
  done

  query_tmp="$(mktemp "/tmp/treed_verify_mcu_query_XXXXXX.json")"
  code=""
  for attempt in $(seq 1 "${retries}"); do
    code="$(
      curl -m "${TREED_CAM_HTTP_TIMEOUT:-8}" -sS -o "${query_tmp}" -w '%{http_code}' \
        "${query_base_url}?${query_string}" || true
    )"
    if [ "${code}" = "200" ] && grep -qE '"status"[[:space:]]*:' "${query_tmp}"; then
      break
    fi
    sleep 1
  done

  if [ "${code}" != "200" ] || ! grep -qE '"status"[[:space:]]*:' "${query_tmp}"; then
    failf "${check_name}: cannot query MCU status (http=${code:-n/a}, retries=${retries})"
    rm -f "${list_tmp}" "${query_tmp}"
    return 0
  fi

  for mcu_name in "${expected_mcus[@]}"; do
    if grep -Fq "\"${mcu_name}\":" "${query_tmp}"; then
      pass "${check_name}: MCU '${mcu_name}' online"
    else
      failf "${check_name}: MCU '${mcu_name}' missing in status"
    fi
  done

  rm -f "${list_tmp}" "${query_tmp}"
}

host_network_status_check() {
  local check_name="$1"
  local url="$2"
  local tmp code retries attempt

  if command -v nmcli >/dev/null 2>&1; then
    pass "nmcli present"
  else
    failf "nmcli present"
    return 0
  fi

  if ! command -v curl >/dev/null 2>&1; then
    failf "${check_name} (curl missing)"
    return 0
  fi

  tmp="$(mktemp "/tmp/treed_verify_host_network_XXXXXX.json")"
  retries="${TREED_MOONRAKER_HTTP_RETRIES:-30}"
  code=""

  for attempt in $(seq 1 "${retries}"); do
    code="$(curl -m "${TREED_CAM_HTTP_TIMEOUT:-8}" -sS -o "${tmp}" -w '%{http_code}' "${url}" || true)"
    if [ "${code}" = "200" ] \
      && grep -qE '"available"[[:space:]]*:' "${tmp}" \
      && grep -qE '"networks"[[:space:]]*:' "${tmp}" \
      && grep -qE '"message"[[:space:]]*:' "${tmp}"; then
      pass "${check_name}"
      rm -f "${tmp}"
      return 0
    fi
    sleep 1
  done

  failf "${check_name} (http=${code:-n/a}, retries=${retries})"
  rm -f "${tmp}"
}

ui_system_capabilities_check() {
  local check_name="$1"
  local url="$2"
  local tmp code retries attempt

  if ! command -v curl >/dev/null 2>&1; then
    failf "${check_name} (curl missing)"
    return 0
  fi

  tmp="$(mktemp "/tmp/treed_verify_ui_system_capabilities_XXXXXX.json")"
  retries="${TREED_MOONRAKER_HTTP_RETRIES:-30}"
  code=""

  for attempt in $(seq 1 "${retries}"); do
    code="$(curl -m "${TREED_CAM_HTTP_TIMEOUT:-8}" -sS -o "${tmp}" -w '%{http_code}' "${url}" || true)"
    if [ "${code}" = "200" ] \
      && grep -qE '"gcode_macro _TREED_SYSTEM_POWER"[[:space:]]*:[[:space:]]*\{[^}]*"enabled"[[:space:]]*:[[:space:]]*1(\.0+)?' "${tmp}" \
      && grep -qE '"gcode_macro _TREED_SERVICE_COMMANDS"[[:space:]]*:[[:space:]]*\{[^}]*"enabled"[[:space:]]*:[[:space:]]*1(\.0+)?' "${tmp}"; then
      pass "${check_name}"
      rm -f "${tmp}"
      return 0
    fi
    sleep 1
  done

  failf "${check_name} (http=${code:-n/a}, retries=${retries})"
  rm -f "${tmp}"
}

http_snapshot_check() {
  local check_name="$1"
  local url="$2"
  local tmp code retries attempt
  tmp="$(mktemp "/tmp/treed_verify_cam_XXXXXX.jpg")"
  retries="${TREED_CAM_HTTP_RETRIES:-3}"
  code=""
  for attempt in $(seq 1 "${retries}"); do
    code="$(curl -m "${TREED_CAM_HTTP_TIMEOUT:-8}" -sS -o "${tmp}" -w '%{http_code}' "${url}" || true)"
    if [ "${code}" = "200" ] && [ -s "${tmp}" ]; then
      pass "${check_name}"
      rm -f "${tmp}"
      return 0
    fi
    sleep 1
  done
  camera_failf "${check_name} (http=${code:-n/a})"
  rm -f "${tmp}"
}

moonraker_webcams_check() {
  local check_name="$1"
  local url="$2"
  local tmp code retries attempt
  tmp="$(mktemp "/tmp/treed_verify_webcams_XXXXXX.json")"
  retries="${TREED_MOONRAKER_HTTP_RETRIES:-30}"
  code=""

  for attempt in $(seq 1 "${retries}"); do
    code="$(curl -m "${TREED_CAM_HTTP_TIMEOUT:-8}" -s -o "${tmp}" -w '%{http_code}' "${url}" || true)"
    if [ "${code}" = "200" ] \
      && grep -qE '"name"[[:space:]]*:[[:space:]]*"treed"' "${tmp}" \
      && grep -qE '"service"[[:space:]]*:[[:space:]]*"mjpegstreamer"' "${tmp}" \
      && grep -qE '"stream_url"[[:space:]]*:[[:space:]]*"/webcam/\?action=stream"' "${tmp}"; then
      pass "${check_name}"
      rm -f "${tmp}"
      return 0
    fi
    sleep 1
  done

  camera_failf "${check_name} (http=${code:-n/a})"
  rm -f "${tmp}"
}

moonraker_gcode_ok_check() {
  local check_name="$1"
  local script="$2"
  local url="http://127.0.0.1:7125/printer/gcode/script"
  local tmp code retries attempt

  if ! command -v curl >/dev/null 2>&1; then
    failf "${check_name} (curl missing)"
    return 0
  fi

  tmp="$(mktemp "/tmp/treed_verify_gcode_XXXXXX.json")"
  retries="${TREED_MOONRAKER_HTTP_RETRIES:-30}"
  code=""

  for attempt in $(seq 1 "${retries}"); do
    code="$(
      curl -m "${TREED_CAM_HTTP_TIMEOUT:-8}" -sS -o "${tmp}" -w '%{http_code}' \
        -H 'Content-Type: application/json' \
        -X POST "${url}" \
        --data "{\"script\":\"${script}\"}" || true
    )"

    if [ "${code}" = "200" ] && grep -qE '"result"[[:space:]]*:[[:space:]]*"ok"' "${tmp}"; then
      pass "${check_name}"
      rm -f "${tmp}"
      return 0
    fi

    sleep 1
  done

  failf "${check_name} (http=${code:-n/a}, retries=${retries})"
  rm -f "${tmp}"
}

http_status_ok_check() {
  local check_name="$1"
  local url="$2"
  local expected_code="${3:-200}"
  local retries="${4:-5}"
  local tmp code attempt

  if ! command -v curl >/dev/null 2>&1; then
    failf "${check_name} (curl missing)"
    return 0
  fi

  tmp="$(mktemp "/tmp/treed_verify_http_XXXXXX.body")"
  code=""

  for attempt in $(seq 1 "${retries}"); do
    code="$(curl -m "${TREED_CAM_HTTP_TIMEOUT:-8}" -sS -o "${tmp}" -w '%{http_code}' "${url}" || true)"
    if [ "${code}" = "${expected_code}" ]; then
      pass "${check_name}"
      rm -f "${tmp}"
      return 0
    fi
    sleep 1
  done

  failf "${check_name} (http=${code:-n/a}, expected=${expected_code})"
  rm -f "${tmp}"
}

normalize_path_for_compare() {
  local path="$1"
  if [ -e "${path}" ]; then
    readlink -f "${path}" 2>/dev/null || printf '%s\n' "${path}"
  else
    printf '%s\n' "${path}"
  fi
}

read_moonraker_mainsail_updater_path() {
  local cfg="$1"
  awk '
    BEGIN { in_section = 0 }
    /^[[:space:]]*\[update_manager mainsail\][[:space:]]*$/ { in_section = 1; next }
    in_section && /^[[:space:]]*\[[^]]+\][[:space:]]*$/ { in_section = 0 }
    in_section && /^[[:space:]]*path[[:space:]]*:/ {
      value = $0
      sub(/^[[:space:]]*path[[:space:]]*:[[:space:]]*/, "", value)
      sub(/[[:space:]]*(#.*)?$/, "", value)
      print value
      exit
    }
  ' "${cfg}"
}

read_nginx_mainsail_root() {
  local cfg="$1"
  awk '
    /^[[:space:]]*root[[:space:]]+/ {
      value = $0
      sub(/^[[:space:]]*root[[:space:]]+/, "", value)
      sub(/[[:space:]]*;[[:space:]]*$/, "", value)
      print value
      exit
    }
  ' "${cfg}"
}

mainsail_web_path_alignment_check() {
  local expected_path updater_path nginx_root expected_norm updater_norm nginx_norm

  expected_path="${TREED_MAINSAIL_WEB_PATH}"
  expected_norm="$(normalize_path_for_compare "${expected_path}")"

  if [ -f "${MOONRAKER_BASE_CORE_RUNTIME}" ]; then
    updater_path="$(read_moonraker_mainsail_updater_path "${MOONRAKER_BASE_CORE_RUNTIME}" | tr -d '\r\n' || true)"
    if [ -n "${updater_path}" ]; then
      updater_norm="$(normalize_path_for_compare "${updater_path}")"
      if [ "${updater_norm}" = "${expected_norm}" ]; then
        pass "Moonraker Mainsail updater path matches web root (${updater_path})"
      else
        failf "Moonraker Mainsail updater path matches web root (path=${updater_path:-missing}, expected=${expected_path})"
      fi
    else
      failf "Moonraker Mainsail updater path present (${MOONRAKER_BASE_CORE_RUNTIME})"
    fi
  else
    failf "Moonraker base core config present (${MOONRAKER_BASE_CORE_RUNTIME})"
  fi

  if [ -f "${TREED_MAINSAIL_NGINX_SITE_ENABLED}" ] || [ -L "${TREED_MAINSAIL_NGINX_SITE_ENABLED}" ]; then
    nginx_root="$(read_nginx_mainsail_root "${TREED_MAINSAIL_NGINX_SITE_ENABLED}" | tr -d '\r\n' || true)"
    if [ -n "${nginx_root}" ]; then
      nginx_norm="$(normalize_path_for_compare "${nginx_root}")"
      if [ "${nginx_norm}" = "${expected_norm}" ]; then
        pass "nginx Mainsail root matches web root (${nginx_root})"
      else
        failf "nginx Mainsail root matches web root (root=${nginx_root:-missing}, expected=${expected_path})"
      fi
    else
      failf "nginx Mainsail root present (${TREED_MAINSAIL_NGINX_SITE_ENABLED})"
    fi
  else
    failf "nginx Mainsail site enabled (${TREED_MAINSAIL_NGINX_SITE_ENABLED})"
  fi
}

# Блок 3: Подготовка boot-контекста и runtime-переменных.
BOOT_DIR="${BOOT_DIR:-$(detect_boot_dir)}"
TREED_BOOT_BACKEND="${TREED_BOOT_BACKEND:-$(detect_boot_backend "${BOOT_DIR}")}"
CMDLINE_FILE="${CMDLINE_FILE:-$(detect_cmdline_file "${BOOT_DIR}")}"
CONFIG_FILE="${CONFIG_FILE:-$(detect_config_file "${BOOT_DIR}")}"
ARMBIAN_ENV_FILE="${ARMBIAN_ENV_FILE:-$(detect_armbian_env_file "${BOOT_DIR}")}"
EXTLINUX_FILE="${EXTLINUX_FILE:-$(detect_extlinux_file "${BOOT_DIR}")}"
TREED_HDMI_MODE="${TREED_HDMI_MODE:-auto}"
TREED_HDMI_MODE="$(normalize_hdmi_mode "${TREED_HDMI_MODE}")"
TREED_HDMI_VIDEO_MODE_RAW="${TREED_HDMI_VIDEO_MODE:-}"
TREED_ARMBIAN_VIDEO_MODE_RAW="${TREED_ARMBIAN_VIDEO_MODE:-}"
KVER="$(uname -r)"
INITRD="${BOOT_DIR}/initrd.img-${KVER}"

PI_USER="${PI_USER:-pi}"
PI_HOME="${PI_HOME:-/home/${PI_USER}}"
CAN_SETUP_ENV_FILE="${TREED_CAN_SETUP_ENV_FILE:-/etc/default/treed-can-setup}"
CAN_ENV_IFACE=""
CAN_ENV_BITRATE=""
CAN_ENV_TXQUEUE=""
CAN_ENV_RESTART_MS=""
if [ -f "${CAN_SETUP_ENV_FILE}" ]; then
  CAN_ENV_IFACE="$(sed -nE 's|^[[:space:]]*TREED_CAN_IFACE=([A-Za-z0-9_.:-]+)[[:space:]]*$|\1|p' "${CAN_SETUP_ENV_FILE}" | tail -n1 | tr -d '\r\n')"
  CAN_ENV_BITRATE="$(sed -nE 's|^[[:space:]]*TREED_CAN_BITRATE=([0-9]+)[[:space:]]*$|\1|p' "${CAN_SETUP_ENV_FILE}" | tail -n1 | tr -d '\r\n')"
  CAN_ENV_TXQUEUE="$(sed -nE 's|^[[:space:]]*TREED_CAN_TXQUEUE=([0-9]+)[[:space:]]*$|\1|p' "${CAN_SETUP_ENV_FILE}" | tail -n1 | tr -d '\r\n')"
  CAN_ENV_RESTART_MS="$(sed -nE 's|^[[:space:]]*TREED_CAN_RESTART_MS=([0-9]+)[[:space:]]*$|\1|p' "${CAN_SETUP_ENV_FILE}" | tail -n1 | tr -d '\r\n')"
fi
TREED_MAIN_MCU_CANBUS_UUID="${TREED_MAIN_MCU_CANBUS_UUID:-d372e54bf965}"
TREED_CAN_IFACE="${TREED_CAN_IFACE:-${CAN_ENV_IFACE:-can0}}"
TREED_CAN_BITRATE="${TREED_CAN_BITRATE:-${CAN_ENV_BITRATE:-1000000}}"
TREED_CAN_TXQUEUE="${TREED_CAN_TXQUEUE:-${CAN_ENV_TXQUEUE:-1024}}"
TREED_CAN_RESTART_MS="${TREED_CAN_RESTART_MS:-${CAN_ENV_RESTART_MS:-100}}"
TREED_EDDY_ENABLED="${TREED_EDDY_ENABLED:-1}"

TREED_REQUIRE_KLIPPER_READY="${TREED_REQUIRE_KLIPPER_READY:-0}"
case "${TREED_REQUIRE_KLIPPER_READY}" in
  0|1|true|TRUE|yes|YES|on|ON|false|FALSE|no|NO|off|OFF) ;;
  *)
    failf "TREED_REQUIRE_KLIPPER_READY is valid (0|1, current=${TREED_REQUIRE_KLIPPER_READY})"
    TREED_REQUIRE_KLIPPER_READY="0"
    ;;
esac

MOONRAKER_SERVER_INFO_URL="http://127.0.0.1:7125/server/info"
MOONRAKER_PRINTER_INFO_URL="http://127.0.0.1:7125/printer/info"
MOONRAKER_HOST_NETWORK_STATUS_URL="http://127.0.0.1:7125/server/treed/network/status"
MOONRAKER_UI_SYSTEM_CAPABILITIES_URL="http://127.0.0.1:7125/printer/objects/query?gcode_macro%20_TREED_SYSTEM_POWER&gcode_macro%20_TREED_SERVICE_COMMANDS"
WEBCAM_API_URL="http://127.0.0.1:7125/server/webcams/list"
CAN_UNIT="treed-can-setup.service"
TREED_MAINSAIL_WEB_PATH="${TREED_MAINSAIL_WEB_PATH:-/var/www/mainsail}"
TREED_MAINSAIL_NGINX_SITE_ENABLED="${TREED_MAINSAIL_NGINX_SITE_ENABLED:-/etc/nginx/sites-enabled/mainsail}"
MOONRAKER_BASE_CORE_RUNTIME="${PI_HOME}/printer_data/config/moonraker/base/00-core.conf"
MAINSAIL_HTTP_ROOT_URL="http://127.0.0.1/"
MAINSAIL_MOONRAKER_PROXY_INFO_URL="http://127.0.0.1/server/info"

KS_OVERRIDE_FILE="/etc/systemd/system/KlipperScreen.service.d/override.conf"
TREED_UI_MODE="$(resolve_treed_ui_mode ts)"
TS_UNIT="treed-shell.service"
KS_UNIT="KlipperScreen.service"
TREED_KLIPPERSCREEN_HOME_RAW="${TREED_KLIPPERSCREEN_HOME:-}"
if [ -n "${TREED_KLIPPERSCREEN_HOME_RAW}" ]; then
  TREED_KLIPPERSCREEN_HOME="${TREED_KLIPPERSCREEN_HOME_RAW}"
  log_info "VERIFY KlipperScreen home forced via TREED_KLIPPERSCREEN_HOME=${TREED_KLIPPERSCREEN_HOME}"
else
  TREED_KLIPPERSCREEN_HOME="$(detect_klipperscreen_home "${PI_HOME}/KlipperScreen" || true)"
  if [ -n "${TREED_KLIPPERSCREEN_HOME}" ]; then
    log_info "VERIFY KlipperScreen home resolved as ${TREED_KLIPPERSCREEN_HOME}"
  else
    log_info "VERIFY KlipperScreen home unresolved (service may be absent)"
  fi
fi

# Блок 4: Проверки initramfs/boot backend/cmdline.
if [ -f "${INITRD}" ]; then
  pass "initramfs file ${INITRD}"
else
  failf "initramfs file (${INITRD} missing)"
fi

case "${TREED_BOOT_BACKEND}" in
  rpi)
    pass "boot backend rpi"

    if [ -f "${CONFIG_FILE}" ] \
      && grep -Fq "initramfs initrd.img-${KVER} followkernel" "${CONFIG_FILE}"; then
      pass "config.txt initramfs initrd.img-${KVER} followkernel"
    else
      failf "config.txt initramfs initrd.img-${KVER} followkernel"
    fi

    gm="$(grep -E "^gpu_mem=" "${CONFIG_FILE}" 2>/dev/null | tail -n1 | cut -d= -f2)"
    case "${gm}" in ''|*[!0-9]*) gm=0;; esac
    if [ "${gm:-0}" -ge 96 ]; then
      pass "gpu_mem >= 96"
    else
      failf "gpu_mem >= 96"
    fi

    CMDLINE_CONTENT=""
    if [ -n "${CMDLINE_FILE}" ] && [ -f "${CMDLINE_FILE}" ]; then
      CMDLINE_CONTENT="$(tr -d '\n' < "${CMDLINE_FILE}" 2>/dev/null || true)"
      for tok in quiet splash plymouth.ignore-serial-consoles logo.nologo vt.global_cursor_default=0 consoleblank=0 loglevel=3 vt.handoff=7 usbcore.autosuspend=-1; do
        if printf '%s\n' "${CMDLINE_CONTENT}" | grep -qE "(^| )${tok}( |$)"; then
          pass "cmdline token ${tok}"
        else
          failf "cmdline token ${tok}"
        fi
      done

      if printf '%s\n' "${CMDLINE_CONTENT}" | grep -q "plymouth.enable=0"; then
        failf "cmdline has no plymouth.enable=0"
      else
        pass "cmdline has no plymouth.enable=0"
      fi

      if [ "$(wc -l < "${CMDLINE_FILE}" 2>/dev/null || echo 2)" -eq 1 ]; then
        pass "cmdline one-line"
      else
        failf "cmdline one-line"
      fi
    else
      failf "cmdline file present for rpi backend"
    fi
    ;;

  armbian)
    pass "boot backend armbian"

    if [ -f "${ARMBIAN_ENV_FILE}" ]; then
      pass "armbianEnv.txt present (${ARMBIAN_ENV_FILE})"
    else
      failf "armbianEnv.txt present (${ARMBIAN_ENV_FILE:-missing})"
    fi

    TREED_ARMBIAN_VERBOSITY="${TREED_ARMBIAN_VERBOSITY:-1}"
    TREED_ARMBIAN_BOOTLOGO="${TREED_ARMBIAN_BOOTLOGO:-true}"
    TREED_ARMBIAN_VIDEO_MODE="${TREED_ARMBIAN_VIDEO_MODE:-HDMI-A-1:960x544@60}"

    if [ -f "${ARMBIAN_ENV_FILE}" ]; then
      armbian_verbosity="$(get_armbian_env_value "${ARMBIAN_ENV_FILE}" "verbosity" | tr -d '"' | tr -d '\r\n')"
      if [ "${armbian_verbosity}" = "${TREED_ARMBIAN_VERBOSITY}" ]; then
        pass "armbianEnv verbosity=${TREED_ARMBIAN_VERBOSITY}"
      else
        failf "armbianEnv verbosity=${TREED_ARMBIAN_VERBOSITY} (current=${armbian_verbosity:-missing})"
      fi

      armbian_bootlogo="$(get_armbian_env_value "${ARMBIAN_ENV_FILE}" "bootlogo" | tr -d '"' | tr -d '\r\n')"
      if [ "${armbian_bootlogo}" = "${TREED_ARMBIAN_BOOTLOGO}" ]; then
        pass "armbianEnv bootlogo=${TREED_ARMBIAN_BOOTLOGO}"
      else
        failf "armbianEnv bootlogo=${TREED_ARMBIAN_BOOTLOGO} (current=${armbian_bootlogo:-missing})"
      fi

      armbian_extraargs="$(get_armbian_env_value "${ARMBIAN_ENV_FILE}" "extraargs")"
      armbian_extraargs="${armbian_extraargs#\"}"
      armbian_extraargs="${armbian_extraargs%\"}"
      for tok in quiet splash plymouth.ignore-serial-consoles logo.nologo vt.global_cursor_default=0 consoleblank=0 loglevel=3 vt.handoff=7 usbcore.autosuspend=-1; do
        if printf '%s\n' "${armbian_extraargs}" | grep -qE "(^| )${tok}( |$)"; then
          pass "armbian extraargs token ${tok}"
        else
          failf "armbian extraargs token ${tok}"
        fi
      done

      verify_video_token_policy \
        "armbian extraargs" \
        "${armbian_extraargs}" \
        "${TREED_HDMI_MODE}" \
        "${TREED_HDMI_VIDEO_MODE_RAW:-${TREED_ARMBIAN_VIDEO_MODE_RAW:-}}"

      if printf '%s\n' "${armbian_extraargs}" | grep -q "plymouth.enable=0"; then
        failf "armbian extraargs has no plymouth.enable=0"
      else
        pass "armbian extraargs has no plymouth.enable=0"
      fi
    fi

    if [ -f /proc/cmdline ]; then
      proc_cmdline="$(tr -d '\n' < /proc/cmdline)"
      for tok in quiet splash consoleblank=0; do
        if printf '%s\n' "${proc_cmdline}" | grep -qE "(^| )${tok}( |$)"; then
          pass "proc cmdline token ${tok}"
        else
          failf "proc cmdline token ${tok}"
        fi
      done
    else
      failf "proc cmdline readable"
    fi
    ;;

  extlinux)
    pass "boot backend extlinux"

    if [ -f "${EXTLINUX_FILE}" ]; then
      pass "extlinux.conf present (${EXTLINUX_FILE})"
    else
      failf "extlinux.conf present (${EXTLINUX_FILE:-missing})"
    fi

    TREED_ARMBIAN_VIDEO_MODE="${TREED_ARMBIAN_VIDEO_MODE:-HDMI-A-1:960x544@60}"

    extlinux_append=""
    if [ -f "${EXTLINUX_FILE}" ]; then
      extlinux_append="$(read_extlinux_append "${EXTLINUX_FILE}" | tr -d '\r\n')"
    fi

    if [ -n "${extlinux_append}" ]; then
      for tok in quiet splash plymouth.ignore-serial-consoles logo.nologo vt.global_cursor_default=0 consoleblank=0 loglevel=3 vt.handoff=7 usbcore.autosuspend=-1; do
        if printf '%s\n' "${extlinux_append}" | grep -qE "(^| )${tok}( |$)"; then
          pass "extlinux append token ${tok}"
        else
          failf "extlinux append token ${tok}"
        fi
      done

      verify_video_token_policy \
        "extlinux append" \
        "${extlinux_append}" \
        "${TREED_HDMI_MODE}" \
        "${TREED_HDMI_VIDEO_MODE_RAW:-${TREED_ARMBIAN_VIDEO_MODE_RAW:-}}"

      if printf '%s\n' "${extlinux_append}" | grep -q "plymouth.enable=0"; then
        failf "extlinux append has no plymouth.enable=0"
      else
        pass "extlinux append has no plymouth.enable=0"
      fi
    else
      failf "extlinux append line present"
    fi

    if [ -f /proc/cmdline ]; then
      proc_cmdline="$(tr -d '\n' < /proc/cmdline)"
      for tok in quiet splash consoleblank=0; do
        if printf '%s\n' "${proc_cmdline}" | grep -qE "(^| )${tok}( |$)"; then
          pass "proc cmdline token ${tok}"
        else
          failf "proc cmdline token ${tok}"
        fi
      done
    else
      failf "proc cmdline readable"
    fi
    ;;

  *)
    failf "supported boot backend (current=${TREED_BOOT_BACKEND})"
    ;;
esac

# Блок 5: Проверки политики getty@tty1 и plymouth-quit unit.
TREED_MASK_TTY1="${TREED_MASK_TTY1:-1}"
if out="$(systemctl is-enabled getty@tty1.service 2>&1)"; then
  rc=0
else
  rc=$?
fi
state="$(printf '%s' "${out}" | head -n 1 | tr -d '\r\n')"
case "${state}" in
  enabled|disabled|static|indirect|generated|masked|masked-runtime) ;;
  *)
    log_error "verify: systemctl is-enabled getty@tty1.service failed rc=${rc}: ${out}"
    exit 1
    ;;
esac
if [ "${TREED_MASK_TTY1}" = "0" ]; then
  case "${state}" in
    masked|masked-runtime)
      failf "getty@tty1 should be unmasked when TREED_MASK_TTY1=0 (state=${state})"
      ;;
    enabled|disabled|static|indirect|generated)
      pass "getty@tty1 unmasked (TREED_MASK_TTY1=0, state=${state})"
      ;;
    *)
      failf "getty@tty1 should be unmasked when TREED_MASK_TTY1=0 (state=${state})"
      ;;
  esac
else
  if [ "${state}" = "masked" ] || [ "${state}" = "masked-runtime" ]; then
    pass "getty@tty1 masked (TREED_MASK_TTY1=1, state=${state})"
  else
    failf "getty@tty1 should be masked when TREED_MASK_TTY1=1 (state=${state})"
  fi
fi

for unit in plymouth-quit.service plymouth-quit-wait.service; do
  if uout="$(systemctl is-enabled "${unit}" 2>&1)"; then
    urc=0
  else
    urc=$?
  fi
  s="$(printf '%s' "${uout}" | head -n 1 | tr -d '\r\n')"
  case "${s}" in
    enabled|disabled|static|indirect|generated|masked|masked-runtime) ;;
    *)
      log_error "verify: systemctl is-enabled ${unit} failed rc=${urc}: ${uout}"
      exit 1
      ;;
  esac
  if [ "${s}" = "masked" ] || [ "${s}" = "masked-runtime" ]; then
    failf "${unit} should be unmasked (state=${s})"
  else
    pass "${unit} unmasked (state=${s})"
  fi
done

# Блок 6: Проверки timezone/NTP через timedatectl.
if command -v timedatectl >/dev/null 2>&1; then
  TREED_SET_TIMEZONE="${TREED_SET_TIMEZONE:-1}"
  TREED_TIMEZONE="${TREED_TIMEZONE:-Europe/Moscow}"
  TREED_ENABLE_NTP="${TREED_ENABLE_NTP:-1}"

  if is_true "${TREED_SET_TIMEZONE}"; then
    current_tz="$(timedatectl show -p Timezone --value 2>/dev/null | tr -d '\r\n')"
    if [ "${current_tz}" = "${TREED_TIMEZONE}" ]; then
      pass "system timezone ${TREED_TIMEZONE}"
    else
      failf "system timezone ${TREED_TIMEZONE} (current=${current_tz:-unknown})"
    fi
  else
    log_info "VERIFY timezone check skipped (TREED_SET_TIMEZONE=${TREED_SET_TIMEZONE})"
  fi

  if is_true "${TREED_ENABLE_NTP}"; then
    ntp_state="$(timedatectl show -p NTP --value 2>/dev/null | tr -d '\r\n')"
    if [ "${ntp_state}" = "yes" ]; then
      pass "timedatectl NTP enabled"
    else
      failf "timedatectl NTP enabled (state=${ntp_state:-unknown})"
    fi
  else
    log_info "VERIFY NTP check skipped (TREED_ENABLE_NTP=${TREED_ENABLE_NTP})"
  fi
else
  failf "timedatectl present"
fi

# Блок 7: Проверки сервисов, Moonraker API и CAN-интерфейса.
check_required_service_active "klipper.service"
check_required_service_active "moonraker.service"
check_required_service_active "nginx.service"
check_required_service_active "${CAN_UNIT}" "running exited"

if [ -f "${TREED_MAINSAIL_WEB_PATH}/release_info.json" ]; then
  pass "Mainsail web root release_info present (${TREED_MAINSAIL_WEB_PATH}/release_info.json)"
else
  failf "Mainsail web root release_info present (${TREED_MAINSAIL_WEB_PATH}/release_info.json)"
fi

if [ -f "${TREED_MAINSAIL_WEB_PATH}/index.html" ]; then
  pass "Mainsail web root index present (${TREED_MAINSAIL_WEB_PATH}/index.html)"
else
  failf "Mainsail web root index present (${TREED_MAINSAIL_WEB_PATH}/index.html)"
fi

mainsail_web_path_alignment_check
http_status_ok_check "nginx HTTP root responds 200" "${MAINSAIL_HTTP_ROOT_URL}" "200" "8"
moonraker_server_info_check "Moonraker HTTP 127.0.0.1:7125 /server/info" "${MOONRAKER_SERVER_INFO_URL}" "direct"
moonraker_server_info_check "nginx proxy /server/info" "${MAINSAIL_MOONRAKER_PROXY_INFO_URL}" "proxy"
printer_info_check "Klipper /printer/info" "${MOONRAKER_PRINTER_INFO_URL}"
ui_system_capabilities_check "TreeD UI system capability macros enabled" "${MOONRAKER_UI_SYSTEM_CAPABILITIES_URL}"
host_network_status_check "TreeD host network /server/treed/network/status" "${MOONRAKER_HOST_NETWORK_STATUS_URL}"
klipper_mcu_journal_clean_check "klipper journal has no fresh MCU errors"
klipper_can_mcus_connected_check "klipper CAN MCU connectivity"

if ip -details link show "${TREED_CAN_IFACE}" >/dev/null 2>&1; then
  pass "CAN interface present (${TREED_CAN_IFACE})"
else
  diagnostic_failf "CAN interface present (${TREED_CAN_IFACE})"
fi

if ip link show "${TREED_CAN_IFACE}" 2>/dev/null | grep -q '<[^>]*UP[^>]*>'; then
  pass "CAN interface UP (${TREED_CAN_IFACE})"
else
  diagnostic_failf "CAN interface UP (${TREED_CAN_IFACE})"
fi

if ip -details link show "${TREED_CAN_IFACE}" 2>/dev/null | grep -q "bitrate ${TREED_CAN_BITRATE}"; then
  pass "CAN bitrate ${TREED_CAN_BITRATE}"
else
  diagnostic_failf "CAN bitrate ${TREED_CAN_BITRATE}"
fi

if ip link show "${TREED_CAN_IFACE}" 2>/dev/null | grep -q "qlen ${TREED_CAN_TXQUEUE}"; then
  pass "CAN txqueuelen ${TREED_CAN_TXQUEUE}"
else
  diagnostic_failf "CAN txqueuelen ${TREED_CAN_TXQUEUE}"
fi

if ip -details link show "${TREED_CAN_IFACE}" 2>/dev/null | grep -q "restart-ms ${TREED_CAN_RESTART_MS}"; then
  pass "CAN restart-ms ${TREED_CAN_RESTART_MS}"
else
  diagnostic_failf "CAN restart-ms ${TREED_CAN_RESTART_MS}"
fi

# Блок 8: Проверки выбранного экранного UI (TreeD Shell или KlipperScreen).
KS_SERVICE_PRESENT=0
TS_SERVICE_PRESENT=0
if systemctl cat "${KS_UNIT}" >/dev/null 2>&1; then
  KS_SERVICE_PRESENT=1
fi
if systemctl cat "${TS_UNIT}" >/dev/null 2>&1; then
  TS_SERVICE_PRESENT=1
fi

case "${TREED_UI_MODE}" in
  ts)
    if [ "${TS_SERVICE_PRESENT}" = "1" ]; then
      if systemctl is-active --quiet "${TS_UNIT}"; then
        pass "treed-shell.service active"
      else
        failf "treed-shell.service active"
      fi

      ts_substate="$(systemctl show -p SubState --value "${TS_UNIT}" 2>/dev/null | tr -d '\r\n')"
      if [ "${ts_substate}" = "running" ]; then
        pass "treed-shell.service substate running"
      else
        failf "treed-shell.service substate running (state=${ts_substate:-unknown})"
      fi
    else
      failf "treed-shell.service present"
    fi

    if [ "${KS_SERVICE_PRESENT}" = "1" ] && systemctl is-active --quiet "${KS_UNIT}"; then
      failf "KlipperScreen.service inactive when TREED_UI_MODE=ts"
    else
      pass "KlipperScreen.service inactive when TREED_UI_MODE=ts"
    fi
    ;;
  ks)
    if [ -f "${KS_OVERRIDE_FILE}" ] && grep -q "plymouth quit --retain-splash" "${KS_OVERRIDE_FILE}"; then
      pass "KlipperScreen retains splash"
    else
      failf "KlipperScreen retains splash"
    fi

    if [ "${KS_SERVICE_PRESENT}" = "1" ]; then
      if systemctl is-active --quiet "${KS_UNIT}"; then
        pass "KlipperScreen.service active"
      else
        failf "KlipperScreen.service active"
      fi

      ks_substate="$(systemctl show -p SubState --value "${KS_UNIT}" 2>/dev/null | tr -d '\r\n')"
      if [ "${ks_substate}" = "running" ]; then
        pass "KlipperScreen.service substate running"
      else
        failf "KlipperScreen.service substate running (state=${ks_substate:-unknown})"
      fi
    else
      failf "KlipperScreen.service present"
    fi

    if [ "${TS_SERVICE_PRESENT}" = "1" ] && systemctl is-active --quiet "${TS_UNIT}"; then
      failf "treed-shell.service inactive when TREED_UI_MODE=ks"
    else
      pass "treed-shell.service inactive when TREED_UI_MODE=ks"
    fi
    ;;
  *)
    failf "TREED_UI_MODE valid (current=${TREED_UI_MODE:-unknown})"
    ;;
esac

# Блок 9: Проверки camera/crowsnest/moonraker-webcam (или skip в auto).
CAM_BIN_DIR="${PI_HOME}/treed/cam/bin"

TREED_VERIFY_CAMERA="${TREED_VERIFY_CAMERA:-auto}"
camera_checks_enabled=0
camera_checks_reason=""
if is_true "${TREED_CAMERA_REQUIRED:-0}"; then
  camera_checks_enabled=1
  camera_checks_reason="required by TREED_CAMERA_REQUIRED=1"
else
  case "${TREED_VERIFY_CAMERA}" in
    1|true|TRUE|yes|YES)
      camera_checks_enabled=1
      camera_checks_reason="forced"
      ;;
    0|false|FALSE|no|NO)
      camera_checks_enabled=0
      camera_checks_reason="disabled by TREED_VERIFY_CAMERA"
      ;;
    auto|AUTO|'')
      if systemctl cat crowsnest.service >/dev/null 2>&1; then
        camera_checks_enabled=1
        camera_checks_reason="auto: crowsnest.service present"
      else
        camera_checks_enabled=0
        camera_checks_reason="auto: crowsnest.service missing"
      fi
      ;;
    *)
      if systemctl cat crowsnest.service >/dev/null 2>&1; then
        camera_checks_enabled=1
        camera_checks_reason="auto fallback: crowsnest.service present"
      else
        camera_checks_enabled=0
        camera_checks_reason="auto fallback: crowsnest.service missing"
      fi
      log_warn "VERIFY invalid TREED_VERIFY_CAMERA='${TREED_VERIFY_CAMERA}', using ${camera_checks_reason}"
      ;;
  esac
fi

if [ "${camera_checks_enabled}" = "1" ]; then
  if systemctl cat crowsnest.service >/dev/null 2>&1; then
    pass "crowsnest.service present for camera checks"
  else
    camera_failf "crowsnest.service present for camera checks"
    camera_checks_enabled=0
  fi
fi

if [ "${camera_checks_enabled}" = "1" ]; then
  for f in session_start.sh snapshot.sh session_stop.sh; do
    if [ -x "${CAM_BIN_DIR}/${f}" ]; then
      pass "cam script executable ${CAM_BIN_DIR}/${f}"
    else
      camera_failf "cam script executable ${CAM_BIN_DIR}/${f}"
    fi
  done

  if command -v curl >/dev/null 2>&1; then
    http_snapshot_check "camera direct snapshot :8080" "http://127.0.0.1:8080/?action=snapshot"
    http_snapshot_check "camera proxied snapshot /webcam" "http://127.0.0.1/webcam/?action=snapshot"
    moonraker_webcams_check "moonraker webcams api treed entry" "${WEBCAM_API_URL}"
  else
    camera_failf "curl installed for camera checks"
  fi
else
  log_info "VERIFY camera checks skipped (${camera_checks_reason})"
fi

# Блок 12: Проверки firmware-build артефактов (если этап включен).
TREED_FIRMWARE_BUILD_ENABLED="${TREED_FIRMWARE_BUILD_ENABLED:-1}"
TREED_FIRMWARE_ARTIFACTS_DIR="${TREED_FIRMWARE_ARTIFACTS_DIR:-${PI_HOME}/treed/firmware-artifacts/treed-v2}"
if [ "${TREED_FIRMWARE_BUILD_ENABLED}" = "1" ]; then
  if [ -L "${TREED_FIRMWARE_ARTIFACTS_DIR}/latest" ] || [ -d "${TREED_FIRMWARE_ARTIFACTS_DIR}/latest" ]; then
    pass "firmware artifacts latest present (${TREED_FIRMWARE_ARTIFACTS_DIR}/latest)"
  else
    failf "firmware artifacts latest present (${TREED_FIRMWARE_ARTIFACTS_DIR}/latest)"
  fi

  if [ -f "${TREED_FIRMWARE_ARTIFACTS_DIR}/latest/manifest.tsv" ]; then
    pass "firmware manifest present"
  else
    failf "firmware manifest present"
  fi

  if [ -f "${TREED_FIRMWARE_ARTIFACTS_DIR}/latest/checksums.sha256" ]; then
    pass "firmware checksums present"
  else
    failf "firmware checksums present"
  fi
else
  log_info "VERIFY firmware artifact checks skipped (TREED_FIRMWARE_BUILD_ENABLED=0)"
fi

# Блок 13: Итог verify (fatal/diagnostic счетчики).
if [ "${fail}" -eq 0 ]; then
  if [ "${VERIFY_DIAGNOSTIC_FAILS}" -eq 0 ]; then
    log_info "verify: all ${ok} checks passed"
  else
    log_warn "verify: ${VERIFY_DIAGNOSTIC_FAILS} diagnostic checks failed, ${ok} passed"
  fi
else
  log_warn "verify: ${fail} fatal checks failed, ${VERIFY_DIAGNOSTIC_FAILS} diagnostic checks failed, ${ok} passed"
  exit 1
fi
