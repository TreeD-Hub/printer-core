#!/bin/bash
set -euo pipefail

. "${REPO_DIR}/loader/lib/common.sh"

log_info "Step plymouth-systemd: adjusting systemd units for plymouth and tty1"

TREED_MASK_TTY1="${TREED_MASK_TTY1:-1}"
TTY1_UNIT="getty@tty1.service"

# systemctl is-enabled может вернуть non-zero даже при валидном состоянии, поэтому читаем и вывод, и rc.
set +e
state_out="$(systemctl is-enabled "${TTY1_UNIT}" 2>&1)"
rc=$?
set -e

case "${state_out}" in
  enabled|enabled-runtime|linked|linked-runtime|alias|disabled|static|indirect|masked|masked-runtime|generated)
    state="${state_out}"
    ;;
  *)
    # Нефатально: если статус не удалось прочитать, продолжаем, расхождения поймает verify.
    log_warn "plymouth-systemd: systemctl is-enabled ${TTY1_UNIT} rc=${rc}: ${state_out}"
    state=""
    ;;
esac

if [ "${TREED_MASK_TTY1}" = "0" ]; then
  log_info "TREED_MASK_TTY1=0: keeping ${TTY1_UNIT} unmasked (local console recovery enabled)"
  if [ "${state}" = "masked" ]; then
    # Нефатально: даже при ошибке unmask продолжаем, verify покажет несоответствие.
    if err="$(systemctl unmask "${TTY1_UNIT}" 2>&1)"; then
      log_info "Unmasked ${TTY1_UNIT}"
    else
      rc=$?
      log_warn "plymouth-systemd: systemctl unmask ${TTY1_UNIT} failed rc=${rc}: ${err}"
    fi
  else
    log_info "${TTY1_UNIT} already unmasked (state=${state})"
  fi
else
  log_info "TREED_MASK_TTY1=${TREED_MASK_TTY1}: masking ${TTY1_UNIT} (local console recovery disabled)"
  if [ "${state}" != "masked" ]; then
    # Нефатально: даже при ошибке mask продолжаем, verify покажет несоответствие.
    if err="$(systemctl mask "${TTY1_UNIT}" 2>&1)"; then
      log_info "Masked ${TTY1_UNIT}"
    else
      rc=$?
      log_warn "plymouth-systemd: systemctl mask ${TTY1_UNIT} failed rc=${rc}: ${err}"
    fi
  else
    log_info "${TTY1_UNIT} already masked"
  fi
fi

# Нефатально: эти unit могут отсутствовать в части дистрибутивов, но пропуск не должен быть тихим.
for unit in plymouth-quit.service plymouth-quit-wait.service; do
  if err="$(systemctl unmask "${unit}" 2>&1)"; then
    :
  else
    rc=$?
    log_warn "plymouth-systemd: systemctl unmask ${unit} failed rc=${rc}: ${err}"
  fi
done

if systemctl list-unit-files | grep -q '^treed-plymouth-late.service'; then
  # Нефатальная очистка: если этот unit сейчас не отключается, loader все равно продолжает.
  if err="$(systemctl disable --now treed-plymouth-late.service 2>&1)"; then
    :
  else
    rc=$?
    log_warn "plymouth-systemd: systemctl disable --now treed-plymouth-late.service failed rc=${rc}: ${err}"
  fi
  rm -f /etc/systemd/system/treed-plymouth-late.service
fi

if err="$(systemctl daemon-reload 2>&1)"; then
  :
else
  rc=$?
  log_error "plymouth-systemd: systemctl daemon-reload failed rc=${rc}: ${err}"
  exit 1
fi

log_info "plymouth-systemd: OK"
