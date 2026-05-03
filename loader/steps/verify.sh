#!/bin/bash
set -euo pipefail

# ==========================================
# ШАГ LOADER: VERIFY
# ==========================================
# Назначение:
# - Выполняет финальные post-configuration проверки provisioning-контура.
# - Сохраняет паритет dev-отчета (boot/time/camera/ui/services) в V2-модели.
# - Валидирует V2 runtime: main MCU USB, CAN EBB(required), Eddy(optional), ADXL.
# Контур:
# - required (непрошедшие проверки завершают loader с ошибкой).

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"

# Блок 1: Библиотеки и базовая инициализация шага.
. "${REPO_DIR}/loader/lib/common.sh"
. "${REPO_DIR}/loader/lib/rpi.sh"

log_info "Step verify: running V2 post-configuration checks (parity mode)"

ok=0
fail=0
MOONRAKER_READY_OK=0

# Блок 2: Вспомогательные функции подсчета/парсинга/проверок.
pass() {
  log_info "VERIFY $1: ok"
  ok=$((ok+1))
}

failf() {
  log_warn "VERIFY $1: FAIL"
  fail=$((fail+1))
}

is_true() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

extract_cfg_value() {
  local key_regex="$1"
  local file="$2"
  sed -nE "s|^[[:space:]]*${key_regex}[[:space:]]*:[[:space:]]*([^[:space:]#]+).*|\\1|p" "${file}" | head -n 1 || true
}

read_klipperscreen_main_theme() {
  local cfg="$1"
  awk '
    BEGIN { in_main = 0 }
    /^[[:space:]]*\[main\][[:space:]]*$/ { in_main = 1; next }
    in_main && /^[[:space:]]*\[[^]]+\][[:space:]]*$/ { in_main = 0 }
    in_main && /^[[:space:]]*theme[[:space:]]*[:=]/ {
      line = $0
      sub(/^[[:space:]]*theme[[:space:]]*[:=][[:space:]]*/, "", line)
      sub(/[[:space:]]*(#|;).*$/, "", line)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", line)
      print line
      exit
    }
  ' "${cfg}"
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

extract_required_theme_icons() {
  local style_file="$1"
  if [ ! -f "${style_file}" ]; then
    return 0
  fi

  grep -Eo "images/[^\"' )?#;]+" "${style_file}" 2>/dev/null \
    | sed 's|^images/||' \
    | sort -u
}

missing_required_icons() {
  local images_dir="$1"
  local required_icons="$2"
  local icon=""

  while IFS= read -r icon; do
    [ -z "${icon}" ] && continue
    if [ ! -f "${images_dir}/${icon}" ]; then
      printf '%s\n' "${icon}"
    fi
  done <<< "${required_icons}"
}

check_required_service_active() {
  local unit="$1"
  local substate_allowed="${2:-running}"
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
    failf "${unit} active (state=${state:-unknown})"
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
    failf "${unit} substate running (state=${substate:-unknown})"
  else
    failf "${unit} substate one-of(${substate_allowed// /|}) (state=${substate:-unknown})"
  fi
}

moonraker_ready_check() {
  local check_name="$1"
  local url="$2"
  local tmp code retries attempt

  if ! command -v curl >/dev/null 2>&1; then
    failf "${check_name} (curl missing)"
    MOONRAKER_READY_OK=0
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
    if [ "${code}" = "200" ] \
      && grep -qE '"klippy_connected"[[:space:]]*:[[:space:]]*true' "${tmp}" \
      && grep -qE '"klippy_state"[[:space:]]*:[[:space:]]*"ready"' "${tmp}"; then
      pass "${check_name}"
      MOONRAKER_READY_OK=1
      rm -f "${tmp}"
      return 0
    fi
    sleep 1
  done

  failf "${check_name} (http=${code:-n/a}, retries=${retries})"
  MOONRAKER_READY_OK=0
  rm -f "${tmp}"
}

klipper_mcu_journal_clean_check() {
  local check_name="$1"
  local unit="klipper.service"
  local since=""
  local tmp=""
  local patterns=""

  if ! command -v journalctl >/dev/null 2>&1; then
    failf "${check_name} (journalctl missing)"
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
    failf "${check_name} (cannot read journal since=${since})"
    rm -f "${tmp}"
    return 0
  fi

  patterns="Lost communication with MCU|Timeout with MCU|MCU 'mcu' shutdown|MCU 'EBBCan' shutdown|mcu[.]error|Error configuring printer|Unable to open serial port|mcu 'mcu': Unable to connect|mcu 'EBBCan': Unable to connect"
  if grep -Eiq "${patterns}" "${tmp}"; then
    failf "${check_name} (mcu errors found since=${since})"
  else
    pass "${check_name}"
  fi

  rm -f "${tmp}"
}

klipper_ebb_connected_check() {
  local check_name="$1"
  local unit="klipper.service"
  local since=""
  local tmp=""
  local patterns=""
  local error_patterns=""

  if ! command -v journalctl >/dev/null 2>&1; then
    failf "${check_name} (journalctl missing)"
    return 0
  fi

  since="$(systemctl show -p ActiveEnterTimestamp --value "${unit}" 2>/dev/null | tr -d '\r\n')"
  case "${since}" in
    ""|"n/a") since="-20 min" ;;
  esac

  tmp="$(mktemp "/tmp/treed_verify_klipper_ebb_XXXXXX.log")"
  if journalctl -u "${unit}" --since "${since}" --no-pager > "${tmp}" 2>/dev/null; then
    :
  else
    failf "${check_name} (cannot read journal since=${since})"
    rm -f "${tmp}"
    return 0
  fi

  patterns="Loaded MCU 'EBBCan'|Configured MCU 'EBBCan'"
  error_patterns="MCU 'EBBCan' shutdown|mcu 'EBBCan': Unable to connect|Lost communication with MCU|Timeout with MCU"
  if grep -Eiq "${patterns}" "${tmp}"; then
    pass "${check_name}"
  else
    if journalctl -u "${unit}" -n 400 --no-pager > "${tmp}" 2>/dev/null; then
      if grep -Eiq "${patterns}" "${tmp}"; then
        pass "${check_name} (markers found in recent journal)"
      elif grep -Eiq "${error_patterns}" "${tmp}"; then
        failf "${check_name} (EBBCan errors found in recent journal)"
      else
        pass "${check_name} (no startup markers, but no EBBCan errors in recent journal)"
      fi
    else
      failf "${check_name} (no EBBCan startup markers since=${since})"
    fi
  fi

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
  failf "${check_name} (http=${code:-n/a})"
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

  failf "${check_name} (http=${code:-n/a})"
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
TREED_CAN_IFACE="${TREED_CAN_IFACE:-${CAN_ENV_IFACE:-can0}}"
TREED_CAN_BITRATE="${TREED_CAN_BITRATE:-${CAN_ENV_BITRATE:-1000000}}"
TREED_CAN_TXQUEUE="${TREED_CAN_TXQUEUE:-${CAN_ENV_TXQUEUE:-1024}}"
TREED_CAN_RESTART_MS="${TREED_CAN_RESTART_MS:-${CAN_ENV_RESTART_MS:-100}}"
TREED_EDDY_ENABLED="${TREED_EDDY_ENABLED:-0}"
TREED_Z_ENDSTOP_PIN="${TREED_Z_ENDSTOP_PIN:-PG10}"
TREED_Z_POSITION_ENDSTOP="${TREED_Z_POSITION_ENDSTOP:-0.5}"

PROFILE_DIR="${PI_HOME}/printer_data/config/profiles/treed_v2_corexy_v1"
PRINTER_CFG_RUNTIME="${PI_HOME}/printer_data/config/printer.cfg"
MAIN_CFG_RUNTIME="${PROFILE_DIR}/mcu_main_octopus_usb.cfg"
EBB_CFG_RUNTIME="${PROFILE_DIR}/ebb42_can.cfg"
EDDY_CFG_RUNTIME="${PROFILE_DIR}/probe_eddy_duo_optional.cfg"
STEPPERS_CFG_RUNTIME="${PROFILE_DIR}/steppers.cfg"
INPUT_SHAPER_CFG="${PROFILE_DIR}/input_shaper.cfg"

MOONRAKER_SERVER_INFO_URL="http://127.0.0.1:7125/server/info"
WEBCAM_API_URL="http://127.0.0.1:7125/server/webcams/list"
CAN_UNIT="treed-can-setup.service"

KS_CONFIG_FILE="${PI_HOME}/printer_data/config/KlipperScreen.conf"
KS_OVERRIDE_FILE="/etc/systemd/system/KlipperScreen.service.d/override.conf"
TREED_KS_THEME_EXPECTED="${TREED_KS_THEME:-treed-oled}"
TREED_KLIPPERSCREEN_REQUIRED="${TREED_KLIPPERSCREEN_REQUIRED:-0}"
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
KS_THEME_RUNTIME_STYLE="${TREED_KLIPPERSCREEN_HOME}/styles/treed-oled/style.css"
KS_THEME_RUNTIME_IMAGES_DIR="${TREED_KLIPPERSCREEN_HOME}/styles/treed-oled/images"

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
check_required_service_active "${CAN_UNIT}" "running exited"
moonraker_ready_check "moonraker api ready/klippy connected" "${MOONRAKER_SERVER_INFO_URL}"
klipper_mcu_journal_clean_check "klipper journal has no fresh MCU errors"
klipper_ebb_connected_check "klipper startup connected EBBCan"

if ip -details link show "${TREED_CAN_IFACE}" >/dev/null 2>&1; then
  pass "CAN interface present (${TREED_CAN_IFACE})"
else
  failf "CAN interface present (${TREED_CAN_IFACE})"
fi

if ip link show "${TREED_CAN_IFACE}" 2>/dev/null | grep -q '<[^>]*UP[^>]*>'; then
  pass "CAN interface UP (${TREED_CAN_IFACE})"
else
  failf "CAN interface UP (${TREED_CAN_IFACE})"
fi

if ip -details link show "${TREED_CAN_IFACE}" 2>/dev/null | grep -q "bitrate ${TREED_CAN_BITRATE}"; then
  pass "CAN bitrate ${TREED_CAN_BITRATE}"
else
  failf "CAN bitrate ${TREED_CAN_BITRATE}"
fi

if ip link show "${TREED_CAN_IFACE}" 2>/dev/null | grep -q "qlen ${TREED_CAN_TXQUEUE}"; then
  pass "CAN txqueuelen ${TREED_CAN_TXQUEUE}"
else
  failf "CAN txqueuelen ${TREED_CAN_TXQUEUE}"
fi

if ip -details link show "${TREED_CAN_IFACE}" 2>/dev/null | grep -q "restart-ms ${TREED_CAN_RESTART_MS}"; then
  pass "CAN restart-ms ${TREED_CAN_RESTART_MS}"
else
  failf "CAN restart-ms ${TREED_CAN_RESTART_MS}"
fi

# Блок 8: Проверки runtime-профиля V2 и MCU binding.
for required_file in "${PRINTER_CFG_RUNTIME}" "${MAIN_CFG_RUNTIME}" "${EBB_CFG_RUNTIME}" "${EDDY_CFG_RUNTIME}" "${STEPPERS_CFG_RUNTIME}" "${INPUT_SHAPER_CFG}"; do
  if [ -f "${required_file}" ]; then
    pass "runtime file present (${required_file})"
  else
    failf "runtime file present (${required_file})"
  fi
done

if [ -f "${PRINTER_CFG_RUNTIME}" ] \
  && grep -qF "[include profiles/treed_v2_corexy_v1/mcu_main_octopus_usb.cfg]" "${PRINTER_CFG_RUNTIME}"; then
  pass "printer.cfg includes V2 main MCU config"
else
  failf "printer.cfg includes V2 main MCU config"
fi

if [ -f "${PRINTER_CFG_RUNTIME}" ] \
  && grep -qF "[include profiles/treed_v2_corexy_v1/ebb42_can.cfg]" "${PRINTER_CFG_RUNTIME}"; then
  pass "printer.cfg includes V2 EBB config"
else
  failf "printer.cfg includes V2 EBB config"
fi

runtime_main_serial=""
if [ -f "${MAIN_CFG_RUNTIME}" ]; then
  runtime_main_serial="$(extract_cfg_value "serial" "${MAIN_CFG_RUNTIME}")"
fi
if [ -n "${runtime_main_serial}" ] && printf '%s' "${runtime_main_serial}" | grep -qE '^/dev/serial/by-id/.+'; then
  pass "main MCU serial format (/dev/serial/by-id/*)"
else
  failf "main MCU serial format (/dev/serial/by-id/*)"
fi

if [ -n "${runtime_main_serial}" ] && [ -e "${runtime_main_serial}" ] && [ -r "${runtime_main_serial}" ]; then
  pass "main MCU serial path exists/readable (${runtime_main_serial})"
else
  failf "main MCU serial path exists/readable (${runtime_main_serial:-missing})"
fi

runtime_ebb_uuid=""
if [ -f "${EBB_CFG_RUNTIME}" ]; then
  runtime_ebb_uuid="$(extract_cfg_value "canbus_uuid" "${EBB_CFG_RUNTIME}")"
fi
if [ -n "${runtime_ebb_uuid}" ] && ! printf '%s' "${runtime_ebb_uuid}" | grep -qE '[^0-9A-Fa-f]'; then
  pass "EBB canbus_uuid is hex"
else
  failf "EBB canbus_uuid is hex"
fi

if [ -f "${EBB_CFG_RUNTIME}" ] \
  && grep -qE "^[[:space:]]*canbus_interface:[[:space:]]*${TREED_CAN_IFACE}[[:space:]]*$" "${EBB_CFG_RUNTIME}"; then
  pass "EBB canbus_interface is ${TREED_CAN_IFACE}"
else
  failf "EBB canbus_interface is ${TREED_CAN_IFACE}"
fi

if [ -f "${EBB_CFG_RUNTIME}" ] \
  && grep -qE '^[[:space:]]*\[adxl345\][[:space:]]*$' "${EBB_CFG_RUNTIME}" \
  && grep -qE '^[[:space:]]*cs_pin[[:space:]]*:[[:space:]]*EBBCan:PB12[[:space:]]*$' "${EBB_CFG_RUNTIME}" \
  && grep -qE '^[[:space:]]*spi_bus[[:space:]]*:[[:space:]]*spi2_PB2_PB11_PB10[[:space:]]*$' "${EBB_CFG_RUNTIME}" \
  && grep -qE '^[[:space:]]*\[resonance_tester\][[:space:]]*$' "${EBB_CFG_RUNTIME}" \
  && grep -qE '^[[:space:]]*accel_chip[[:space:]]*:[[:space:]]*adxl345[[:space:]]*$' "${EBB_CFG_RUNTIME}"; then
  pass "EBB config contains onboard ADXL/resonance_tester"
else
  failf "EBB config contains onboard ADXL/resonance_tester"
fi

if [ -f "${PRINTER_CFG_RUNTIME}" ] \
  && grep -qE '^[[:space:]]*\[include[[:space:]]+profiles/treed_v2_corexy_v1/input_shaper\.cfg\][[:space:]]*$' "${PRINTER_CFG_RUNTIME}"; then
  pass "Input Shaper include enabled in printer.cfg"
else
  failf "Input Shaper include enabled in printer.cfg"
fi

if [ -f "${INPUT_SHAPER_CFG}" ] && grep -qE '^[[:space:]]*\[input_shaper\][[:space:]]*$' "${INPUT_SHAPER_CFG}"; then
  pass "Input Shaper config present (${INPUT_SHAPER_CFG})"
else
  failf "Input Shaper config present (${INPUT_SHAPER_CFG})"
fi

# Проверки sensorless X/Y: tmc2209 + virtual endstop + retract=0.
if [ -f "${STEPPERS_CFG_RUNTIME}" ] \
  && grep -qE '^[[:space:]]*\[tmc2209[[:space:]]+stepper_x\][[:space:]]*$' "${STEPPERS_CFG_RUNTIME}" \
  && grep -qE '^[[:space:]]*\[tmc2209[[:space:]]+stepper_y\][[:space:]]*$' "${STEPPERS_CFG_RUNTIME}"; then
  pass "sensorless X/Y: tmc2209 sections present"
else
  failf "sensorless X/Y: tmc2209 sections present"
fi

if [ -f "${STEPPERS_CFG_RUNTIME}" ] \
  && awk '
    /^\[stepper_x\][[:space:]]*$/ { in_section = 1; next }
    in_section && /^\[[^]]+\][[:space:]]*$/ { in_section = 0 }
    in_section && /^[[:space:]]*endstop_pin[[:space:]]*:/ {
      value = $0
      sub(/^[[:space:]]*endstop_pin[[:space:]]*:[[:space:]]*/, "", value)
      sub(/[[:space:]]*(#.*)?$/, "", value)
      found = (value == "tmc2209_stepper_x:virtual_endstop")
    }
    END { exit found ? 0 : 1 }
  ' "${STEPPERS_CFG_RUNTIME}"; then
  pass "sensorless X: virtual endstop"
else
  failf "sensorless X: virtual endstop"
fi

if [ -f "${STEPPERS_CFG_RUNTIME}" ] \
  && awk '
    /^\[stepper_y\][[:space:]]*$/ { in_section = 1; next }
    in_section && /^\[[^]]+\][[:space:]]*$/ { in_section = 0 }
    in_section && /^[[:space:]]*endstop_pin[[:space:]]*:/ {
      value = $0
      sub(/^[[:space:]]*endstop_pin[[:space:]]*:[[:space:]]*/, "", value)
      sub(/[[:space:]]*(#.*)?$/, "", value)
      found = (value == "tmc2209_stepper_y:virtual_endstop")
    }
    END { exit found ? 0 : 1 }
  ' "${STEPPERS_CFG_RUNTIME}"; then
  pass "sensorless Y: virtual endstop"
else
  failf "sensorless Y: virtual endstop"
fi

if [ -f "${STEPPERS_CFG_RUNTIME}" ] \
  && awk '
    /^\[stepper_x\][[:space:]]*$/ { in_x = 1; next }
    in_x && /^\[[^]]+\][[:space:]]*$/ { in_x = 0 }
    in_x && /^[[:space:]]*homing_retract_dist[[:space:]]*:/ {
      value = $0
      sub(/^[[:space:]]*homing_retract_dist[[:space:]]*:[[:space:]]*/, "", value)
      sub(/[[:space:]]*(#.*)?$/, "", value)
      found = 1
      is_zero = ((value + 0) == 0)
    }
    END { exit (found && is_zero) ? 0 : 1 }
  ' "${STEPPERS_CFG_RUNTIME}"; then
  pass "sensorless X: homing_retract_dist=0"
else
  failf "sensorless X: homing_retract_dist=0"
fi

if [ -f "${STEPPERS_CFG_RUNTIME}" ] \
  && awk '
    /^\[stepper_y\][[:space:]]*$/ { in_y = 1; next }
    in_y && /^\[[^]]+\][[:space:]]*$/ { in_y = 0 }
    in_y && /^[[:space:]]*homing_retract_dist[[:space:]]*:/ {
      value = $0
      sub(/^[[:space:]]*homing_retract_dist[[:space:]]*:[[:space:]]*/, "", value)
      sub(/[[:space:]]*(#.*)?$/, "", value)
      found = 1
      is_zero = ((value + 0) == 0)
    }
    END { exit (found && is_zero) ? 0 : 1 }
  ' "${STEPPERS_CFG_RUNTIME}"; then
  pass "sensorless Y: homing_retract_dist=0"
else
  failf "sensorless Y: homing_retract_dist=0"
fi

if [ "${MOONRAKER_READY_OK}" = "1" ]; then
  moonraker_gcode_ok_check "ADXL ACCELEROMETER_QUERY via Moonraker" "ACCELEROMETER_QUERY CHIP=adxl345"
else
  log_info "VERIFY ADXL ACCELEROMETER_QUERY via Moonraker skipped (moonraker/klippy not ready)"
fi

# Блок 9: Optional Eddy-контур.
case "${TREED_EDDY_ENABLED}" in
  0|1) ;;
  *)
    failf "TREED_EDDY_ENABLED is valid (0|1, current=${TREED_EDDY_ENABLED})"
    TREED_EDDY_ENABLED="0"
    ;;
esac

EDDY_INCLUDE_LINE="[include profiles/treed_v2_corexy_v1/probe_eddy_duo_optional.cfg]"
if [ "${TREED_EDDY_ENABLED}" = "1" ]; then
  if [ -f "${PRINTER_CFG_RUNTIME}" ] && grep -qF "${EDDY_INCLUDE_LINE}" "${PRINTER_CFG_RUNTIME}"; then
    pass "Eddy include enabled in printer.cfg"
  else
    failf "Eddy include enabled in printer.cfg"
  fi

  runtime_eddy_uuid=""
  if [ -f "${EDDY_CFG_RUNTIME}" ]; then
    runtime_eddy_uuid="$(extract_cfg_value "canbus_uuid" "${EDDY_CFG_RUNTIME}")"
  fi
  if [ -n "${runtime_eddy_uuid}" ] && ! printf '%s' "${runtime_eddy_uuid}" | grep -qE '[^0-9A-Fa-f]'; then
    pass "Eddy canbus_uuid is hex"
  else
    failf "Eddy canbus_uuid is hex"
  fi

  if [ -f "${EDDY_CFG_RUNTIME}" ] \
    && grep -qE "^[[:space:]]*canbus_interface:[[:space:]]*${TREED_CAN_IFACE}[[:space:]]*$" "${EDDY_CFG_RUNTIME}"; then
    pass "Eddy canbus_interface is ${TREED_CAN_IFACE}"
  else
    failf "Eddy canbus_interface is ${TREED_CAN_IFACE}"
  fi

  if [ -f "${STEPPERS_CFG_RUNTIME}" ] \
    && awk '
      /^\[stepper_z\][[:space:]]*$/ { in_z = 1; next }
      in_z && /^\[[^]]+\][[:space:]]*$/ { in_z = 0 }
      in_z && /^[[:space:]]*endstop_pin:[[:space:]]*probe:z_virtual_endstop[[:space:]]*$/ { found = 1 }
      END { exit found ? 0 : 1 }
    ' "${STEPPERS_CFG_RUNTIME}"; then
    pass "stepper_z uses probe:z_virtual_endstop"
  else
    failf "stepper_z uses probe:z_virtual_endstop"
  fi
else
  if [ -f "${PRINTER_CFG_RUNTIME}" ] \
    && grep -qE '^[[:space:]]*#[[:space:]]*\[include[[:space:]]+profiles/treed_v2_corexy_v1/probe_eddy_duo_optional\.cfg\][[:space:]]*$' "${PRINTER_CFG_RUNTIME}"; then
    pass "Eddy include disabled in printer.cfg"
  else
    failf "Eddy include disabled in printer.cfg"
  fi

  if [ -f "${STEPPERS_CFG_RUNTIME}" ] \
    && awk -v expected="${TREED_Z_ENDSTOP_PIN}" '
      /^\[stepper_z\][[:space:]]*$/ { in_z = 1; next }
      in_z && /^\[[^]]+\][[:space:]]*$/ { in_z = 0 }
      in_z && /^[[:space:]]*endstop_pin[[:space:]]*:/ {
        value = $0
        sub(/^[[:space:]]*endstop_pin[[:space:]]*:[[:space:]]*/, "", value)
        sub(/[[:space:]]*(#.*)?$/, "", value)
        found = (value == expected)
      }
      END { exit found ? 0 : 1 }
    ' "${STEPPERS_CFG_RUNTIME}"; then
    pass "stepper_z uses physical Z endstop ${TREED_Z_ENDSTOP_PIN}"
  else
    failf "stepper_z uses physical Z endstop ${TREED_Z_ENDSTOP_PIN}"
  fi

  if [ -f "${STEPPERS_CFG_RUNTIME}" ] \
    && awk -v expected="${TREED_Z_POSITION_ENDSTOP}" '
      /^\[stepper_z\][[:space:]]*$/ { in_z = 1; next }
      in_z && /^\[[^]]+\][[:space:]]*$/ { in_z = 0 }
      in_z && /^[[:space:]]*position_endstop[[:space:]]*:/ {
        value = $0
        sub(/^[[:space:]]*position_endstop[[:space:]]*:[[:space:]]*/, "", value)
        sub(/[[:space:]]*(#.*)?$/, "", value)
        found = (value == expected)
      }
      END { exit found ? 0 : 1 }
    ' "${STEPPERS_CFG_RUNTIME}"; then
    pass "stepper_z position_endstop ${TREED_Z_POSITION_ENDSTOP}"
  else
    failf "stepper_z position_endstop ${TREED_Z_POSITION_ENDSTOP}"
  fi
fi

# Блок 10: Проверки состояния KlipperScreen (required/optional режимы).
KS_SERVICE_PRESENT=0
if systemctl cat KlipperScreen.service >/dev/null 2>&1; then
  KS_SERVICE_PRESENT=1
fi

if is_true "${TREED_KLIPPERSCREEN_REQUIRED}"; then
  if [ -f "${KS_OVERRIDE_FILE}" ] && grep -q "plymouth quit --retain-splash" "${KS_OVERRIDE_FILE}"; then
    pass "KlipperScreen retains splash"
  else
    failf "KlipperScreen retains splash"
  fi

  if [ "${KS_SERVICE_PRESENT}" = "1" ]; then
    if systemctl is-active --quiet KlipperScreen.service; then
      pass "KlipperScreen.service active"
    else
      failf "KlipperScreen.service active"
    fi

    ks_substate="$(systemctl show -p SubState --value KlipperScreen.service 2>/dev/null | tr -d '\r\n')"
    if [ "${ks_substate}" = "running" ]; then
      pass "KlipperScreen.service substate running"
    else
      failf "KlipperScreen.service substate running (state=${ks_substate:-unknown})"
    fi
  else
    failf "KlipperScreen.service present"
  fi
else
  if [ -f "${KS_OVERRIDE_FILE}" ] && grep -q "plymouth quit --retain-splash" "${KS_OVERRIDE_FILE}"; then
    pass "KlipperScreen retains splash (optional)"
  else
    log_info "VERIFY KlipperScreen optional: override missing or not configured"
  fi

  if [ "${KS_SERVICE_PRESENT}" = "1" ]; then
    if systemctl is-active --quiet KlipperScreen.service; then
      pass "KlipperScreen.service active (optional)"
    else
      ks_state="$(systemctl is-active KlipperScreen.service 2>/dev/null || true)"
      log_info "VERIFY KlipperScreen optional: service not active (state=${ks_state:-unknown})"
    fi
  else
    log_info "VERIFY KlipperScreen optional: service not installed"
  fi
fi

if [ "${TREED_KS_THEME_EXPECTED}" = "keep" ]; then
  log_info "VERIFY KlipperScreen theme check skipped (TREED_KS_THEME=keep)"
elif [ "${KS_SERVICE_PRESENT}" = "1" ]; then
  if [ -f "${KS_CONFIG_FILE}" ]; then
    ks_theme_actual="$(read_klipperscreen_main_theme "${KS_CONFIG_FILE}" | tr -d '\r\n' || true)"
    if [ "${ks_theme_actual}" = "${TREED_KS_THEME_EXPECTED}" ]; then
      pass "KlipperScreen configured theme (${TREED_KS_THEME_EXPECTED})"
    else
      failf "KlipperScreen configured theme (${TREED_KS_THEME_EXPECTED}, current=${ks_theme_actual:-missing})"
    fi
  else
    failf "KlipperScreen config present (${KS_CONFIG_FILE})"
  fi

  if [ "${TREED_KS_THEME_EXPECTED}" = "treed-oled" ]; then
    required_ks_icons=""
    missing_ks_icons=""
    if [ -f "${KS_THEME_RUNTIME_STYLE}" ]; then
      pass "KlipperScreen treed-oled style deployed (${KS_THEME_RUNTIME_STYLE})"
      required_ks_icons="$(extract_required_theme_icons "${KS_THEME_RUNTIME_STYLE}" || true)"
      if [ -n "${required_ks_icons}" ]; then
        pass "KlipperScreen treed-oled style icon refs parsed"
      fi
    else
      failf "KlipperScreen treed-oled style deployed (${KS_THEME_RUNTIME_STYLE})"
    fi

    if [ -d "${KS_THEME_RUNTIME_IMAGES_DIR}" ] \
      && [ -n "$(find "${KS_THEME_RUNTIME_IMAGES_DIR}" -maxdepth 1 -type f -print -quit 2>/dev/null)" ]; then
      pass "KlipperScreen treed-oled icon pack deployed (${KS_THEME_RUNTIME_IMAGES_DIR})"
    else
      failf "KlipperScreen treed-oled icon pack deployed (${KS_THEME_RUNTIME_IMAGES_DIR})"
    fi

    if [ -n "${required_ks_icons}" ]; then
      missing_ks_icons="$(missing_required_icons "${KS_THEME_RUNTIME_IMAGES_DIR}" "${required_ks_icons}" || true)"
      if [ -z "${missing_ks_icons}" ]; then
        pass "KlipperScreen treed-oled required icons present"
      else
        failf "KlipperScreen treed-oled required icons present (missing=$(printf '%s' "${missing_ks_icons}" | tr '\n' ' '))"
      fi
    fi
  fi
else
  log_info "VERIFY KlipperScreen theme check skipped (service not installed)"
fi

# Блок 11: Проверки camera/crowsnest/moonraker-webcam (или skip в auto).
CAM_BIN_DIR="${PI_HOME}/treed/cam/bin"
CROWSNEST_CFG="${PI_HOME}/printer_data/config/crowsnest.conf"
MOONRAKER_CFG="${PI_HOME}/printer_data/config/moonraker.conf"
MOONRAKER_WEBCAM_FRAGMENT="${PI_HOME}/printer_data/config/moonraker/generated/50-webcam-treed.conf"

TREED_VERIFY_CAMERA="${TREED_VERIFY_CAMERA:-auto}"
camera_checks_enabled=0
camera_checks_reason=""
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
    if [ -f "${MOONRAKER_WEBCAM_FRAGMENT}" ]; then
      camera_checks_enabled=1
      camera_checks_reason="auto: webcam fragment present"
    else
      camera_checks_enabled=0
      camera_checks_reason="auto: webcam fragment missing"
    fi
    ;;
  *)
    if [ -f "${MOONRAKER_WEBCAM_FRAGMENT}" ]; then
      camera_checks_enabled=1
      camera_checks_reason="auto fallback: webcam fragment present"
    else
      camera_checks_enabled=0
      camera_checks_reason="auto fallback: webcam fragment missing"
    fi
    log_warn "VERIFY invalid TREED_VERIFY_CAMERA='${TREED_VERIFY_CAMERA}', using ${camera_checks_reason}"
    ;;
esac

if [ "${camera_checks_enabled}" = "1" ]; then
  byid_index0_available=0
  if find /dev/v4l/by-id -maxdepth 1 -type l -name '*-video-index0' -print -quit 2>/dev/null | grep -q .; then
    byid_index0_available=1
  fi

  for f in session_start.sh snapshot.sh session_stop.sh; do
    if [ -x "${CAM_BIN_DIR}/${f}" ]; then
      pass "cam script executable ${CAM_BIN_DIR}/${f}"
    else
      failf "cam script executable ${CAM_BIN_DIR}/${f}"
    fi
  done

  if [ -f "${MOONRAKER_WEBCAM_FRAGMENT}" ] \
    && grep -qE '^\[webcam treed\]\s*$' "${MOONRAKER_WEBCAM_FRAGMENT}" \
    && grep -qE '^[[:space:]]*service[[:space:]]*[:=][[:space:]]*mjpegstreamer[[:space:]]*$' "${MOONRAKER_WEBCAM_FRAGMENT}"; then
    pass "moonraker webcam treed service=mjpegstreamer (${MOONRAKER_WEBCAM_FRAGMENT})"
  else
    failf "moonraker webcam treed service=mjpegstreamer (${MOONRAKER_WEBCAM_FRAGMENT})"
  fi

  if [ -f "${MOONRAKER_CFG}" ]; then
    if grep -qE '^\[include[[:space:]]+moonraker/generated/\*\.conf\][[:space:]]*$' "${MOONRAKER_CFG}"; then
      pass "moonraker include generated/*.conf"
    else
      failf "moonraker include generated/*.conf"
    fi
  else
    failf "moonraker config present (${MOONRAKER_CFG})"
  fi

  if [ -f "${CROWSNEST_CFG}" ]; then
    cam_device_cfg="$(
      awk '
        /^[[:space:]]*device[[:space:]]*:/ {
          v = substr($0, index($0, ":") + 1)
          gsub(/^[[:space:]]+|[[:space:]]+$/, "", v)
          print v
          exit
        }
      ' "${CROWSNEST_CFG}"
    )"

    if [ -n "${cam_device_cfg}" ]; then
      pass "crowsnest camera device configured (${cam_device_cfg})"
      if [ -e "${cam_device_cfg}" ] || [ -L "${cam_device_cfg}" ]; then
        pass "crowsnest camera device exists (${cam_device_cfg})"
      else
        failf "crowsnest camera device exists (${cam_device_cfg})"
      fi
    else
      failf "crowsnest camera device configured"
    fi

    if [ "${byid_index0_available}" = "1" ]; then
      if printf '%s' "${cam_device_cfg:-}" | grep -qE '^/dev/v4l/by-id/.+-video-index0$'; then
        pass "crowsnest prefers /dev/v4l/by-id/*-video-index0"
      else
        failf "crowsnest prefers /dev/v4l/by-id/*-video-index0"
      fi
    else
      pass "no /dev/v4l/by-id/*-video-index0 on host (fallback allowed)"
    fi
  else
    failf "crowsnest config present (${CROWSNEST_CFG})"
  fi

  if command -v curl >/dev/null 2>&1; then
    http_snapshot_check "camera direct snapshot :8080" "http://127.0.0.1:8080/?action=snapshot"
    http_snapshot_check "camera proxied snapshot /webcam" "http://127.0.0.1/webcam/?action=snapshot"
    moonraker_webcams_check "moonraker webcams api treed entry" "${WEBCAM_API_URL}"
  else
    failf "curl installed for camera checks"
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

# Блок 13: Итог verify (pass/fail счетчики).
if [ "${fail}" -eq 0 ]; then
  log_info "verify: all ${ok} checks passed"
else
  log_warn "verify: ${fail} checks failed, ${ok} passed"
  exit 1
fi
