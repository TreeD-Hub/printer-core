#!/bin/bash
set -euo pipefail
. "${REPO_DIR}/loader/lib/common.sh"
. "${REPO_DIR}/loader/lib/plymouth.sh"
ensure_root

log_info "Step plymouth-theme-install: installing TreeD plymouth theme"

SRC="${REPO_DIR}/plymouth/theme/treed"
DST="/usr/share/plymouth/themes/treed"

# Проверяем полный набор файлов темы до копирования.
for f in treed.plymouth treed.script watermark.png prog.png; do
  [ -f "${SRC}/${f}" ] || { log_error "plymouth-theme-install: missing ${SRC}/${f}"; exit 1; }
done

mkdir -p "${DST}"
rsync -a --delete "${SRC}/" "${DST}/"
plymouth_set_default_theme

log_info "plymouth-theme-install: OK"
