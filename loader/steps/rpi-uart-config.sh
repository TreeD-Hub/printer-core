#!/bin/bash
set -euo pipefail

. "${REPO_DIR}/loader/lib/common.sh"
. "${REPO_DIR}/loader/lib/rpi.sh"

ensure_root

MCU_TRANSPORT_RAW="${TREED_MCU_TRANSPORT:-usb}"
MCU_UART_DEV="${TREED_MCU_UART_DEV:-/dev/serial0}"
TREED_UART_DISABLE_BT="${TREED_UART_DISABLE_BT:-0}"

is_true() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

case "${MCU_TRANSPORT_RAW}" in
  usb|USB) MCU_TRANSPORT="usb" ;;
  uart|UART) MCU_TRANSPORT="uart" ;;
  *)
    log_error "rpi-uart-config: unsupported TREED_MCU_TRANSPORT='${MCU_TRANSPORT_RAW}' (expected: usb|uart)"
    exit 1
    ;;
esac

log_info "Step rpi-uart-config: apply MCU transport prerequisites (${MCU_TRANSPORT})"

if [ "${MCU_TRANSPORT}" != "uart" ]; then
  log_info "rpi-uart-config: skipped (TREED_MCU_TRANSPORT=${MCU_TRANSPORT})"
  exit 0
fi

if [ -z "${CONFIG_FILE:-}" ] || [ ! -f "${CONFIG_FILE}" ]; then
  BOOT_DIR="${BOOT_DIR:-$(detect_boot_dir)}"
  CONFIG_FILE="$(detect_config_file "${BOOT_DIR}")"
fi

if [ -z "${CONFIG_FILE:-}" ] || [ ! -f "${CONFIG_FILE}" ]; then
  log_error "rpi-uart-config: config.txt not found: ${CONFIG_FILE:-<empty>}"
  exit 1
fi

backup_file_once "${CONFIG_FILE}"
changed=0

tmp="$(mktemp)"
awk '
  BEGIN { seen=0 }
  /^[[:space:]]*enable_uart[[:space:]]*=/ {
    if (!seen) {
      print "enable_uart=1"
      seen=1
    }
    next
  }
  { print }
  END {
    if (!seen) {
      print "enable_uart=1"
    }
  }
' "${CONFIG_FILE}" > "${tmp}"

if ! cmp -s "${tmp}" "${CONFIG_FILE}"; then
  cat "${tmp}" > "${CONFIG_FILE}"
  changed=1
  log_info "rpi-uart-config: set enable_uart=1 in ${CONFIG_FILE}"
else
  log_info "rpi-uart-config: enable_uart=1 already configured"
fi
rm -f "${tmp}"

if is_true "${TREED_UART_DISABLE_BT}"; then
  tmp="$(mktemp)"
  awk '
    BEGIN { seen=0 }
    /^[[:space:]]*dtoverlay[[:space:]]*=[[:space:]]*disable-bt([[:space:]]*#.*)?$/ {
      if (!seen) {
        print "dtoverlay=disable-bt"
        seen=1
      }
      next
    }
    /^[[:space:]]*dtoverlay[[:space:]]*=[[:space:]]*miniuart-bt([[:space:]]*#.*)?$/ {
      next
    }
    { print }
    END {
      if (!seen) {
        print "dtoverlay=disable-bt"
      }
    }
  ' "${CONFIG_FILE}" > "${tmp}"

  if ! cmp -s "${tmp}" "${CONFIG_FILE}"; then
    cat "${tmp}" > "${CONFIG_FILE}"
    changed=1
    log_info "rpi-uart-config: set dtoverlay=disable-bt in ${CONFIG_FILE}"
  else
    log_info "rpi-uart-config: dtoverlay=disable-bt already configured"
  fi
  rm -f "${tmp}"
else
  log_info "rpi-uart-config: bluetooth UART keep enabled (set TREED_UART_DISABLE_BT=1 to disable)"
fi

for unit in serial-getty@ttyAMA0.service serial-getty@ttyS0.service; do
  if systemctl disable --now "${unit}" >/dev/null 2>&1; then
    log_info "rpi-uart-config: disabled ${unit}"
  else
    log_info "rpi-uart-config: ${unit} not present or already disabled"
  fi
done

if [ -e "${MCU_UART_DEV}" ] || [ -L "${MCU_UART_DEV}" ]; then
  log_info "rpi-uart-config: UART device path is present: ${MCU_UART_DEV}"
else
  log_warn "rpi-uart-config: UART device path is not present yet: ${MCU_UART_DEV}"
fi

if [ "${changed}" -eq 1 ]; then
  log_warn "rpi-uart-config: boot config changed; reboot is recommended before first UART run"
fi

log_info "rpi-uart-config: OK"
