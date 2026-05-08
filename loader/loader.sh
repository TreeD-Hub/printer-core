#!/bin/bash
set -euo pipefail

# ==========================================
# ОРКЕСТРАТОР LOADER: MAIN ENTRYPOINT
# ==========================================
# Назначение:
# - Управляет полным жизненным циклом provisioning TreeD через последовательность step-скриптов.
# - Применяет единый fail-fast/best-effort контракт и экспортирует общий контекст шагов.
# Контур:
# - required-steps: любая ошибка завершает loader с ненулевым кодом,
# - optional-steps: ошибка логируется и не прерывает provisioning.

# Блок 1: Определение корня репозитория и базовой рабочей директории.
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Блок 2: Нормализация shell-скриптов loader после Windows checkout.
# - Убираем CRLF для *.sh в loader/**,
# - Восстанавливаем executable-бит для entrypoint и step-скриптов.
if [ -d "${REPO_DIR}/loader" ]; then
  find "${REPO_DIR}/loader" -type f -name "*.sh" -print0 | xargs -0 -r sed -i 's/\r$//'
  chmod +x "${REPO_DIR}/loader/loader.sh" || true
  chmod +x "${REPO_DIR}/loader/steps/"*.sh 2>/dev/null || true
fi

# Блок 3: Определение целевого пользователя deploy и его home.
PI_USER="${PI_USER:-${SUDO_USER:-$(id -un)}}"
PI_HOME="$(getent passwd "$PI_USER" | cut -d: -f6 || true)"

if [ -z "${PI_HOME}" ] || [ ! -d "${PI_HOME}" ]; then
  echo "[loader] ERROR: cannot determine home for user ${PI_USER}" >&2
  exit 1
fi

# Блок 4: Экспорт общего контекста и подключение базовых библиотек.
export REPO_DIR
export PI_USER
export PI_HOME

. "${REPO_DIR}/loader/lib/common.sh"
. "${REPO_DIR}/loader/lib/boot-env.sh"

# Блок 5: Определение boot-backend и путей (RPi/Armbian/Extlinux aware).
BOOT_DIR="$(detect_boot_dir)"
TREED_BOOT_BACKEND="$(detect_boot_backend "${BOOT_DIR}")"
CMDLINE_FILE="$(detect_cmdline_file "${BOOT_DIR}")"
CONFIG_FILE="$(detect_config_file "${BOOT_DIR}")"
ARMBIAN_ENV_FILE="$(detect_armbian_env_file "${BOOT_DIR}")"
EXTLINUX_FILE="$(detect_extlinux_file "${BOOT_DIR}")"

# Блок 6: Нормализация BOOT_DIR до фактического каталога boot-файлов.
if [ -n "${CMDLINE_FILE}" ] && [ -n "${CONFIG_FILE}" ]; then
  cmd_dir="$(dirname "${CMDLINE_FILE}")"
  cfg_dir="$(dirname "${CONFIG_FILE}")"
  if [ "${cmd_dir}" = "${cfg_dir}" ]; then
    BOOT_DIR="${cmd_dir}"
  elif [ -n "${cfg_dir}" ]; then
    BOOT_DIR="${cfg_dir}"
  fi
elif [ -n "${CONFIG_FILE}" ]; then
  BOOT_DIR="$(dirname "${CONFIG_FILE}")"
elif [ -n "${CMDLINE_FILE}" ]; then
  BOOT_DIR="$(dirname "${CMDLINE_FILE}")"
elif [ -n "${ARMBIAN_ENV_FILE}" ]; then
  BOOT_DIR="$(dirname "${ARMBIAN_ENV_FILE}")"
elif [ -n "${EXTLINUX_FILE}" ]; then
  BOOT_DIR="$(dirname "$(dirname "${EXTLINUX_FILE}")")"
fi

# Блок 7: Валидация boot-backend (fail-fast без legacy-only ограничений).
case "${TREED_BOOT_BACKEND}" in
  rpi)
    if [ -z "${CMDLINE_FILE}" ] || [ ! -f "${CMDLINE_FILE}" ]; then
      echo "[loader] ERROR: rpi backend requires cmdline.txt (BOOT_DIR=${BOOT_DIR})" >&2
      exit 1
    fi
    if [ -z "${CONFIG_FILE}" ] || [ ! -f "${CONFIG_FILE}" ]; then
      echo "[loader] ERROR: rpi backend requires config.txt (BOOT_DIR=${BOOT_DIR})" >&2
      exit 1
    fi
    ;;
  armbian)
    if [ -z "${ARMBIAN_ENV_FILE}" ] || [ ! -f "${ARMBIAN_ENV_FILE}" ]; then
      echo "[loader] ERROR: armbian backend requires /boot/armbianEnv.txt (BOOT_DIR=${BOOT_DIR})" >&2
      exit 1
    fi
    ;;
  extlinux)
    if [ -z "${EXTLINUX_FILE}" ] || [ ! -f "${EXTLINUX_FILE}" ]; then
      echo "[loader] ERROR: extlinux backend requires /boot/extlinux/extlinux.conf (BOOT_DIR=${BOOT_DIR})" >&2
      exit 1
    fi
    ;;
  *)
    echo "[loader] ERROR: unsupported boot backend '${TREED_BOOT_BACKEND}' (expected: rpi|armbian|extlinux)" >&2
    exit 1
    ;;
esac

export BOOT_DIR
export TREED_BOOT_BACKEND
export CMDLINE_FILE
export CONFIG_FILE
export ARMBIAN_ENV_FILE
export EXTLINUX_FILE

# Блок 8: Глобальные режимы оркестрации (maintenance/deploy mode).
TREED_MAINTENANCE_MODE="${TREED_MAINTENANCE_MODE:-1}"
TREED_STATE_DIR="/run/treed-loader"
TREED_STATE_FILE="/run/treed-loader/state.env"
export TREED_MAINTENANCE_MODE
export TREED_STATE_DIR
export TREED_STATE_FILE

# Блок 9: Helper-функция определения текущей ветки репозитория.
resolve_repo_branch() {
  local branch=""

  if command -v git >/dev/null 2>&1 && git -C "${REPO_DIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    branch="$(git -C "${REPO_DIR}" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
  fi

  case "${branch}" in
    ""|HEAD)
      printf '%s\n' ""
      ;;
    *)
      printf '%s\n' "${branch}"
      ;;
  esac
}

# Блок 10: Helper-функции снимка состояния устройства.
# Состояние нужно определить до deploy-mode: auto должен смотреть на runtime,
# а не на имя ветки installer checkout.
unit_exists() {
  systemctl cat "$1" >/dev/null 2>&1
}

unit_active_state() {
  local unit="$1"

  if ! unit_exists "${unit}"; then
    printf '%s\n' "missing"
    return 0
  fi

  systemctl show -p ActiveState --value "${unit}" 2>/dev/null || printf '%s\n' "unknown"
}

detect_device_state() {
  local runtime_config="${PI_HOME}/printer_data/config/printer.cfg"
  local printer_data_dir="${PI_HOME}/printer_data"
  local klipper_dir="${PI_HOME}/klipper"
  local moonraker_dir="${PI_HOME}/moonraker"
  local has_any_runtime=0
  local runtime_incomplete=0

  TREED_HAS_RUNTIME_CONFIG=0
  TREED_HAS_PRINTER_DATA=0
  TREED_HAS_KLIPPER_DIR=0
  TREED_HAS_MOONRAKER_DIR=0
  TREED_HAS_KLIPPER_SERVICE=0
  TREED_HAS_MOONRAKER_SERVICE=0
  TREED_KLIPPER_ACTIVE_STATE="missing"
  TREED_MOONRAKER_ACTIVE_STATE="missing"
  TREED_DEVICE_STATE_REASON=""

  [ -f "${runtime_config}" ] && TREED_HAS_RUNTIME_CONFIG=1
  [ -d "${printer_data_dir}" ] && TREED_HAS_PRINTER_DATA=1
  [ -d "${klipper_dir}" ] && TREED_HAS_KLIPPER_DIR=1
  [ -d "${moonraker_dir}" ] && TREED_HAS_MOONRAKER_DIR=1

  if unit_exists "klipper.service"; then
    TREED_HAS_KLIPPER_SERVICE=1
    TREED_KLIPPER_ACTIVE_STATE="$(unit_active_state "klipper.service")"
  fi
  if unit_exists "moonraker.service"; then
    TREED_HAS_MOONRAKER_SERVICE=1
    TREED_MOONRAKER_ACTIVE_STATE="$(unit_active_state "moonraker.service")"
  fi

  if [ "${TREED_HAS_RUNTIME_CONFIG}" = "1" ] ||
     [ "${TREED_HAS_PRINTER_DATA}" = "1" ] ||
     [ "${TREED_HAS_KLIPPER_DIR}" = "1" ] ||
     [ "${TREED_HAS_MOONRAKER_DIR}" = "1" ] ||
     [ "${TREED_HAS_KLIPPER_SERVICE}" = "1" ] ||
     [ "${TREED_HAS_MOONRAKER_SERVICE}" = "1" ]; then
    has_any_runtime=1
  fi

  if [ "${has_any_runtime}" = "0" ]; then
    TREED_DEVICE_STATE="fresh"
    TREED_DEVICE_STATE_REASON="runtime-empty"
  else
    case "${TREED_KLIPPER_ACTIVE_STATE}:${TREED_MOONRAKER_ACTIVE_STATE}" in
      *failed*|*activating*|*deactivating*)
        runtime_incomplete=1
        TREED_DEVICE_STATE_REASON="service-not-stable"
        ;;
    esac

    if [ "${runtime_incomplete}" = "0" ]; then
      if [ "${TREED_HAS_RUNTIME_CONFIG}" != "1" ] ||
         [ "${TREED_HAS_KLIPPER_SERVICE}" != "1" ] ||
         [ "${TREED_HAS_MOONRAKER_SERVICE}" != "1" ]; then
        runtime_incomplete=1
        TREED_DEVICE_STATE_REASON="runtime-incomplete"
      fi
    fi

    if [ "${runtime_incomplete}" = "1" ]; then
      TREED_DEVICE_STATE="recover"
    else
      TREED_DEVICE_STATE="update"
      TREED_DEVICE_STATE_REASON="runtime-present"
    fi
  fi

  export TREED_DEVICE_STATE
  export TREED_DEVICE_STATE_REASON
  export TREED_HAS_RUNTIME_CONFIG
  export TREED_HAS_PRINTER_DATA
  export TREED_HAS_KLIPPER_DIR
  export TREED_HAS_MOONRAKER_DIR
  export TREED_HAS_KLIPPER_SERVICE
  export TREED_HAS_MOONRAKER_SERVICE
  export TREED_KLIPPER_ACTIVE_STATE
  export TREED_MOONRAKER_ACTIVE_STATE
}

write_device_state_snapshot() {
  ensure_dir "${TREED_STATE_DIR}"
  cat > "${TREED_STATE_FILE}" <<EOF
TREED_DEVICE_STATE=${TREED_DEVICE_STATE}
TREED_DEVICE_STATE_REASON=${TREED_DEVICE_STATE_REASON}
TREED_HAS_RUNTIME_CONFIG=${TREED_HAS_RUNTIME_CONFIG}
TREED_HAS_PRINTER_DATA=${TREED_HAS_PRINTER_DATA}
TREED_HAS_KLIPPER_DIR=${TREED_HAS_KLIPPER_DIR}
TREED_HAS_MOONRAKER_DIR=${TREED_HAS_MOONRAKER_DIR}
TREED_HAS_KLIPPER_SERVICE=${TREED_HAS_KLIPPER_SERVICE}
TREED_HAS_MOONRAKER_SERVICE=${TREED_HAS_MOONRAKER_SERVICE}
TREED_KLIPPER_ACTIVE_STATE=${TREED_KLIPPER_ACTIVE_STATE}
TREED_MOONRAKER_ACTIVE_STATE=${TREED_MOONRAKER_ACTIVE_STATE}
TREED_DEPLOY_MODE=${TREED_DEPLOY_MODE:-auto}
TREED_DEPLOY_MODE_EFFECTIVE=${TREED_DEPLOY_MODE_EFFECTIVE:-}
EOF
  chmod 0644 "${TREED_STATE_FILE}"
}

# Блок 11: Helper-функция вычисления эффективного deploy-режима.
# Правило auto:
# - fresh   -> clean,
# - update  -> preserve,
# - recover -> preserve.
resolve_deploy_mode() {
  local raw_mode="${TREED_DEPLOY_MODE:-auto}"
  local repo_branch=""
  local effective_mode=""

  case "${raw_mode}" in
    auto|clean|preserve)
      ;;
    *)
      log_error "Invalid TREED_DEPLOY_MODE=${raw_mode} (allowed: auto|clean|preserve)"
      exit 1
      ;;
  esac

  repo_branch="$(resolve_repo_branch)"

  if [ "${raw_mode}" = "auto" ]; then
    case "${TREED_DEVICE_STATE:-fresh}" in
      fresh)
        effective_mode="clean"
        ;;
      update|recover)
        effective_mode="preserve"
        ;;
      *)
        log_warn "Unknown TREED_DEVICE_STATE=${TREED_DEVICE_STATE:-}; auto deploy falls back to preserve"
        effective_mode="preserve"
        ;;
    esac
  else
    effective_mode="${raw_mode}"
  fi

  TREED_DEPLOY_MODE="${raw_mode}"
  TREED_DEPLOY_MODE_EFFECTIVE="${effective_mode}"
  TREED_DEPLOY_BRANCH="${repo_branch}"

  export TREED_DEPLOY_MODE
  export TREED_DEPLOY_MODE_EFFECTIVE
  export TREED_DEPLOY_BRANCH
}

# Блок 12: Подключение доп. библиотек, снимок состояния и deploy-режим.
. "${REPO_DIR}/loader/lib/plymouth.sh"
detect_device_state
resolve_deploy_mode
write_device_state_snapshot

# Блок 13: Глобальный trap ошибок.
# Логирует имя шага, код, строку и команду, после чего завершает loader.
trap 'rc=$?; log_error "FAILED step=${CURRENT_STEP:-unknown} rc=${rc} line=${BASH_LINENO[0]} cmd=${BASH_COMMAND}"; exit ${rc}' ERR

# Блок 14: Реестр шагов оркестрации (порядок критичен).
STEPS=(
  # Предварительные проверки и подготовка окружения.
  "check-env"                # Контракт окружения: root, PI_USER/PI_HOME, OS sanity.
  "detect-boot-env"          # Host-aware определение boot backend и boot-файлов.
  "timezone-sync"            # Синхронизация timezone/NTP для корректного времени UI/логов.
  "maintenance-stop"         # Остановка runtime-сервисов перед изменением конфигов.

  # Системная база и boot-контур.
  "packages-core"            # Установка базовых пакетов provisioning-контура.
  "runtime-bootstrap"        # Bootstrap Klipper/Moonraker unit-файлов, venv и runtime-каталогов.
  "can-setup"                # Подготовка и подъем CAN-интерфейса host (U2C -> can0).
  "firmware-build"           # Сборка firmware-артефактов main/EBB/(Eddy) без автопрошивки.
  "boot-hdmi-config"         # Управляемый HDMI-блок + контроль gpu_mem.
  "plymouth-theme-install"   # Установка темы TreeD в системный каталог Plymouth.
  "plymouth-initramfs"       # Пересборка initramfs с активной темой Plymouth.
  "plymouth-initramfs-config" # Привязка initramfs строки в config.txt.
  "plymouth-cmdline"         # Нормализация kernel cmdline (splash/noise reduction).
  "plymouth-systemd"         # Политика getty@tty1 и unit-цепочки Plymouth.

  # Конфигурация Klipper/Moonraker/камера/UI.
  "klipper-sync"             # Репозиторные конфиги -> staging: ~/treed/klipper.
  "klipper-core"             # Раскладка staging -> runtime: ~/printer_data/config.
  "klipper-anti-shutdown"    # Сброс MCU shutdown при обнаружении после раскладки.
  "mainsail-web"             # Развертывание Mainsail web-layer + nginx reverse proxy.
  "moonraker-config"         # Деплой moonraker.conf/base/generated и shell-компонента.
  "crowsnest-webcam"         # Деплой камеры (crowsnest + moonraker webcam fragment).
  "treed-cam"                # Runtime-скрипты TreeD камеры в ~/treed/cam.
  "klipper-mainsail-theme"   # Синхронизация темы Mainsail в runtime-конфиг.
  "klipperscreen-install"    # Установка/health-check KlipperScreen.
  "klipperscreen-theme"      # Деплой темы/шрифта и обновление KlipperScreen.conf.
  "klipperscreen-integr"     # Systemd override KlipperScreen для корректного splash.

  # Финализация и контроль.
  "maintenance-start"        # Запуск required/best-effort сервисов после provisioning.
  "verify"                   # Финальная валидация всего контура (must-pass).
)

# Блок 15: Явный список optional-шагов (не прерывают provisioning при ошибке).
OPTIONAL_STEPS=(
  "crowsnest-webcam"         # Камера может быть недоступна на конкретном хосте.
)

# Блок 16: Helper-проверка принадлежности шага к optional-контуру.
is_optional_step() {
  local step_name="$1"
  local opt=""
  for opt in "${OPTIONAL_STEPS[@]}"; do
    if [ "${opt}" = "${step_name}" ]; then
      return 0
    fi
  done
  return 1
}

# Блок 17: Унифицированный запуск step-скрипта.
# Если у файла нет executable-бита, запускаем через bash явно.
run_step_script() {
  local script_path="$1"
  if [ -x "${script_path}" ]; then
    "${script_path}"
  else
    bash "${script_path}"
  fi
}

# Блок 18: Стартовая диагностика оркестратора.
log_info "TreeD loader starting"
log_info "REPO_DIR=${REPO_DIR}, PI_USER=${PI_USER}, PI_HOME=${PI_HOME}, BOOT_BACKEND=${TREED_BOOT_BACKEND}"
log_info "BOOT_DIR=${BOOT_DIR}, CMDLINE_FILE=${CMDLINE_FILE:-<none>}, CONFIG_FILE=${CONFIG_FILE:-<none>}, ARMBIAN_ENV_FILE=${ARMBIAN_ENV_FILE:-<none>}, EXTLINUX_FILE=${EXTLINUX_FILE:-<none>}"
log_info "TREED_MAINTENANCE_MODE=${TREED_MAINTENANCE_MODE}"
log_info "TREED_DEVICE_STATE=${TREED_DEVICE_STATE} (${TREED_DEVICE_STATE_REASON}), state_file=${TREED_STATE_FILE}"
log_info "TREED_DEPLOY_MODE=${TREED_DEPLOY_MODE}, TREED_DEPLOY_MODE_EFFECTIVE=${TREED_DEPLOY_MODE_EFFECTIVE}, TREED_DEPLOY_BRANCH=${TREED_DEPLOY_BRANCH:-unknown}"

# Блок 19: Основной цикл выполнения шагов по реестру STEPS.
for step in "${STEPS[@]}"; do
  CURRENT_STEP="$step"
  script="${REPO_DIR}/loader/steps/${step}.sh"

  # Валидация существования step-скрипта:
  # - optional: предупреждение + пропуск,
  # - required: немедленная ошибка.
  if [ ! -f "${script}" ]; then
    if is_optional_step "${step}"; then
      log_warn "Optional step script not found: ${script} (skipping)"
      continue
    fi
    log_error "Required step script not found: ${script}"
    exit 1
  fi

  # Контур выполнения:
  # - optional step: ошибка не блокирует общий результат,
  # - required step: ошибка прерывает loader (через trap/exit).
  if is_optional_step "${step}"; then
    log_info "Running optional step: ${step}"
    if run_step_script "${script}"; then
      :
    else
      rc=$?
      log_warn "Optional step failed: ${step} rc=${rc} (continuing)"
    fi
  else
    log_info "Running step: ${step}"
    run_step_script "${script}"
  fi
done

# Блок 20: Успешное завершение полного контура provisioning.
write_device_state_snapshot
log_info "TreeD loader finished successfully"
