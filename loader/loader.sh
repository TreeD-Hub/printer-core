#!/bin/bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Normalize CRLF for loader scripts (Windows clones) and ensure executable bits.
if [ -d "${REPO_DIR}/loader" ]; then
  find "${REPO_DIR}/loader" -type f -name "*.sh" -print0 | xargs -0 -r sed -i 's/\r$//'
  chmod +x "${REPO_DIR}/loader/loader.sh" || true
  chmod +x "${REPO_DIR}/loader/steps/"*.sh 2>/dev/null || true
fi

PI_USER="${PI_USER:-${SUDO_USER:-$(id -un)}}"
PI_HOME="$(getent passwd "$PI_USER" | cut -d: -f6 || true)"

if [ -z "${PI_HOME}" ] || [ ! -d "${PI_HOME}" ]; then
  echo "[loader] ERROR: cannot determine home for user ${PI_USER}" >&2
  exit 1
fi

export REPO_DIR
export PI_USER
export PI_HOME

. "${REPO_DIR}/loader/lib/common.sh"
. "${REPO_DIR}/loader/lib/rpi.sh"

BOOT_DIR="$(detect_boot_dir)"
CMDLINE_FILE="$(detect_cmdline_file "${BOOT_DIR}")"
CONFIG_FILE="$(detect_config_file "${BOOT_DIR}")"

# Align BOOT_DIR with actual config/cmdline locations when possible.
if [ -n "${CMDLINE_FILE}" ] && [ -n "${CONFIG_FILE}" ]; then
  cmd_dir="$(dirname "${CMDLINE_FILE}")"
  cfg_dir="$(dirname "${CONFIG_FILE}")"
  if [ "${cmd_dir}" = "${cfg_dir}" ]; then
    BOOT_DIR="${cmd_dir}"
  fi
elif [ -n "${CMDLINE_FILE}" ]; then
  BOOT_DIR="$(dirname "${CMDLINE_FILE}")"
elif [ -n "${CONFIG_FILE}" ]; then
  BOOT_DIR="$(dirname "${CONFIG_FILE}")"
fi

# Fail fast: do not continue with empty paths (prevents silent skips in steps).
if [ -z "${CMDLINE_FILE}" ] || [ ! -f "${CMDLINE_FILE}" ]; then
  echo "[loader] ERROR: cmdline.txt not found (BOOT_DIR=${BOOT_DIR})" >&2
  exit 1
fi
if [ -z "${CONFIG_FILE}" ] || [ ! -f "${CONFIG_FILE}" ]; then
  echo "[loader] ERROR: config.txt not found (BOOT_DIR=${BOOT_DIR})" >&2
  exit 1
fi

export BOOT_DIR
export CMDLINE_FILE
export CONFIG_FILE

TREED_MAINTENANCE_MODE="${TREED_MAINTENANCE_MODE:-1}"
export TREED_MAINTENANCE_MODE


. "${REPO_DIR}/loader/lib/plymouth.sh"

trap 'rc=$?; log_error "FAILED step=${CURRENT_STEP:-unknown} rc=${rc} line=${BASH_LINENO[0]} cmd=${BASH_COMMAND}"; exit ${rc}' ERR

STEPS=(
  "check-env"
  "detect-rpi"
  "timezone-sync"     # timezone + NTP baseline for UI and services
  "maintenance-stop"   # controlled stop of runtime services before provisioning
  "packages-core"
  "boot-hdmi-config"
  "rpi-uart-config"     # optional UART transport prep (enable_uart/serial-getty)
  "plymouth-theme-install"
  "plymouth-initramfs"
  "plymouth-initramfs-config"
  "plymouth-cmdline"
  "plymouth-systemd"
  "klipper-sync"        # репо -> ~/treed/klipper
  "klipper-profiles"    # fixed profile + serial update in staging
  "klipper-core"        # теперь КЛАДЁМ ВЕСЬ klipper/ в /config
  "klipper-anti-shutdown"
  "moonraker-config"
  "crowsnest-webcam"
  "treed-cam"
  "klipper-mainsail-theme"
  "klipperscreen-install"
  "klipperscreen-integr"
  "maintenance-start"  # bring core services back before final verification
  "verify"
)

log_info "TreeD loader starting"
log_info "REPO_DIR=${REPO_DIR}, PI_USER=${PI_USER}, PI_HOME=${PI_HOME}, CMDLINE_FILE=${CMDLINE_FILE}"
log_info "TREED_MAINTENANCE_MODE=${TREED_MAINTENANCE_MODE}"

for step in "${STEPS[@]}"; do
  CURRENT_STEP="$step"
  script="${REPO_DIR}/loader/steps/${step}.sh"
  if [ -x "$script" ]; then
    log_info "Running step: ${step}"
    "$script"
  elif [ -f "$script" ]; then
    log_info "Running step: ${step}"
    bash "$script"
  else
    log_warn "Step script not found: ${script} (skipping)"
  fi
done

log_info "TreeD loader finished successfully"
