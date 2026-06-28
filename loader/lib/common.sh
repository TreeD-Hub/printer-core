#!/bin/bash
set -euo pipefail

# ==========================================
# БИБЛИОТЕКА LOADER: COMMON
# ==========================================
# Назначение:
# - Дает общий набор helper-функций для всех шагов loader.
# - Централизует логирование, проверки root/путей и базовые операции с файлами.
# Контур:
# - required library; при нарушении базового контракта завершает вызывающий step.

# Блок 1: Контракт окружения библиотеки (REPO_DIR обязателен).
if [ -z "${REPO_DIR:-}" ]; then
  echo "[common] ERROR: REPO_DIR is not set" >&2
  exit 1
fi

# Блок 1a: Нормализация PATH для root/systemd/apt-контуров.
# При `sudo env PATH=...` user PATH может не содержать /usr/sbin.
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"

# Блок 1b: Noninteractive-контур системных установщиков.
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

# Блок 3: Режим экранного UI (TreeD Shell/KlipperScreen).
normalize_treed_ui_mode() {
  local raw="${1:-}"

  case "${raw}" in
    ts|TS|treed-shell|treed_shell|shell)
      printf '%s\n' "ts"
      ;;
    ks|KS|klipperscreen|KlipperScreen)
      printf '%s\n' "ks"
      ;;
    *)
      return 1
      ;;
  esac
}

resolve_treed_ui_mode() {
  local default_mode="${1:-ts}"
  local env_file="${TREED_UI_ENV_FILE:-/etc/default/treed-ui}"
  local raw_mode=""
  local normalized=""

  if [ -n "${TREED_UI_MODE:-}" ]; then
    raw_mode="${TREED_UI_MODE}"
  elif [ -f "${env_file}" ]; then
    raw_mode="$(sed -nE 's|^[[:space:]]*TREED_UI_MODE=([A-Za-z0-9_-]+)[[:space:]]*$|\1|p' "${env_file}" | tail -n1 | tr -d '\r\n')"
  fi

  raw_mode="${raw_mode:-${default_mode}}"
  if normalized="$(normalize_treed_ui_mode "${raw_mode}")"; then
    printf '%s\n' "${normalized}"
    return 0
  fi

  log_warn "Invalid TREED_UI_MODE=${raw_mode}; fallback to ${default_mode}"
  normalize_treed_ui_mode "${default_mode}"
}

# Блок 4: Базовые проверки и файловые helper-функции.
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

# Блок 5: Определение каталога KlipperScreen (systemd -> fallback).
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

# Блок 6: Определение primary group пользователя для корректного chown.
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

# Блок 7: Нормализация primary group runtime-пользователя.
# PI_GROUP остается переопределяемой, но по умолчанию определяется из PI_USER.
if [ -n "${PI_USER:-}" ] && [ -z "${PI_GROUP:-}" ]; then
  if ! PI_GROUP="$(pi_primary_group "${PI_USER}")"; then
    exit 1
  fi
  export PI_GROUP
fi
