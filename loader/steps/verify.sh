#!/bin/bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"

. "${REPO_DIR}/loader/lib/common.sh"
. "${REPO_DIR}/loader/lib/rpi.sh"

log_info "Step verify: running post-configuration checks"

ok=0
fail=0

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

check_required_service_active() {
  local unit="$1"
  local state=""
  local substate=""

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
  if [ "${substate}" = "running" ]; then
    pass "${unit} substate running"
  else
    failf "${unit} substate running (state=${substate:-unknown})"
  fi
}

moonraker_ready_check() {
  local check_name="$1"
  local url="$2"
  local tmp code retries attempt

  if ! command -v curl >/dev/null 2>&1; then
    failf "${check_name} (curl missing)"
    return 0
  fi

  tmp="$(mktemp "/tmp/treed_verify_server_info_XXXXXX.json")"
  retries="${TREED_MOONRAKER_HTTP_RETRIES:-30}"
  code=""

  for attempt in $(seq 1 "${retries}"); do
    code="$(curl -m "${TREED_CAM_HTTP_TIMEOUT:-8}" -sS -o "${tmp}" -w '%{http_code}' "${url}" || true)"
    if [ "${code}" = "200" ] \
      && grep -qE '"klippy_connected"[[:space:]]*:[[:space:]]*true' "${tmp}" \
      && grep -qE '"klippy_state"[[:space:]]*:[[:space:]]*"ready"' "${tmp}"; then
      pass "${check_name}"
      rm -f "${tmp}"
      return 0
    fi
    sleep 1
  done

  failf "${check_name} (http=${code:-n/a})"
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

  patterns="Lost communication with MCU|Timeout with MCU|MCU 'mcu' shutdown|mcu[.]error|Error configuring printer|Unable to open serial port|mcu 'mcu': Unable to connect"
  if grep -Eiq "${patterns}" "${tmp}"; then
    failf "${check_name} (mcu errors found since=${since})"
  else
    pass "${check_name}"
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

# Гарантируем BOOT_DIR / CMDLINE_FILE / CONFIG_FILE даже при ручном запуске
if [ -z "${BOOT_DIR:-}" ]; then
  BOOT_DIR="$(detect_boot_dir)"
fi

if [ -z "${CMDLINE_FILE:-}" ] || [ ! -f "${CMDLINE_FILE}" ]; then
  CMDLINE_FILE="$(detect_cmdline_file "${BOOT_DIR}" 2>/dev/null || true)"
fi

if [ -z "${CONFIG_FILE:-}" ] || [ ! -f "${CONFIG_FILE}" ]; then
  CONFIG_FILE="$(detect_config_file "${BOOT_DIR}")"
fi

KVER="$(uname -r)"
INITRD="${BOOT_DIR}/initrd.img-${KVER}"

if [ -f "${INITRD}" ]; then
  pass "initramfs file ${INITRD}"
else
  failf "initramfs file (${INITRD} missing)"
fi

# Проверка строки initramfs в config.txt
if [ -f "${CONFIG_FILE}" ]; then
  if grep -Fq "initramfs initrd.img-${KVER} followkernel" "${CONFIG_FILE}"; then
    pass "config.txt initramfs initrd.img-${KVER} followkernel"
  else
    failf "config.txt initramfs initrd.img-${KVER} followkernel"
  fi
else
  failf "config.txt (${CONFIG_FILE} missing)"
fi


CMDLINE_CONTENT=""
CMDLINE_PATH="${CMDLINE_FILE:-<empty>}"

if [ -n "${CMDLINE_FILE:-}" ] && [ -f "${CMDLINE_FILE}" ]; then
  CMDLINE_CONTENT="$(tr -d '\n' < "${CMDLINE_FILE}" 2>/dev/null || true)"

  for tok in quiet splash plymouth.ignore-serial-consoles logo.nologo vt.global_cursor_default=0 consoleblank=0 loglevel=3 vt.handoff=7 usbcore.autosuspend=-1; do
    if printf '%s\n' "${CMDLINE_CONTENT}" | grep -qE "(^| )${tok}( |$)"; then
      pass "cmdline token ${tok}"
    else
      failf "cmdline token ${tok}"
    fi
  done

  if printf '%s\n' "${CMDLINE_CONTENT}" | grep -q "plymouth.enable=0"; then
    failf "cmdline has plymouth.enable=0"
  else
    pass "cmdline has no plymouth.enable=0"
  fi

  if [ "$(wc -l < "${CMDLINE_FILE}" 2>/dev/null || echo 2)" -eq 1 ]; then
    pass "cmdline one-line"
  else
    failf "cmdline one-line"
  fi
else
  failf "cmdline file missing (${CMDLINE_PATH})"
fi

PI_USER="${PI_USER:-pi}"
PI_HOME="${PI_HOME:-/home/${PI_USER}}"
TREED_MCU_TRANSPORT_RAW="${TREED_MCU_TRANSPORT:-uart}"
TREED_MCU_UART_DEV="${TREED_MCU_UART_DEV:-/dev/serial0}"
TREED_UART_DISABLE_BT="${TREED_UART_DISABLE_BT:-auto}"
TREED_KLIPPERSCREEN_REQUIRED="${TREED_KLIPPERSCREEN_REQUIRED:-0}"
TREED_KS_THEME_EXPECTED="${TREED_KS_THEME:-treed-oled}"
TREED_KLIPPERSCREEN_HOME_RAW="${TREED_KLIPPERSCREEN_HOME:-}"
if [ -n "${TREED_KLIPPERSCREEN_HOME_RAW}" ]; then
  TREED_KLIPPERSCREEN_HOME="${TREED_KLIPPERSCREEN_HOME_RAW}"
  log_info "VERIFY KlipperScreen home forced via TREED_KLIPPERSCREEN_HOME=${TREED_KLIPPERSCREEN_HOME}"
else
  TREED_KLIPPERSCREEN_HOME="$(detect_klipperscreen_home "${PI_HOME}/KlipperScreen")"
  log_info "VERIFY KlipperScreen home resolved as ${TREED_KLIPPERSCREEN_HOME}"
fi
MCU_CFG_RUNTIME="${PI_HOME}/printer_data/config/profiles/rn12_hbot_v1/mcu_rn12.cfg"
MOONRAKER_SERVER_INFO_URL="http://127.0.0.1:7125/server/info"
KS_CONFIG_FILE="${PI_HOME}/printer_data/config/KlipperScreen.conf"
KS_OVERRIDE_FILE="/etc/systemd/system/KlipperScreen.service.d/override.conf"
KS_THEME_RUNTIME_STYLE="${TREED_KLIPPERSCREEN_HOME}/styles/treed-oled/style.css"
KS_THEME_RUNTIME_IMAGES_DIR="${TREED_KLIPPERSCREEN_HOME}/styles/treed-oled/images"
KS_SERVICE_PRESENT=0

case "${TREED_MCU_TRANSPORT_RAW}" in
  usb|USB) TREED_MCU_TRANSPORT="usb" ;;
  uart|UART) TREED_MCU_TRANSPORT="uart" ;;
  *)
    failf "TREED_MCU_TRANSPORT is valid (value=${TREED_MCU_TRANSPORT_RAW})"
    TREED_MCU_TRANSPORT="uart"
    ;;
esac

check_required_service_active "klipper.service"
check_required_service_active "moonraker.service"
moonraker_ready_check "moonraker api ready/klippy connected" "${MOONRAKER_SERVER_INFO_URL}"
klipper_mcu_journal_clean_check "klipper journal has no fresh MCU errors"

if [ -f "${MCU_CFG_RUNTIME}" ]; then
  pass "mcu config present (${MCU_CFG_RUNTIME})"
  runtime_mcu_serial="$(
    sed -nE 's|^[[:space:]]*serial:[[:space:]]*([^[:space:]#]+).*|\1|p' "${MCU_CFG_RUNTIME}" \
      | head -n 1 || true
  )"
  if [ -n "${runtime_mcu_serial}" ]; then
    pass "mcu serial line present (${runtime_mcu_serial})"
  else
    failf "mcu serial line present (${MCU_CFG_RUNTIME})"
  fi
else
  failf "mcu config present (${MCU_CFG_RUNTIME})"
  runtime_mcu_serial=""
fi

if [ "${TREED_MCU_TRANSPORT}" = "usb" ]; then
  if printf '%s' "${runtime_mcu_serial}" | grep -qE '^/dev/serial/by-id/.+'; then
    pass "mcu transport usb serial path format"
  else
    failf "mcu transport usb serial path format"
  fi

  if [ -n "${runtime_mcu_serial}" ] && [ -e "${runtime_mcu_serial}" ] && [ -r "${runtime_mcu_serial}" ]; then
    pass "mcu usb serial path exists (${runtime_mcu_serial})"
  else
    failf "mcu usb serial path exists (${runtime_mcu_serial:-missing})"
  fi
fi

if [ "${TREED_MCU_TRANSPORT}" = "uart" ]; then
  if [ "${runtime_mcu_serial}" = "${TREED_MCU_UART_DEV}" ]; then
    pass "mcu transport uart serial target (${TREED_MCU_UART_DEV})"
  else
    failf "mcu transport uart serial target (${TREED_MCU_UART_DEV}, current=${runtime_mcu_serial:-missing})"
  fi

  if [ -e "${TREED_MCU_UART_DEV}" ] || [ -L "${TREED_MCU_UART_DEV}" ]; then
    pass "mcu uart device path exists (${TREED_MCU_UART_DEV})"
  else
    failf "mcu uart device path exists (${TREED_MCU_UART_DEV})"
  fi

  for unit in serial-getty@ttyAMA0.service serial-getty@ttyS0.service; do
    if out="$(systemctl is-enabled "${unit}" 2>&1)"; then
      st=0
    else
      st=$?
    fi
    state="$(printf '%s' "${out}" | head -n 1 | tr -d '\r\n')"
    case "${state}" in
      enabled|disabled|static|indirect|generated|masked|masked-runtime|linked|linked-runtime|alias) ;;
      *)
        log_error "verify: systemctl is-enabled ${unit} failed rc=${st}: ${out}"
        exit 1
        ;;
    esac

    if [ "${state}" = "masked" ] || [ "${state}" = "masked-runtime" ]; then
      pass "${unit} masked for uart transport (state=${state})"
    else
      failf "${unit} masked for uart transport (state=${state})"
    fi
  done

  if [ "$(id -u)" -eq 0 ]; then
    if sudo -u "${PI_USER}" test -r "${TREED_MCU_UART_DEV}" \
      && sudo -u "${PI_USER}" test -w "${TREED_MCU_UART_DEV}"; then
      pass "mcu uart device readable/writable by ${PI_USER} (${TREED_MCU_UART_DEV})"
    else
      failf "mcu uart device readable/writable by ${PI_USER} (${TREED_MCU_UART_DEV})"
    fi
  else
    log_info "VERIFY uart rw-check skipped (script not running as root)"
  fi

  UART_RULE_FILE="/etc/udev/rules.d/99-treed-uart-perms.rules"
  if [ -f "${UART_RULE_FILE}" ] \
    && grep -qE '^[[:space:]]*KERNEL=="ttyAMA0",[[:space:]]*MODE="0660",[[:space:]]*GROUP="dialout"[[:space:]]*$' "${UART_RULE_FILE}" \
    && grep -qE '^[[:space:]]*KERNEL=="ttyS0",[[:space:]]*MODE="0660",[[:space:]]*GROUP="dialout"[[:space:]]*$' "${UART_RULE_FILE}"; then
    pass "uart udev permissions rule present (${UART_RULE_FILE})"
  else
    failf "uart udev permissions rule present (${UART_RULE_FILE})"
  fi

  enable_uart_val="$(
    sed -nE 's|^[[:space:]]*enable_uart[[:space:]]*=[[:space:]]*([0-9]+).*|\1|p' "${CONFIG_FILE}" \
      | tail -n 1 || true
  )"
  if [ "${enable_uart_val}" = "1" ]; then
    pass "config.txt enable_uart=1"
  else
    failf "config.txt enable_uart=1"
  fi

  if is_true "${TREED_UART_DISABLE_BT}"; then
    if grep -qE '^[[:space:]]*dtoverlay[[:space:]]*=[[:space:]]*disable-bt([[:space:]]*#.*)?$' "${CONFIG_FILE}"; then
      pass "config.txt dtoverlay=disable-bt for uart transport"
    else
      failf "config.txt dtoverlay=disable-bt for uart transport"
    fi
  elif [ "${TREED_UART_DISABLE_BT}" = "0" ] || [ "${TREED_UART_DISABLE_BT}" = "false" ] || [ "${TREED_UART_DISABLE_BT}" = "FALSE" ] || [ "${TREED_UART_DISABLE_BT}" = "no" ] || [ "${TREED_UART_DISABLE_BT}" = "NO" ]; then
    log_info "VERIFY bluetooth UART check skipped (TREED_UART_DISABLE_BT=${TREED_UART_DISABLE_BT})"
  else
    if grep -qE '^[[:space:]]*dtoverlay[[:space:]]*=[[:space:]]*disable-bt([[:space:]]*#.*)?$' "${CONFIG_FILE}"; then
      pass "config.txt dtoverlay=disable-bt for uart transport (auto)"
    else
      log_info "VERIFY bluetooth UART check auto: dtoverlay=disable-bt not found"
    fi
  fi

  if [ -n "${CMDLINE_CONTENT}" ] \
    && printf '%s\n' "${CMDLINE_CONTENT}" | grep -qE '(^| )console=(serial0|ttyAMA0|ttyS0),[^ ]+'; then
    failf "cmdline has no serial console tokens for uart transport"
  else
    pass "cmdline has no serial console tokens for uart transport"
  fi
fi

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

    ks_substate="$(systemctl show -p SubState --value KlipperScreen.service 2>/dev/null | tr -d '\r\n')"
    if [ "${ks_substate}" = "running" ]; then
      pass "KlipperScreen.service substate running (optional)"
    else
      log_info "VERIFY KlipperScreen optional: substate is ${ks_substate:-unknown}"
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
    if [ -f "${KS_THEME_RUNTIME_STYLE}" ]; then
      pass "KlipperScreen treed-oled style deployed (${KS_THEME_RUNTIME_STYLE})"
    else
      failf "KlipperScreen treed-oled style deployed (${KS_THEME_RUNTIME_STYLE})"
    fi
    if [ -d "${KS_THEME_RUNTIME_IMAGES_DIR}" ] \
      && [ -n "$(find "${KS_THEME_RUNTIME_IMAGES_DIR}" -maxdepth 1 -type f -print -quit 2>/dev/null)" ]; then
      pass "KlipperScreen treed-oled icon pack deployed (${KS_THEME_RUNTIME_IMAGES_DIR})"
    else
      failf "KlipperScreen treed-oled icon pack deployed (${KS_THEME_RUNTIME_IMAGES_DIR})"
    fi
  fi
else
  log_info "VERIFY KlipperScreen theme check skipped (service not installed)"
fi

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

gm="$(grep -E "^gpu_mem=" "${CONFIG_FILE}" 2>/dev/null | tail -n1 | cut -d= -f2)"
case "${gm}" in ''|*[!0-9]*) gm=0;; esac

if [ "${gm:-0}" -ge 96 ]; then
  pass "gpu_mem >= 96"
else
  failf "gpu_mem >= 96"
fi

CAM_BIN_DIR="${PI_HOME}/treed/cam/bin"
CROWSNEST_CFG="${PI_HOME}/printer_data/config/crowsnest.conf"
MOONRAKER_CFG="${PI_HOME}/printer_data/config/moonraker.conf"
MOONRAKER_WEBCAM_FRAGMENT="${PI_HOME}/printer_data/config/moonraker/generated/50-webcam-treed.conf"
WEBCAM_API_URL="http://127.0.0.1:7125/server/webcams/list"

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

    if grep -qE '^\[webcam treed\][[:space:]]*$' "${MOONRAKER_CFG}"; then
      failf "moonraker root config should not contain [webcam treed]"
    else
      pass "moonraker root config has no [webcam treed]"
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

if [ "${fail}" -eq 0 ]; then
  log_info "verify: all ${ok} checks passed"
else
  log_warn "verify: ${fail} checks failed, ${ok} passed"
  exit 1
fi
