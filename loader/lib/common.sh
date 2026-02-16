#!/bin/bash
set -euo pipefail

if [ -z "${REPO_DIR:-}" ]; then
  echo "[common] ERROR: REPO_DIR is not set" >&2
  exit 1
fi

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

backup_file_once() {
  local path="$1"
  if [ -f "$path" ] && [ ! -f "${path}.bak" ]; then
    cp "$path" "${path}.bak"
    log_info "Backup created: ${path}.bak"
  fi
}

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
