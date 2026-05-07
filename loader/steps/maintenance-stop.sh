#!/bin/bash
set -euo pipefail

# ==========================================
# ШАГ LOADER: MAINTENANCE STOP
# ==========================================
# Назначение:
# - Останавливает runtime-сервисы перед provisioning.
# - Разделяет required и best-effort контуры остановки.
# Контур:
# - required для базовых сервисов (klipper/moonraker),
# - best-effort для UI/камеры.

# Блок 1: Библиотеки и root-права.
. "${REPO_DIR}/loader/lib/common.sh"
ensure_root

# Блок 2: Режим maintenance (ранний выход при отключении шага).
if [ "${TREED_MAINTENANCE_MODE:-1}" != "1" ]; then
  log_info "Step maintenance-stop: skipped (TREED_MAINTENANCE_MODE=${TREED_MAINTENANCE_MODE:-0})"
  exit 0
fi

# Блок 3: Старт шага и параметры остановки сервисов.
log_info "Step maintenance-stop: stopping runtime services"

# Блок 4: Списки сервисов и таймауты остановки.
REQUIRED_SERVICES=(
  "klipper.service"
  "moonraker.service"
)

# Опциональные сервисы останавливаем в best-effort режиме.
BEST_EFFORT_SERVICES=(
  "KlipperScreen.service"
  "crowsnest.service"
)

REQUIRED_STOP_TIMEOUT="${TREED_REQUIRED_SERVICE_STOP_TIMEOUT:-20}"
BEST_EFFORT_STOP_TIMEOUT="${TREED_BEST_EFFORT_SERVICE_STOP_TIMEOUT:-10}"
TREED_ALLOW_MISSING_REQUIRED_SERVICES_ON_STOP="${TREED_ALLOW_MISSING_REQUIRED_SERVICES_ON_STOP:-1}"

# Блок 5: Вспомогательные функции (ожидание, required-stop, best-effort-stop).
wait_service_inactive() {
  local unit="$1"
  local timeout="$2"
  local i
  local state=""

  for i in $(seq 1 "${timeout}"); do
    state="$(systemctl show -p ActiveState --value "${unit}" 2>/dev/null || true)"
    case "${state}" in
      inactive|failed) return 0 ;;
    esac

    state="$(systemctl is-active "${unit}" 2>/dev/null || true)"
    case "${state}" in
      inactive|failed|unknown) return 0 ;;
    esac

    sleep 1
  done

  return 1
}

request_stop_service() {
  local unit="$1"

  if systemctl stop "${unit}" >/dev/null 2>&1; then
    log_info "maintenance-stop: stop requested for ${unit}"
    return 0
  fi

  log_error "maintenance-stop: failed to stop required ${unit}"
  systemctl --no-pager -l status "${unit}" || true
  return 1
}

force_kill_service() {
  local unit="$1"

  if systemctl kill --kill-who=all "${unit}" >/dev/null 2>&1; then
    log_warn "maintenance-stop: force-kill requested for ${unit}"
  else
    log_warn "maintenance-stop: force-kill failed for ${unit}"
  fi
}

stop_required_service() {
  local unit="$1"
  local state=""

  # Для критичных сервисов любой сбой — блокирующий.
  if ! systemctl cat "${unit}" >/dev/null 2>&1; then
    if [ "${TREED_ALLOW_MISSING_REQUIRED_SERVICES_ON_STOP}" = "1" ]; then
      log_warn "maintenance-stop: required ${unit} not found (bootstrap mode, continuing)"
      return 0
    fi
    log_error "maintenance-stop: required ${unit} not found"
    return 1
  fi

  state="$(systemctl show -p ActiveState --value "${unit}" 2>/dev/null || true)"
  case "${state}" in
    inactive|failed|"")
      log_info "maintenance-stop: ${unit} already inactive"
      ;;
    *)
      if ! request_stop_service "${unit}"; then
        return 1
      fi
      # Для stuck start-pre (activating) дополнительно гасим процессы юнита.
      if [ "${state}" = "activating" ]; then
        force_kill_service "${unit}"
      fi
      ;;
  esac

  if wait_service_inactive "${unit}" "${REQUIRED_STOP_TIMEOUT}"; then
    log_info "maintenance-stop: ${unit} inactive"
    return 0
  fi

  state="$(systemctl show -p ActiveState --value "${unit}" 2>/dev/null || true)"
  case "${state}" in
    activating|deactivating)
      log_warn "maintenance-stop: ${unit} stuck in ${state}, retry stop+kill"
      if ! request_stop_service "${unit}"; then
        return 1
      fi
      force_kill_service "${unit}"
      if wait_service_inactive "${unit}" 5; then
        log_info "maintenance-stop: ${unit} inactive after force-kill"
        return 0
      fi
      ;;
  esac

  state="$(systemctl is-active "${unit}" 2>/dev/null || true)"
  log_error "maintenance-stop: ${unit} did not become inactive within ${REQUIRED_STOP_TIMEOUT}s (state=${state:-unknown})"
  systemctl --no-pager -l status "${unit}" || true
  journalctl -u "${unit}" -n 120 --no-pager || true
  return 1
}

stop_best_effort_service() {
  local unit="$1"
  local state=""

  # Для опциональных сервисов ошибки не блокируют provisioning.
  if ! systemctl cat "${unit}" >/dev/null 2>&1; then
    log_info "maintenance-stop: ${unit} not found, skipping"
    return 0
  fi

  state="$(systemctl show -p ActiveState --value "${unit}" 2>/dev/null || true)"
  case "${state}" in
    inactive|failed|"")
      log_info "maintenance-stop: ${unit} already inactive"
      return 0
      ;;
    *)
      if ! systemctl stop "${unit}" >/dev/null 2>&1; then
      log_warn "maintenance-stop: failed to stop ${unit}, continuing"
      return 0
      fi
      log_info "maintenance-stop: stop requested for ${unit}"
      if [ "${state}" = "activating" ]; then
        force_kill_service "${unit}"
      fi
      ;;
  esac

  if wait_service_inactive "${unit}" "${BEST_EFFORT_STOP_TIMEOUT}"; then
    log_info "maintenance-stop: ${unit} inactive"
  else
    state="$(systemctl is-active "${unit}" 2>/dev/null || true)"
    log_warn "maintenance-stop: ${unit} did not become inactive within ${BEST_EFFORT_STOP_TIMEOUT}s (state=${state:-unknown}), continuing"
  fi
}

# Блок 6: Основной сценарий остановки сервисов.
for unit in "${REQUIRED_SERVICES[@]}"; do
  stop_required_service "${unit}"
done

for unit in "${BEST_EFFORT_SERVICES[@]}"; do
  stop_best_effort_service "${unit}"
done

log_info "maintenance-stop: OK"
