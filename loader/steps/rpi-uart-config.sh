#!/bin/bash
set -euo pipefail

. "${REPO_DIR}/loader/lib/common.sh"
. "${REPO_DIR}/loader/lib/rpi.sh"

ensure_root

MCU_TRANSPORT_RAW="${TREED_MCU_TRANSPORT:-uart}"
MCU_UART_DEV="${TREED_MCU_UART_DEV:-/dev/serial0}"
TREED_UART_DISABLE_BT="${TREED_UART_DISABLE_BT:-1}"
UART_PERMS_RULE_FILE="/etc/udev/rules.d/99-treed-uart-perms.rules"

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

# Нормализуем enable_uart: оставляем единственную строку и фиксируем значение 1.
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
  # Для UART убираем конфликтный miniuart-bt и оставляем единичный dtoverlay=disable-bt.
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
  log_info "rpi-uart-config: bluetooth UART keep enabled (set TREED_UART_DISABLE_BT=1 to disable, default in uart mode)"
fi

# В UART-режиме serial-getty на аппаратных UART должен быть отключен и замаскирован.
for unit in serial-getty@ttyAMA0.service serial-getty@ttyS0.service; do
  if systemctl disable --now "${unit}" >/dev/null 2>&1; then
    log_info "rpi-uart-config: disabled ${unit}"
  else
    log_info "rpi-uart-config: ${unit} not present or already disabled"
  fi
  if systemctl mask "${unit}" >/dev/null 2>&1; then
    log_info "rpi-uart-config: masked ${unit}"
  else
    log_warn "rpi-uart-config: unable to mask ${unit}"
  fi
done

tmp="$(mktemp)"
cat > "${tmp}" <<'EOF'
# treed-managed: права UART для транспорта Klipper
KERNEL=="ttyAMA0", MODE="0660", GROUP="dialout"
KERNEL=="ttyS0", MODE="0660", GROUP="dialout"
EOF

if [ ! -f "${UART_PERMS_RULE_FILE}" ] || ! cmp -s "${tmp}" "${UART_PERMS_RULE_FILE}"; then
  cat "${tmp}" > "${UART_PERMS_RULE_FILE}"
  chmod 0644 "${UART_PERMS_RULE_FILE}"
  log_info "rpi-uart-config: wrote UART permissions rule ${UART_PERMS_RULE_FILE}"
else
  log_info "rpi-uart-config: UART permissions rule already up to date"
fi
rm -f "${tmp}"

if command -v udevadm >/dev/null 2>&1; then
  udevadm control --reload-rules >/dev/null 2>&1 || true
  for dev in /dev/ttyAMA0 /dev/ttyS0; do
    if [ -e "${dev}" ]; then
      udevadm trigger --name-match="${dev}" >/dev/null 2>&1 || true
    fi
  done
else
  log_warn "rpi-uart-config: udevadm not found, skipping rule reload"
fi

# Применяем права сразу (до следующего события udev), чтобы убрать гонку на первом старте.
if getent group dialout >/dev/null 2>&1; then
  for dev in /dev/ttyAMA0 /dev/ttyS0; do
    if [ -e "${dev}" ]; then
      chgrp dialout "${dev}" || true
      chmod 0660 "${dev}" || true
      log_info "rpi-uart-config: applied runtime permissions to ${dev}"
    fi
  done
else
  log_warn "rpi-uart-config: group dialout not found, skipping runtime permissions apply"
fi

if [ -e "${MCU_UART_DEV}" ] || [ -L "${MCU_UART_DEV}" ]; then
  log_info "rpi-uart-config: UART device path is present: ${MCU_UART_DEV}"
else
  log_warn "rpi-uart-config: UART device path is not present yet: ${MCU_UART_DEV}"
fi

if [ "${changed}" -eq 1 ]; then
  log_warn "rpi-uart-config: boot config changed; reboot is recommended before first UART run"
fi

log_info "rpi-uart-config: OK"
