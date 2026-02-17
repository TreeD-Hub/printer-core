#!/bin/bash
set -euo pipefail

. "${REPO_DIR}/loader/lib/common.sh"

log_info "Step timezone-sync: syncing timezone and NTP settings"
ensure_root

TREED_SET_TIMEZONE="${TREED_SET_TIMEZONE:-1}"
TREED_TIMEZONE="${TREED_TIMEZONE:-Europe/Moscow}"
TREED_ENABLE_NTP="${TREED_ENABLE_NTP:-1}"

# Нормализуем timezone (убираем CR/LF и пробелы по краям).
TREED_TIMEZONE="$(printf '%s' "${TREED_TIMEZONE}" | tr -d '\r' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
if [ -z "${TREED_TIMEZONE}" ]; then
  log_error "timezone-sync: TREED_TIMEZONE is empty after normalization"
  exit 1
fi

is_true() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

if ! command -v timedatectl >/dev/null 2>&1; then
  log_warn "timezone-sync: timedatectl not found, skipping"
  exit 0
fi

timezone_changed=0

if is_true "${TREED_SET_TIMEZONE}"; then
  # Основная проверка через timedatectl, fallback — через /usr/share/zoneinfo
  # (на случай временной недоступности timedated/dbus).
  if tz_list="$(timedatectl list-timezones 2>/dev/null)"; then
    if ! printf '%s\n' "${tz_list}" | grep -Fxq "${TREED_TIMEZONE}"; then
      log_error "timezone-sync: invalid timezone '${TREED_TIMEZONE}'"
      exit 1
    fi
  elif [ ! -f "/usr/share/zoneinfo/${TREED_TIMEZONE}" ]; then
    log_error "timezone-sync: invalid timezone '${TREED_TIMEZONE}'"
    exit 1
  else
    log_warn "timezone-sync: timedatectl list-timezones unavailable, validated timezone via /usr/share/zoneinfo"
  fi

  current_tz="$(timedatectl show -p Timezone --value 2>/dev/null || true)"
  if [ "${current_tz}" != "${TREED_TIMEZONE}" ]; then
    timedatectl set-timezone "${TREED_TIMEZONE}"
    timezone_changed=1
    log_info "timezone-sync: timezone set to ${TREED_TIMEZONE}"
  else
    log_info "timezone-sync: timezone already ${TREED_TIMEZONE}"
  fi
else
  log_info "timezone-sync: timezone update disabled (TREED_SET_TIMEZONE=${TREED_SET_TIMEZONE})"
fi

if is_true "${TREED_ENABLE_NTP}"; then
  timedatectl set-ntp true
  log_info "timezone-sync: NTP enabled"

  if systemctl cat systemd-timesyncd.service >/dev/null 2>&1; then
    if err="$(systemctl restart systemd-timesyncd.service 2>&1)"; then
      :
    else
      rc=$?
      log_warn "timezone-sync: failed to restart systemd-timesyncd.service rc=${rc}: ${err}"
    fi
  fi
else
  log_info "timezone-sync: NTP enable skipped (TREED_ENABLE_NTP=${TREED_ENABLE_NTP})"
fi

if [ "${timezone_changed}" = "1" ] && systemctl is-active --quiet KlipperScreen.service; then
  if err="$(systemctl restart KlipperScreen.service 2>&1)"; then
    log_info "timezone-sync: restarted KlipperScreen.service to apply new timezone"
  else
    rc=$?
    log_warn "timezone-sync: failed to restart KlipperScreen.service rc=${rc}: ${err}"
  fi
fi

log_info "timezone-sync: OK"
