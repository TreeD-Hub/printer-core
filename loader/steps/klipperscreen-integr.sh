#!/bin/bash
set -euo pipefail

. "${REPO_DIR}/loader/lib/common.sh"

log_info "Step klipperscreen-integr: configuring KlipperScreen systemd override"

OVERRIDE_DIR="/etc/systemd/system/KlipperScreen.service.d"
OVERRIDE_FILE="${OVERRIDE_DIR}/override.conf"
KS_UNIT="KlipperScreen.service"
KS_TIMEOUT="${TREED_KLIPPERSCREEN_START_TIMEOUT:-45}"

wait_service_active() {
  local unit="$1"
  local timeout="$2"
  local i

  for i in $(seq 1 "${timeout}"); do
    if systemctl is-active --quiet "${unit}"; then
      return 0
    fi
    sleep 1
  done

  return 1
}

ensure_root
ensure_dir "${OVERRIDE_DIR}"
backup_file_once "${OVERRIDE_FILE}"

cat > "${OVERRIDE_FILE}" <<EOF
[Unit]
After=systemd-user-sessions.service plymouth-quit.service
Wants=plymouth-quit.service

[Service]
ExecStartPre=/bin/sh -lc 'plymouth quit --retain-splash || true'
EOF

systemctl daemon-reload

if ! systemctl cat "${KS_UNIT}" >/dev/null 2>&1; then
  log_error "klipperscreen-integr: ${KS_UNIT} not found after install"
  exit 1
fi

systemctl restart "${KS_UNIT}"

if ! wait_service_active "${KS_UNIT}" "${KS_TIMEOUT}"; then
  log_error "klipperscreen-integr: ${KS_UNIT} failed to become active within ${KS_TIMEOUT}s"
  systemctl --no-pager -l status "${KS_UNIT}" || true
  exit 1
fi

log_info "klipperscreen-integr: OK"
