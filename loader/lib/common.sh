#!/bin/bash
set -euo pipefail

# ==========================================
# БИБЛИОТЕКА LOADER: COMMON
# ==========================================
# Назначение:
# - Дает общий набор helper-функций для всех шагов loader.
# - Централизует логирование, проверки root/путей и базовые операции с файлами.

# Блок 1: Контракт окружения библиотеки (REPO_DIR обязателен).
if [ -z "${REPO_DIR:-}" ]; then
  echo "[common] ERROR: REPO_DIR is not set" >&2
  exit 1
fi

# Блок 1a: Noninteractive-контур системных установщиков.
# По умолчанию loader не должен ждать подтверждений apt/dpkg/needrestart.
TREED_NONINTERACTIVE="${TREED_NONINTERACTIVE:-1}"
export TREED_NONINTERACTIVE

if [ "${TREED_NONINTERACTIVE}" = "1" ]; then
  export DEBIAN_FRONTEND="${DEBIAN_FRONTEND:-noninteractive}"
  export APT_LISTCHANGES_FRONTEND="${APT_LISTCHANGES_FRONTEND:-none}"
  export NEEDRESTART_MODE="${NEEDRESTART_MODE:-a}"
fi

# Блок 2: Логирование с единым форматом timestamp/уровней.
log_ts() {
  date +"%Y-%m-%d %H:%M:%S"
}

log_info() {
  echo "$(log_ts) [INFO] $*"
}

log_warn() {
  echo "$(log_ts) [WARN] $*" >&2
}

log_error() {
  echo "$(log_ts) [ERROR] $*" >&2
}

# Блок 3: Базовые проверки и файловые helper-функции.
ensure_root() {
  if [ "$(id -u)" -ne 0 ]; then
    log_error "This script must be run as root (use sudo)"
    exit 1
  fi
}

ensure_dir() {
  local dir="$1"
  if [ ! -d "$dir" ]; then
    mkdir -p "$dir"
    log_info "Created directory: ${dir}"
  fi
}

apt_update_noninteractive() {
  if [ "${TREED_NONINTERACTIVE:-1}" = "1" ]; then
    DEBIAN_FRONTEND=noninteractive \
    APT_LISTCHANGES_FRONTEND=none \
    NEEDRESTART_MODE=a \
      apt-get update
  else
    apt-get update
  fi
}

apt_get_noninteractive() {
  if [ "${TREED_NONINTERACTIVE:-1}" = "1" ]; then
    DEBIAN_FRONTEND=noninteractive \
    APT_LISTCHANGES_FRONTEND=none \
    NEEDRESTART_MODE=a \
      apt-get -y \
        -o Dpkg::Options::=--force-confdef \
        -o Dpkg::Options::=--force-confold \
        "$@"
  else
    apt-get "$@"
  fi
}

backup_file_once() {
  local path="$1"
  if [ -f "$path" ] && [ ! -f "${path}.bak" ]; then
    cp "$path" "${path}.bak"
    log_info "Backup created: ${path}.bak"
  fi
}

# Блок 4: Определение каталога KlipperScreen (systemd -> fallback).
detect_klipperscreen_home() {
  local fallback_home="${1:-}"
  local workdir=""

  if systemctl cat KlipperScreen.service >/dev/null 2>&1; then
    workdir="$(systemctl show -p WorkingDirectory --value KlipperScreen.service 2>/dev/null | tr -d '\r\n')"
    if [ -n "${workdir}" ] && [ -d "${workdir}" ]; then
      printf '%s\n' "${workdir}"
      return 0
    fi
  fi

  if [ -n "${fallback_home}" ]; then
    printf '%s\n' "${fallback_home}"
    return 0
  fi

  return 1
}

# Блок 5: Определение primary group пользователя для корректного chown.
pi_primary_group() {
  local user="${1:-}"
  local grp=""

  if [ -z "${user}" ]; then
    log_error "pi_primary_group: username is empty"
    return 1
  fi

  if ! grp="$(id -gn "${user}" 2>/dev/null)"; then
    log_error "pi_primary_group: failed to resolve primary group for user ${user}"
    return 1
  fi

  if [ -z "${grp}" ]; then
    log_error "pi_primary_group: resolved empty primary group for user ${user}"
    return 1
  fi

  printf '%s\n' "${grp}"
}
