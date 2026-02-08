#!/bin/bash
set -euo pipefail

. "${REPO_DIR}/loader/lib/common.sh"
ensure_root

if [ "${TREED_MAINTENANCE_MODE:-1}" != "1" ]; then
  log_info "Step maintenance-stop: skipped (TREED_MAINTENANCE_MODE=${TREED_MAINTENANCE_MODE:-0})"
  exit 0
fi

log_info "Step maintenance-stop: stopping runtime services"

SERVICES=(
  "klipper"
  "moonraker"
  "KlipperScreen"
  "crowsnest"
)

for svc in "${SERVICES[@]}"; do
  unit="${svc}.service"
  if ! systemctl cat "${unit}" >/dev/null 2>&1; then
    log_info "maintenance-stop: ${unit} not found, skipping"
    continue
  fi

  if systemctl is-active --quiet "${unit}"; then
    if systemctl stop "${unit}" >/dev/null 2>&1; then
      log_info "maintenance-stop: stopped ${unit}"
    else
      log_warn "maintenance-stop: failed to stop ${unit}, continuing"
    fi
  else
    log_info "maintenance-stop: ${unit} already inactive"
  fi
done

log_info "maintenance-stop: OK"
