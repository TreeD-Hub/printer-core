#!/bin/bash
set -euo pipefail

. "${REPO_DIR}/loader/lib/common.sh"
ensure_root

if [ "${TREED_MAINTENANCE_MODE:-1}" != "1" ]; then
  log_info "Step maintenance-start: skipped (TREED_MAINTENANCE_MODE=${TREED_MAINTENANCE_MODE:-0})"
  exit 0
fi

log_info "Step maintenance-start: starting runtime services"

start_required_service() {
  local unit="$1"

  if ! systemctl cat "${unit}" >/dev/null 2>&1; then
    log_warn "maintenance-start: ${unit} not found, skipping"
    return 0
  fi

  systemctl start "${unit}"
  if systemctl is-active --quiet "${unit}"; then
    log_info "maintenance-start: ${unit} active"
  else
    log_error "maintenance-start: ${unit} failed to become active"
    return 1
  fi
}

start_best_effort_service() {
  local unit="$1"

  if ! systemctl cat "${unit}" >/dev/null 2>&1; then
    log_info "maintenance-start: ${unit} not found, skipping"
    return 0
  fi

  if systemctl start "${unit}" >/dev/null 2>&1 && systemctl is-active --quiet "${unit}"; then
    log_info "maintenance-start: ${unit} active"
  else
    log_warn "maintenance-start: ${unit} is not active (continuing)"
  fi
}

start_required_service "klipper.service"
start_required_service "moonraker.service"
start_best_effort_service "KlipperScreen.service"
start_best_effort_service "crowsnest.service"

log_info "maintenance-start: OK"
