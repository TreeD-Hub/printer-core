#!/bin/bash
set -euo pipefail
# ==========================================
# ШАГ LOADER: PACKAGES CORE
# ==========================================
# Назначение:
# - Устанавливает базовые системные пакеты provisioning-контура.
# - Проверяет состояние критичных бинарников после установки.
# Контур:
# - required (база для последующих шагов).

# Блок 1: Библиотеки и root-права.
. "${REPO_DIR}/loader/lib/common.sh"
ensure_root

# Блок 2: Установка базового пакета зависимостей.
log_info "Step packages-core: installing core packages"
apt-get update
apt-get -y install \
  plymouth plymouth-themes plymouth-label \
  rsync curl v4l-utils git \
  python3 python3-pip python3-venv python3-dev \
  build-essential libffi-dev libssl-dev \
  gcc-avr binutils-avr avr-libc \
  gcc-arm-none-eabi binutils-arm-none-eabi libnewlib-arm-none-eabi

# Блок 3: Санитарная проверка socat (битый бинарник удаляем).
# TreeD работает через python3 и Unix-сокеты, поэтому битый socat удаляем.
if command -v socat >/dev/null 2>&1; then
  if socat -V >/dev/null 2>&1; then
    log_info "packages-core: socat binary is healthy"
  else
    rc=$?
    log_warn "packages-core: socat is broken (rc=${rc}), removing package"
    apt-get -y purge socat || true
  fi
else
  log_info "packages-core: socat not installed (expected)"
fi

# Блок 4: Проверка наличия script-плагина Plymouth.
if ls /usr/lib/*/plymouth/script.so >/dev/null 2>&1; then
  log_info "packages-core: plymouth script engine present"
else
  log_warn "packages-core: plymouth script engine missing; check distro packages"
fi

log_info "packages-core: OK"
