#!/bin/bash
set -euo pipefail

# ==========================================
# ШАГ LOADER: CAN SETUP
# ==========================================
# Назначение:
# - Поднимает CAN-интерфейс host для контура U2C -> EBB/Eddy.
# - Фиксирует параметры интерфейса через systemd oneshot unit.
# Контур:
# - required (без can0 недоступны required/optional CAN MCU).

# Блок 1: Библиотеки и root-предусловия.
. "${REPO_DIR}/loader/lib/common.sh"

log_info "Step can-setup: configure CAN interface for V2"
ensure_root

# Блок 2: Нормализация env-контракта CAN.
TREED_CAN_IFACE="${TREED_CAN_IFACE:-can0}"
TREED_CAN_BITRATE="${TREED_CAN_BITRATE:-1000000}"
TREED_CAN_TXQUEUE="${TREED_CAN_TXQUEUE:-1024}"

if ! printf '%s' "${TREED_CAN_IFACE}" | grep -Eq '^[A-Za-z0-9_.:-]+$'; then
  log_error "can-setup: TREED_CAN_IFACE has invalid format: ${TREED_CAN_IFACE}"
  exit 1
fi

case "${TREED_CAN_BITRATE}" in
  ''|*[!0-9]*)
    log_error "can-setup: TREED_CAN_BITRATE must be a positive integer, got: ${TREED_CAN_BITRATE}"
    exit 1
    ;;
esac

case "${TREED_CAN_TXQUEUE}" in
  ''|*[!0-9]*)
    log_error "can-setup: TREED_CAN_TXQUEUE must be a positive integer, got: ${TREED_CAN_TXQUEUE}"
    exit 1
    ;;
esac

if [ "${TREED_CAN_BITRATE}" -le 0 ] || [ "${TREED_CAN_TXQUEUE}" -le 0 ]; then
  log_error "can-setup: TREED_CAN_BITRATE and TREED_CAN_TXQUEUE must be > 0"
  exit 1
fi

if ! command -v ip >/dev/null 2>&1; then
  log_error "can-setup: command 'ip' is required but not found"
  exit 1
fi

# Блок 3: Runtime-скрипт применения параметров CAN.
CAN_SCRIPT="/usr/local/sbin/treed-can-setup.sh"
ensure_dir "/usr/local/sbin"
cat > "${CAN_SCRIPT}" <<'EOF'
#!/bin/bash
set -euo pipefail

CAN_ENV_FILE="/etc/default/treed-can-setup"
if [ -f "${CAN_ENV_FILE}" ]; then
  # shellcheck disable=SC1090
  . "${CAN_ENV_FILE}"
fi

TREED_CAN_IFACE="${TREED_CAN_IFACE:-can0}"
TREED_CAN_BITRATE="${TREED_CAN_BITRATE:-1000000}"
TREED_CAN_TXQUEUE="${TREED_CAN_TXQUEUE:-1024}"

IP_BIN="$(command -v ip || true)"
if [ -z "${IP_BIN}" ]; then
  echo "[can-setup] ERROR: ip command not found" >&2
  exit 1
fi

if ! "${IP_BIN}" link show "${TREED_CAN_IFACE}" >/dev/null 2>&1; then
  echo "[can-setup] ERROR: interface not found: ${TREED_CAN_IFACE}" >&2
  exit 1
fi

"${IP_BIN}" link set "${TREED_CAN_IFACE}" down || true
"${IP_BIN}" link set "${TREED_CAN_IFACE}" type can bitrate "${TREED_CAN_BITRATE}"
"${IP_BIN}" link set "${TREED_CAN_IFACE}" txqueuelen "${TREED_CAN_TXQUEUE}"
"${IP_BIN}" link set "${TREED_CAN_IFACE}" up
EOF
chmod 0755 "${CAN_SCRIPT}"

# Блок 4: Параметры unit через env-file.
CAN_ENV_FILE="/etc/default/treed-can-setup"
cat > "${CAN_ENV_FILE}" <<EOF
TREED_CAN_IFACE=${TREED_CAN_IFACE}
TREED_CAN_BITRATE=${TREED_CAN_BITRATE}
TREED_CAN_TXQUEUE=${TREED_CAN_TXQUEUE}
EOF
chmod 0644 "${CAN_ENV_FILE}"

# Блок 5: Systemd oneshot unit для повторяемого старта после reboot.
CAN_UNIT="/etc/systemd/system/treed-can-setup.service"
cat > "${CAN_UNIT}" <<'EOF'
[Unit]
Description=TreeD CAN interface setup
After=local-fs.target
Wants=network-pre.target
Before=network.target

[Service]
Type=oneshot
EnvironmentFile=/etc/default/treed-can-setup
ExecStart=/usr/local/sbin/treed-can-setup.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
chmod 0644 "${CAN_UNIT}"

# Блок 6: Активация unit и fail-fast проверка состояния CAN.
systemctl daemon-reload
systemctl enable treed-can-setup.service >/dev/null
systemctl restart treed-can-setup.service

if ip -details link show "${TREED_CAN_IFACE}" >/dev/null 2>&1; then
  log_info "can-setup: interface is present (${TREED_CAN_IFACE})"
else
  log_error "can-setup: cannot read interface details (${TREED_CAN_IFACE})"
  exit 1
fi

if ip link show "${TREED_CAN_IFACE}" | grep -q '<[^>]*UP[^>]*>'; then
  log_info "can-setup: interface is UP (${TREED_CAN_IFACE}, bitrate=${TREED_CAN_BITRATE}, txqueuelen=${TREED_CAN_TXQUEUE})"
else
  log_error "can-setup: interface is not UP (${TREED_CAN_IFACE})"
  exit 1
fi

log_info "can-setup: OK"
