#!/bin/bash
set -euo pipefail

. "${REPO_DIR}/loader/lib/common.sh"
ensure_root

if [ "${TREED_MAINTENANCE_MODE:-1}" != "1" ]; then
  log_info "Step maintenance-start: skipped (TREED_MAINTENANCE_MODE=${TREED_MAINTENANCE_MODE:-0})"
  exit 0
fi

log_info "Step maintenance-start: starting runtime services"

REQUIRED_START_TIMEOUT="${TREED_REQUIRED_SERVICE_START_TIMEOUT:-30}"
BEST_EFFORT_START_TIMEOUT="${TREED_BEST_EFFORT_SERVICE_START_TIMEOUT:-20}"

wait_service_active() {
  local unit="$1"
  local timeout="$2"
  local i
  local state=""

  for i in $(seq 1 "${timeout}"); do
    if systemctl is-active --quiet "${unit}"; then
      return 0
    fi

    state="$(systemctl is-active "${unit}" 2>/dev/null || true)"
    case "${state}" in
      failed)
        return 2
        ;;
    esac

    sleep 1
  done

  return 1
}

start_required_service() {
  local unit="$1"
  local rc=0

  if ! systemctl cat "${unit}" >/dev/null 2>&1; then
    log_warn "maintenance-start: ${unit} not found, skipping"
    return 0
  fi

  systemctl start "${unit}"

  if wait_service_active "${unit}" "${REQUIRED_START_TIMEOUT}"; then
    log_info "maintenance-start: ${unit} active"
    return 0
  fi

  rc=$?
  case "${rc}" in
    2)
      log_error "maintenance-start: ${unit} entered failed state during startup"
      ;;
    *)
      log_error "maintenance-start: ${unit} did not become active within ${REQUIRED_START_TIMEOUT}s"
      ;;
  esac
  systemctl --no-pager -l status "${unit}" || true
  return 1
}

start_best_effort_service() {
  local unit="$1"
  local rc=0

  if ! systemctl cat "${unit}" >/dev/null 2>&1; then
    log_info "maintenance-start: ${unit} not found, skipping"
    return 0
  fi

  if ! systemctl start "${unit}" >/dev/null 2>&1; then
    log_warn "maintenance-start: failed to start ${unit} (continuing)"
    return 0
  fi

  if wait_service_active "${unit}" "${BEST_EFFORT_START_TIMEOUT}"; then
    log_info "maintenance-start: ${unit} active"
    return 0
  fi

  rc=$?
  case "${rc}" in
    2)
      log_warn "maintenance-start: ${unit} entered failed state during startup (continuing)"
      ;;
    *)
      log_warn "maintenance-start: ${unit} did not become active within ${BEST_EFFORT_START_TIMEOUT}s (continuing)"
      ;;
  esac
  if [ "${TREED_MAINTENANCE_STATUS_LOG:-0}" = "1" ]; then
    systemctl --no-pager -l status "${unit}" || true
  fi
}

start_required_service "klipper.service"
start_required_service "moonraker.service"
start_best_effort_service "KlipperScreen.service"
start_best_effort_service "crowsnest.service"

log_info "maintenance-start: OK"
