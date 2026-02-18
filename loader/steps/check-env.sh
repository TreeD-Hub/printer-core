#!/bin/bash
set -euo pipefail

. "${REPO_DIR}/loader/lib/common.sh"

log_info "Step check-env: verifying environment"

ensure_root

# Контракт loader: PI_USER/PI_HOME обязаны быть определены до запуска step-скриптов.
if [ -z "${PI_USER:-}" ] || [ -z "${PI_HOME:-}" ]; then
  log_error "PI_USER or PI_HOME is not set"
  exit 1
fi

# Проверяем, что целевой home реально существует и пригоден для дальнейшего deploy.
if [ ! -d "${PI_HOME}" ]; then
  log_error "Home directory not found: ${PI_HOME}"
  exit 1
fi

if [ -f /etc/os-release ]; then
  . /etc/os-release
  log_info "Detected OS: ${PRETTY_NAME:-unknown}"
else
  log_warn "/etc/os-release not found; cannot detect OS"
fi

log_info "check-env: OK"
