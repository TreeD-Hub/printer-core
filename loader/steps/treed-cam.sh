#!/bin/bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
LIB_DIR="${REPO_DIR}/loader/lib"
source "${LIB_DIR}/common.sh"

log_info "Step treed-cam"


PI_USER="${PI_USER:-pi}"
PI_HOME="${PI_HOME:-/home/${PI_USER}}"
if ! grp="$(pi_primary_group "${PI_USER}")"; then
  exit 1
fi

SRC_DIR="${REPO_DIR}/runtime-scripts/treed-cam"
DST_ROOT="${PI_HOME}/treed/cam"
DST_BIN="${DST_ROOT}/bin"
DST_DATA="${DST_ROOT}/prints"

ensure_dir "${DST_BIN}"
ensure_dir "${DST_DATA}"

if [[ ! -d "${SRC_DIR}" ]]; then
  log_error "Missing runtime scripts directory: ${SRC_DIR}"
  exit 1
fi

log_info "Sync runtime scripts: ${SRC_DIR} -> ${DST_BIN}"
# Runtime-скрипты камеры синхронизируем полным снапшотом.
find "${DST_BIN}" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
cp -a "${SRC_DIR}/." "${DST_BIN}/"

find "${DST_BIN}" -type f -name '*.sh' -exec chmod +x {} \;

chown -R "${PI_USER}:${grp}" "${DST_ROOT}" || true

log_info "treed-cam: DONE (data at ${DST_DATA})"
