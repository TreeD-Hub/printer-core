#!/bin/bash
set -euo pipefail

# ==========================================
# ШАГ LOADER: KLIPPERSCREEN INTEGR
# ==========================================
# Назначение:
# - Настраивает systemd override для интеграции KlipperScreen.
# - Применяет изменения идемпотентно и перезагружает daemon.
# Контур:
# - required при наличии KlipperScreen.service.

# Блок 1: Библиотеки и базовые параметры шага.
. "${REPO_DIR}/loader/lib/common.sh"

# Блок 2: Старт шага и параметры override/systemd.
log_info "Step klipperscreen-integr: configuring KlipperScreen systemd override"

OVERRIDE_DIR="/etc/systemd/system/KlipperScreen.service.d"
OVERRIDE_FILE="${OVERRIDE_DIR}/override.conf"
KS_UNIT="KlipperScreen.service"
KS_TIMEOUT="${TREED_KLIPPERSCREEN_START_TIMEOUT:-45}"

# Блок 3: Вспомогательная функция ожидания активного состояния сервиса.
wait_service_active() {
  local unit="$1"
  local timeout="$2"
  local i

  for i in $(seq 1 "${timeout}"); do
    if systemctl is-active --quiet "${unit}"; then
      return 0
    fi
    sleep 1
  done

  return 1
}

print_klipperscreen_service_diagnostics() {
  local unit="$1"

  systemctl --no-pager -l status "${unit}" || true
  journalctl -u "${unit}" -n 80 --no-pager || true
}

# Блок 4: Основной сценарий применения override и перезапуска сервиса.
ensure_root
ensure_dir "${OVERRIDE_DIR}"
backup_file_once "${OVERRIDE_FILE}"

# Override нужен, чтобы закрывать plymouth, сохраняя splash до старта UI.
cat > "${OVERRIDE_FILE}" <<EOF
[Unit]
After=systemd-user-sessions.service plymouth-quit.service
Wants=plymouth-quit.service

[Service]
ExecStartPre=/bin/sh -lc 'plymouth quit --retain-splash || true'
EOF

systemctl daemon-reload

if ! systemctl cat "${KS_UNIT}" >/dev/null 2>&1; then
  log_error "klipperscreen-integr: ${KS_UNIT} not found after install"
  exit 1
fi

if err="$(systemctl restart "${KS_UNIT}" 2>&1)"; then
  :
else
  rc=$?
  log_error "klipperscreen-integr: ${KS_UNIT} restart failed rc=${rc}: ${err}"
  print_klipperscreen_service_diagnostics "${KS_UNIT}"
  exit 1
fi

# После изменения unit-файлов проверяем, что сервис реально поднялся.
if ! wait_service_active "${KS_UNIT}" "${KS_TIMEOUT}"; then
  log_error "klipperscreen-integr: ${KS_UNIT} failed to become active within ${KS_TIMEOUT}s"
  print_klipperscreen_service_diagnostics "${KS_UNIT}"
  exit 1
fi

log_info "klipperscreen-integr: OK"
