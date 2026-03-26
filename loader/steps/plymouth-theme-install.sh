#!/bin/bash
set -euo pipefail
# ==========================================
# ШАГ LOADER: PLYMOUTH THEME INSTALL
# ==========================================
# Назначение:
# - Устанавливает тему Plymouth TreeD в системный каталог.
# - Проверяет обязательные файлы темы перед копированием.
# Контур:
# - required (тема должна существовать до шага initramfs).

# Блок 1: Библиотеки и root-права.
. "${REPO_DIR}/loader/lib/common.sh"
. "${REPO_DIR}/loader/lib/plymouth.sh"
ensure_root

# Блок 2: Расчет source/destination и старт шага.
log_info "Step plymouth-theme-install: installing TreeD plymouth theme"

SRC="${REPO_DIR}/plymouth/theme/treed"
DST="/usr/share/plymouth/themes/treed"

# Блок 3: Валидация полноты набора файлов темы.
# Проверяем полный набор файлов темы до копирования.
for f in treed.plymouth treed.script watermark.png prog.png; do
  [ -f "${SRC}/${f}" ] || { log_error "plymouth-theme-install: missing ${SRC}/${f}"; exit 1; }
done

# Блок 4: Идемпотентная раскладка темы и установка default-темы.
mkdir -p "${DST}"
rsync -a --delete "${SRC}/" "${DST}/"
plymouth_set_default_theme

log_info "plymouth-theme-install: OK"
