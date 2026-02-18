#!/bin/bash
set -euo pipefail

. "${REPO_DIR}/loader/lib/common.sh"

if [ -z "${PI_USER:-}" ]; then
  log_error "klipper-sync: PI_USER is not set"
  exit 1
fi

if ! grp="$(pi_primary_group "${PI_USER}")"; then
  exit 1
fi

log_info "Step klipper-sync: syncing Klipper config tree to /home/${PI_USER}/treed/klipper"

KLIPPER_SOURCE_DIR="${REPO_DIR}/klipper"
KLIPPER_TARGET_DIR="${PI_HOME}/treed/klipper"

if [ ! -d "${KLIPPER_SOURCE_DIR}" ]; then
  log_error "klipper-sync: source dir not found: ${KLIPPER_SOURCE_DIR}"
  exit 1
fi

if [ -z "$(find "${KLIPPER_SOURCE_DIR}" -mindepth 1 -print -quit 2>/dev/null)" ]; then
  log_error "klipper-sync: source dir is empty: ${KLIPPER_SOURCE_DIR}"
  exit 1
fi

ensure_dir "${PI_HOME}/treed"

# staging-контур синхронизируется целиком: сначала очистка, затем полное копирование из репозитория.
rm -rf "${KLIPPER_TARGET_DIR}"
mkdir -p "${KLIPPER_TARGET_DIR}"

cp -a "${KLIPPER_SOURCE_DIR}/." "${KLIPPER_TARGET_DIR}/"

chown -R "${PI_USER}:${grp}" "${KLIPPER_TARGET_DIR}"

log_info "klipper-sync: synced ${KLIPPER_SOURCE_DIR} -> ${KLIPPER_TARGET_DIR}"
