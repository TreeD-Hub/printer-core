#!/bin/bash
set -euo pipefail

. "${REPO_DIR}/loader/lib/common.sh"
. "${REPO_DIR}/loader/lib/rpi.sh"

log_info "Step detect-rpi: detecting Raspberry Pi environment"

RPI_MODEL="$(detect_rpi_model)"
BOOT_DIR="$(detect_boot_dir)"
CMDLINE_FILE="$(detect_cmdline_file "${BOOT_DIR}")"
CONFIG_FILE="$(detect_config_file "${BOOT_DIR}")"

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

if [ -n "${BOOT_DIR}" ]; then
  [ -f "${BOOT_DIR}/cmdline.txt" ] && CMDLINE_FILE="${BOOT_DIR}/cmdline.txt"
  [ -f "${BOOT_DIR}/config.txt" ] && CONFIG_FILE="${BOOT_DIR}/config.txt"
fi

export RPI_MODEL
export BOOT_DIR
export CMDLINE_FILE
export CONFIG_FILE

log_info "RPI_MODEL=${RPI_MODEL}"
log_info "BOOT_DIR=${BOOT_DIR}"
log_info "CMDLINE_FILE=${CMDLINE_FILE}"
log_info "CONFIG_FILE=${CONFIG_FILE}"

log_info "detect-rpi: OK"
