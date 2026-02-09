#!/bin/bash
set -euo pipefail

. "${REPO_DIR}/loader/lib/common.sh"
ensure_root

if [ "${TREED_MAINTENANCE_MODE:-1}" != "1" ]; then
  log_info "Step maintenance-stop: skipped (TREED_MAINTENANCE_MODE=${TREED_MAINTENANCE_MODE:-0})"
  exit 0
fi

log_info "Step maintenance-stop: stopping runtime services"

REQUIRED_SERVICES=(
  "klipper.service"
  "moonraker.service"
)

BEST_EFFORT_SERVICES=(
  "KlipperScreen.service"
  "crowsnest.service"
)

REQUIRED_STOP_TIMEOUT="${TREED_REQUIRED_SERVICE_STOP_TIMEOUT:-20}"
BEST_EFFORT_STOP_TIMEOUT="${TREED_BEST_EFFORT_SERVICE_STOP_TIMEOUT:-10}"

wait_service_inactive() {
  local unit="$1"
  local timeout="$2"
  local i
  local state=""

  for i in $(seq 1 "${timeout}"); do
    state="$(systemctl show -p ActiveState --value "${unit}" 2>/dev/null || true)"
    case "${state}" in
      inactive|failed) return 0 ;;
    esac

    state="$(systemctl is-active "${unit}" 2>/dev/null || true)"
    case "${state}" in
      inactive|failed|unknown) return 0 ;;
    esac

    sleep 1
  done

  return 1
}

stop_required_service() {
  local unit="$1"
  local state=""

  if ! systemctl cat "${unit}" >/dev/null 2>&1; then
    log_error "maintenance-stop: required ${unit} not found"
    return 1
  fi

  if systemctl is-active --quiet "${unit}"; then
    if systemctl stop "${unit}" >/dev/null 2>&1; then
      log_info "maintenance-stop: stop requested for ${unit}"
    else
      log_error "maintenance-stop: failed to stop required ${unit}"
      systemctl --no-pager -l status "${unit}" || true
      return 1
    fi
  else
    log_info "maintenance-stop: ${unit} already inactive"
  fi

  if wait_service_inactive "${unit}" "${REQUIRED_STOP_TIMEOUT}"; then
    log_info "maintenance-stop: ${unit} inactive"
    return 0
  fi

  state="$(systemctl is-active "${unit}" 2>/dev/null || true)"
  log_error "maintenance-stop: ${unit} did not become inactive within ${REQUIRED_STOP_TIMEOUT}s (state=${state:-unknown})"
  systemctl --no-pager -l status "${unit}" || true
  return 1
}

stop_best_effort_service() {
  local unit="$1"
  local state=""

  if ! systemctl cat "${unit}" >/dev/null 2>&1; then
    log_info "maintenance-stop: ${unit} not found, skipping"
    return 0
  fi

  if systemctl is-active --quiet "${unit}"; then
    if systemctl stop "${unit}" >/dev/null 2>&1; then
      log_info "maintenance-stop: stop requested for ${unit}"
    else
      log_warn "maintenance-stop: failed to stop ${unit}, continuing"
      return 0
    fi
  else
    log_info "maintenance-stop: ${unit} already inactive"
    return 0
  fi

  if wait_service_inactive "${unit}" "${BEST_EFFORT_STOP_TIMEOUT}"; then
    log_info "maintenance-stop: ${unit} inactive"
  else
    state="$(systemctl is-active "${unit}" 2>/dev/null || true)"
    log_warn "maintenance-stop: ${unit} did not become inactive within ${BEST_EFFORT_STOP_TIMEOUT}s (state=${state:-unknown}), continuing"
  fi
}

for unit in "${REQUIRED_SERVICES[@]}"; do
  stop_required_service "${unit}"
done

for unit in "${BEST_EFFORT_SERVICES[@]}"; do
  stop_best_effort_service "${unit}"
done

log_info "maintenance-stop: OK"
