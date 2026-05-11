#!/bin/bash
set -euo pipefail

# ==========================================
# БИБЛИОТЕКА LOADER: PLYMOUTH
# ==========================================
# Назначение:
# - Инкапсулирует операции с темой Plymouth и пересборкой initramfs.
# - Используется шагами, где нужен единый контракт boot-визуализации.
# Контур:
# - required library для Plymouth-шагов.
# - операции изменения системы выполняются только явными helper-вызовами.

# Блок 1: Подключение общей библиотеки loader.
. "${REPO_DIR}/loader/lib/common.sh"

# Блок 2: Дефолт имени темы Plymouth (может быть переопределен env).
PLYMOUTH_THEME_NAME="${PLYMOUTH_THEME_NAME:-treed}"

# Блок 3: Установка default-темы Plymouth (best-effort).
plymouth_set_default_theme() {
  if command -v plymouth-set-default-theme >/dev/null 2>&1; then
    plymouth-set-default-theme "${PLYMOUTH_THEME_NAME}" >/dev/null 2>&1 || true
    log_info "Set plymouth default theme to ${PLYMOUTH_THEME_NAME}"
  else
    log_warn "plymouth-set-default-theme not found; cannot set default theme"
  fi
}

# Блок 4: Пересборка initramfs для текущего ядра.
plymouth_rebuild_initramfs() {
  if command -v update-initramfs >/dev/null 2>&1; then
    local kver
    kver="$(uname -r)"
    log_info "Rebuilding initramfs for kernel ${kver}"
    update-initramfs -u -k "${kver}"
  else
    log_warn "update-initramfs not found; skipping initramfs rebuild"
  fi
}
