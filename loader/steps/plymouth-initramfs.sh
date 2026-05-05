#!/bin/bash
set -euo pipefail

# ==========================================
# ШАГ LOADER: PLYMOUTH INITRAMFS
# ==========================================
# Назначение:
# - Применяет тему Plymouth и пересобирает initramfs.
# - Копирует собранный initrd в актуальный boot-раздел.
# Контур:
# - required для гарантированного старта splash на следующем boot.

# Блок 1: Библиотеки и root-права.
. "${REPO_DIR}/loader/lib/common.sh"
. "${REPO_DIR}/loader/lib/plymouth.sh"
. "${REPO_DIR}/loader/lib/rpi.sh"

ensure_root

# Блок 2: Предусловие наличия plymouth-set-default-theme.
if ! command -v plymouth-set-default-theme >/dev/null 2>&1; then
  log_warn "plymouth-initramfs: plymouth-set-default-theme not found; skipping step"
  exit 0
fi

# Блок 3: Пересборка initramfs с выбранной темой.
THEME="${PLYMOUTH_THEME_NAME:-treed}"
export PLYMOUTH_THEME_NAME="${THEME}"
log_info "plymouth-initramfs: applying theme '${THEME}' and rebuilding initramfs"

plymouth_set_default_theme
plymouth_rebuild_initramfs

BOOT_DIR="${BOOT_DIR:-$(detect_boot_dir)}"
initrd_src="/boot/initrd.img-$(uname -r)"
initrd_dst="${BOOT_DIR}/initrd.img-$(uname -r)"

# Блок 4: Копирование initrd в boot-раздел.
# Копируем собранный initrd в boot-раздел, откуда его читает прошивка RPi.
if [ -f "${initrd_src}" ]; then
  src_real="$(readlink -f "${initrd_src}" 2>/dev/null || printf '%s' "${initrd_src}")"
  dst_real="$(readlink -f "${initrd_dst}" 2>/dev/null || printf '%s' "${initrd_dst}")"
  if [ "${src_real}" = "${dst_real}" ]; then
    log_info "plymouth-initramfs: initrd source and destination are identical (${initrd_dst}), copy skipped"
  else
    cp -f "${initrd_src}" "${initrd_dst}"
    log_info "plymouth-initramfs: copied initrd to ${initrd_dst}"
  fi
else
  log_error "plymouth-initramfs: initrd source not found: ${initrd_src}"
  exit 1
fi

log_info "plymouth-initramfs: OK"
