#!/bin/bash
set -euo pipefail

# ==========================================
# ШАГ LOADER: UPDATE RECOVERY BOOTSTRAP
# ==========================================
# Назначение:
# - Устанавливает timer для короткой проверки срока сохранённого retry.
# Контур:
# - required; ошибка доставки или включения timer останавливает provisioning.

# Блок 1: Библиотеки и root-права.
. "${REPO_DIR}/loader/lib/common.sh"
ensure_root

# Блок 2: Проверка и атомарная установка fixed systemd units.
service_src="${REPO_DIR}/runtime-scripts/treed-update/treed-update-retry.service"
timer_src="${REPO_DIR}/runtime-scripts/treed-update/treed-update-retry.timer"
service_dst="/etc/systemd/system/treed-update-retry.service"
timer_dst="/etc/systemd/system/treed-update-retry.timer"

validate_source() {
  local source="$1" required="$2"
  if [ -L "${source}" ] || [ ! -f "${source}" ] || ! grep -Fqx "${required}" "${source}"; then
    return 1
  fi
}

validate_timer_schedule() {
  local key=""
  for key in OnBootSec OnUnitInactiveSec; do
    if ! grep -Eq "^[[:space:]]*${key}[[:space:]]*=[[:space:]]*[^[:space:]]+" "${timer_src}"; then
      return 1
    fi
  done
}

validate_units() {
  validate_source "${service_src}" 'ExecStart=/usr/local/sbin/treed-update-service retry-due' || return 1
  validate_source "${timer_src}" 'Unit=treed-update-retry.service' || return 1
  validate_timer_schedule || return 1
  if command -v systemd-analyze >/dev/null 2>&1; then
    systemd-analyze verify "${service_src}" "${timer_src}" || return 1
  fi
}

validate_loaded_units() {
  local unit="" load_state=""
  for unit in treed-update-retry.service treed-update-retry.timer; do
    load_state="$(systemctl show --property=LoadState --value "${unit}")" || return 1
    [ "${load_state}" = "loaded" ] || return 1
  done
}

activate_retry_timer() {
  systemctl daemon-reload || return 1
  validate_loaded_units || return 1
  systemctl enable --now treed-update-retry.timer
}

install_unit() {
  local source="$1" target="$2" temporary=""
  temporary="$(mktemp "${target}.XXXXXX")"
  if ! install -m 0644 "${source}" "${temporary}"; then
    rm -f "${temporary}"
    log_error "update-recovery-bootstrap: cannot install ${target}"
    exit 1
  fi
  sync -f "${temporary}"
  mv -f "${temporary}" "${target}"
  sync -f "$(dirname "${target}")"
}

if ! validate_units; then
  log_error "update-recovery-bootstrap: invalid retry systemd units"
  exit 1
fi
install_unit "${service_src}" "${service_dst}"
install_unit "${timer_src}" "${timer_dst}"

# Блок 3: Активируем только короткий периодический scheduler.
if ! activate_retry_timer; then
  log_error "update-recovery-bootstrap: retry units are not loaded or timer enable failed"
  exit 1
fi
log_info "update-recovery-bootstrap: retry timer enabled"
