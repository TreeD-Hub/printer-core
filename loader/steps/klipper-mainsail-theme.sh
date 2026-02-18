#!/bin/bash
set -euo pipefail

# ==========================================
# ШАГ LOADER: KLIPPER MAINSAIL THEME
# ==========================================
# Назначение:
# - Синхронизирует репозиторную тему Mainsail в runtime-конфиг.
# - Работает в best-effort с проверками наличия исходников.
# Контур:
# - best-effort (отсутствие темы не блокирует provisioning).

# Блок 1: Библиотеки и root-права.
. "${REPO_DIR}/loader/lib/common.sh"

ensure_root

# Блок 2: Определение пользователя/домашнего каталога и старт шага.
log_info "Step klipper-mainsail-theme: deploying Mainsail .theme"
PI_USER="${PI_USER:-${SUDO_USER:-pi}}"
PI_HOME="${PI_HOME:-/home/${PI_USER}}"
if [ -z "${PI_HOME}" ] || [ ! -d "${PI_HOME}" ]; then
  PI_HOME="$(getent passwd "${PI_USER}" | cut -d: -f6 || true)"
fi
if [ -z "${PI_HOME}" ] || [ ! -d "${PI_HOME}" ]; then
  log_error "klipper-mainsail-theme: cannot determine home for user ${PI_USER}"
  exit 1
fi

if ! grp="$(pi_primary_group "${PI_USER}")"; then
  exit 1
fi

# Блок 3: Расчет source/destination путей темы.
THEME_SRC="${REPO_DIR}/mainsail/.theme"
THEME_DST="${PI_HOME}/printer_data/config/.theme"

# Блок 4: Best-effort синхронизация темы и нормализация владельца.
if [ ! -d "${THEME_SRC}" ]; then
  # Тема Mainsail не критична для базового provisioning.
  log_warn "Mainsail theme source not found: ${THEME_SRC}; skipping"
else
  ensure_dir "${THEME_DST}"
  rsync -a --delete "${THEME_SRC}/" "${THEME_DST}/"
  chown -R "${PI_USER}:${grp}" "${THEME_DST}" || true
  log_info "Synced Mainsail theme to ${THEME_DST}"
fi

log_info "klipper-mainsail-theme: OK"
