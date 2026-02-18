#!/bin/bash
set -euo pipefail

# ==========================================
# ШАГ LOADER: CHECK ENV
# ==========================================
# Назначение:
# - Валидирует базовое окружение loader перед provisioning.
# - Проверяет обязательные переменные PI_USER/PI_HOME и доступность home-каталога.
# Контур:
# - required (любой сбой останавливает provisioning).

# Блок 1: Библиотеки и базовая инициализация.
. "${REPO_DIR}/loader/lib/common.sh"

# Блок 2: Старт шага и проверка системных предусловий.
log_info "Step check-env: verifying environment"
ensure_root

# Блок 3: Валидация обязательного контракта loader-переменных.
# Контракт loader: PI_USER/PI_HOME обязаны быть определены до запуска step-скриптов.
if [ -z "${PI_USER:-}" ] || [ -z "${PI_HOME:-}" ]; then
  log_error "PI_USER or PI_HOME is not set"
  exit 1
fi

# Блок 4: Проверка доступности целевого home-каталога.
# Проверяем, что целевой home реально существует и пригоден для дальнейшего deploy.
if [ ! -d "${PI_HOME}" ]; then
  log_error "Home directory not found: ${PI_HOME}"
  exit 1
fi

# Блок 5: Диагностика ОС (не блокирует шаг).
if [ -f /etc/os-release ]; then
  . /etc/os-release
  log_info "Detected OS: ${PRETTY_NAME:-unknown}"
else
  log_warn "/etc/os-release not found; cannot detect OS"
fi

log_info "check-env: OK"
