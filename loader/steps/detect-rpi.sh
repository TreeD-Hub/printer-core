#!/bin/bash
set -euo pipefail

# ==========================================
# ШАГ LOADER: DETECT RPI
# ==========================================
# Назначение:
# - Определяет host boot-backend и boot-пути конфигурации.
# - Логирует согласованный контур RPi/Armbian для последующих шагов.
# Контур:
# - required (используется последующими шагами boot/plymouth).

# Блок 1: Библиотеки и функции определения платформы.
. "${REPO_DIR}/loader/lib/common.sh"
. "${REPO_DIR}/loader/lib/rpi.sh"

# Блок 2: Первичное определение модели host и boot-файлов.
log_info "Step detect-rpi: detecting host boot environment"

RPI_MODEL="$(detect_host_model)"
BOOT_DIR="$(detect_boot_dir)"
TREED_BOOT_BACKEND="${TREED_BOOT_BACKEND:-$(detect_boot_backend "${BOOT_DIR}")}"
CMDLINE_FILE="$(detect_cmdline_file "${BOOT_DIR}")"
CONFIG_FILE="$(detect_config_file "${BOOT_DIR}")"
ARMBIAN_ENV_FILE="$(detect_armbian_env_file "${BOOT_DIR}")"
EXTLINUX_FILE="$(detect_extlinux_file "${BOOT_DIR}")"

# Блок 3: Нормализация BOOT_DIR по реальным путям config/cmdline.
# Синхронизируем BOOT_DIR с фактическим расположением config/cmdline, если это возможно.
if [ -n "${CMDLINE_FILE}" ] && [ -n "${CONFIG_FILE}" ]; then
  cmd_dir="$(dirname "${CMDLINE_FILE}")"
  cfg_dir="$(dirname "${CONFIG_FILE}")"
  if [ "${cmd_dir}" = "${cfg_dir}" ]; then
    BOOT_DIR="${cfg_dir}"
  else
    # Опорным каталогом считаем путь от CONFIG_FILE (там управляется initramfs/config).
    BOOT_DIR="${cfg_dir}"
  fi
elif [ -n "${CONFIG_FILE}" ]; then
  BOOT_DIR="$(dirname "${CONFIG_FILE}")"
elif [ -n "${CMDLINE_FILE}" ]; then
  BOOT_DIR="$(dirname "${CMDLINE_FILE}")"
fi

# Блок 4: Уточнение ссылок на cmdline.txt/config.txt/armbianEnv.txt из итогового BOOT_DIR.
if [ -n "${BOOT_DIR}" ]; then
  [ -f "${BOOT_DIR}/cmdline.txt" ] && CMDLINE_FILE="${BOOT_DIR}/cmdline.txt"
  [ -f "${BOOT_DIR}/config.txt" ] && CONFIG_FILE="${BOOT_DIR}/config.txt"
  [ -f "${BOOT_DIR}/armbianEnv.txt" ] && ARMBIAN_ENV_FILE="${BOOT_DIR}/armbianEnv.txt"
fi

# Блок 5: Экспорт переменных (только в пределах шага) и логирование контракта.
export RPI_MODEL
export BOOT_DIR
export TREED_BOOT_BACKEND
export CMDLINE_FILE
export CONFIG_FILE
export ARMBIAN_ENV_FILE
export EXTLINUX_FILE

log_info "HOST_MODEL=${RPI_MODEL}"
log_info "TREED_BOOT_BACKEND=${TREED_BOOT_BACKEND}"
log_info "BOOT_DIR=${BOOT_DIR:-<empty>}"

case "${TREED_BOOT_BACKEND}" in
  rpi)
    if [ -n "${CMDLINE_FILE}" ] && [ -f "${CMDLINE_FILE}" ]; then
      log_info "CMDLINE_FILE=${CMDLINE_FILE}"
    else
      log_error "detect-rpi: rpi backend requires cmdline.txt"
      exit 1
    fi
    if [ -n "${CONFIG_FILE}" ] && [ -f "${CONFIG_FILE}" ]; then
      log_info "CONFIG_FILE=${CONFIG_FILE}"
    else
      log_error "detect-rpi: rpi backend requires config.txt"
      exit 1
    fi
    ;;
  armbian)
    if [ -n "${ARMBIAN_ENV_FILE}" ] && [ -f "${ARMBIAN_ENV_FILE}" ]; then
      log_info "ARMBIAN_ENV_FILE=${ARMBIAN_ENV_FILE}"
    else
      log_error "detect-rpi: armbian backend requires /boot/armbianEnv.txt"
      exit 1
    fi
    log_info "CMDLINE_FILE=${CMDLINE_FILE:-<not used by armbian backend>}"
    log_info "CONFIG_FILE=${CONFIG_FILE:-<not used by armbian backend>}"
    ;;
  extlinux)
    if [ -n "${EXTLINUX_FILE}" ] && [ -f "${EXTLINUX_FILE}" ]; then
      log_info "EXTLINUX_FILE=${EXTLINUX_FILE}"
    else
      log_error "detect-rpi: extlinux backend requires /boot/extlinux/extlinux.conf"
      exit 1
    fi
    log_info "CMDLINE_FILE=${CMDLINE_FILE:-<not used by extlinux backend>}"
    log_info "CONFIG_FILE=${CONFIG_FILE:-<not used by extlinux backend>}"
    log_info "ARMBIAN_ENV_FILE=${ARMBIAN_ENV_FILE:-<not used by extlinux backend>}"
    ;;
  *)
    log_error "detect-rpi: unsupported TREED_BOOT_BACKEND=${TREED_BOOT_BACKEND}"
    exit 1
    ;;
esac

log_info "detect-rpi: OK"
