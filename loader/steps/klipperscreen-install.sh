#!/bin/bash
set -euo pipefail

. "${REPO_DIR}/loader/lib/common.sh"

ensure_root

log_info "Step klipperscreen-install: ensuring KlipperScreen is installed"

wait_service_active() {
  local unit="$1"
  local timeout="${2:-30}"
  local i

  for i in $(seq 1 "${timeout}"); do
    if systemctl is-active --quiet "${unit}"; then
      return 0
    fi
    sleep 1
  done

  return 1
}

assert_klipperscreen_healthy() {
  local unit="KlipperScreen.service"
  local timeout="${TREED_KLIPPERSCREEN_START_TIMEOUT:-45}"

  if ! systemctl cat "${unit}" >/dev/null 2>&1; then
    log_error "klipperscreen-install: ${unit} not found"
    exit 1
  fi

  if systemctl is-active --quiet "${unit}"; then
    log_info "klipperscreen-install: ${unit} already active"
    return 0
  fi

  log_warn "klipperscreen-install: ${unit} is not active, restarting"
  systemctl restart "${unit}"

  if wait_service_active "${unit}" "${timeout}"; then
    log_info "klipperscreen-install: ${unit} is active after restart"
    return 0
  fi

  log_error "klipperscreen-install: ${unit} failed to become active within ${timeout}s"
  systemctl --no-pager -l status "${unit}" || true
  exit 1
}

PI_USER="${PI_USER:-${SUDO_USER:-pi}}"
PI_HOME="${PI_HOME:-$(getent passwd "${PI_USER}" | cut -d: -f6 || true)}"

if [ -z "${PI_HOME}" ] || [ ! -d "${PI_HOME}" ]; then
  log_error "klipperscreen-install: cannot determine home for user ${PI_USER}"
  exit 1
fi

# Default behavior: install only when KlipperScreen.service is absent.
if systemctl cat KlipperScreen.service >/dev/null 2>&1 && [ "${TREED_FORCE_KLIPPERSCREEN_INSTALL:-0}" != "1" ]; then
  log_info "klipperscreen-install: KlipperScreen.service already exists, skipping install and validating service health"
  assert_klipperscreen_healthy
  exit 0
fi

if ! command -v git >/dev/null 2>&1; then
  log_info "klipperscreen-install: installing git"
  apt-get update
  apt-get -y install git
fi

KS_REPO_URL="${TREED_KLIPPERSCREEN_REPO:-https://github.com/jordanruthe/KlipperScreen.git}"
KS_STAGING_DIR="${PI_HOME}/treed/.staging/KlipperScreen"

rm -rf "${KS_STAGING_DIR}"
sudo -u "${PI_USER}" -H git clone --depth 1 "${KS_REPO_URL}" "${KS_STAGING_DIR}"

sudo -u "${PI_USER}" -H bash -lc "'${KS_STAGING_DIR}/scripts/KlipperScreen-install.sh'"

systemctl enable KlipperScreen.service >/dev/null 2>&1 || true
assert_klipperscreen_healthy

log_info "klipperscreen-install: OK"
