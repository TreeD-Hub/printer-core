#!/bin/bash
set -euo pipefail

. "${REPO_DIR}/loader/lib/common.sh"

log_info "Step plymouth-cmdline: updating kernel cmdline for plymouth"

MCU_TRANSPORT_RAW="${TREED_MCU_TRANSPORT:-usb}"
case "${MCU_TRANSPORT_RAW}" in
  usb|USB) MCU_TRANSPORT="usb" ;;
  uart|UART) MCU_TRANSPORT="uart" ;;
  *)
    log_error "plymouth-cmdline: unsupported TREED_MCU_TRANSPORT='${MCU_TRANSPORT_RAW}' (expected: usb|uart)"
    exit 1
    ;;
esac

if [ -z "${CMDLINE_FILE:-}" ]; then
  if [ -f /boot/firmware/cmdline.txt ]; then
    CMDLINE_FILE=/boot/firmware/cmdline.txt
  elif [ -f /boot/cmdline.txt ]; then
    CMDLINE_FILE=/boot/cmdline.txt
  fi
fi

if [ -z "${CMDLINE_FILE:-}" ] || [ ! -f "${CMDLINE_FILE}" ]; then
  log_error "plymouth-cmdline: CMDLINE_FILE missing or invalid: ${CMDLINE_FILE:-<empty>}"
  exit 1
fi

backup_file_once "${CMDLINE_FILE}"

tmp="$(mktemp)"
tr -d '\r\n' < "${CMDLINE_FILE}" > "${tmp}"
current="$(cat "${tmp}")"
rm -f "${tmp}"

if [ -z "${current}" ]; then
  log_error "plymouth-cmdline: cmdline is empty"
  exit 1
fi

read -r -a tokens <<< "${current}"

new_tokens=()
serial_console_removed=0
for t in "${tokens[@]}"; do
  case "$t" in
    quiet|splash|plymouth.ignore-serial-consoles|vt.global_cursor_default=*|consoleblank=*|loglevel=*|logo.nologo|plymouth.debug|vt.handoff=*|plymouth.enable=0|usbcore.autosuspend=*)
      ;;
    console=serial0,*|console=ttyAMA0,*|console=ttyS0,*)
      if [ "${MCU_TRANSPORT}" = "uart" ]; then
        serial_console_removed=1
      else
        new_tokens+=("$t")
      fi
      ;;
    *)
      new_tokens+=("$t")
      ;;
  esac
done

new_tokens+=(
  quiet
  splash
  plymouth.ignore-serial-consoles
  vt.global_cursor_default=0
  consoleblank=0
  loglevel=3
  logo.nologo
  vt.handoff=7
  usbcore.autosuspend=-1
)

new_line="${new_tokens[*]}"
printf '%s\n' "${new_line}" > "${CMDLINE_FILE}"

if [ "${MCU_TRANSPORT}" = "uart" ] && [ "${serial_console_removed}" -eq 1 ]; then
  log_info "plymouth-cmdline: removed serial console tokens for UART MCU transport"
fi

log_info "plymouth-cmdline: OK"
