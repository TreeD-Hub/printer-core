#!/bin/bash
set -euo pipefail

# ==========================================
# ШАГ LOADER: PLYMOUTH INITRAMFS CONFIG
# ==========================================
# Назначение:
# - Привязывает initramfs к boot backend для текущего ядра.
# - RPi backend: управляет строкой initramfs в `config.txt`.
# - Armbian backend: проверяет контур initrd/boot scripts без правки `config.txt`.
# Контур:
# - required для согласованности boot-конфига и initrd.

# Блок 1: Библиотеки и RPi helper-функции.
. "${REPO_DIR}/loader/lib/common.sh"
. "${REPO_DIR}/loader/lib/boot-env.sh"

# Блок 2: Старт шага и root-права.
log_info "Step plymouth-initramfs-config: wiring initramfs into config.txt"

ensure_root

# Блок 3: Вычисление целевого config.txt и имени initrd текущего ядра.
BOOT_DIR="${BOOT_DIR:-$(detect_boot_dir)}"
CONFIG_FILE="${CONFIG_FILE:-$(detect_config_file "${BOOT_DIR}")}"
TREED_BOOT_BACKEND="${TREED_BOOT_BACKEND:-$(detect_boot_backend "${BOOT_DIR}")}"
ARMBIAN_ENV_FILE="${ARMBIAN_ENV_FILE:-$(detect_armbian_env_file "${BOOT_DIR}")}"

kver="$(uname -r)"
initrd_name="initrd.img-${kver}"
initrd_path="${BOOT_DIR}/${initrd_name}"

# Блок 4: Защита от записи несуществующего initrd.
if [ ! -f "${initrd_path}" ]; then
  log_warn "plymouth-initramfs-config: initramfs file not found: ${initrd_path}"
  log_warn "plymouth-initramfs-config: config.txt not updated"
  exit 0
fi

# Блок 4a: Armbian/Extlinux backend — initrd управляется boot scripts/extlinux.
if [ "${TREED_BOOT_BACKEND}" = "armbian" ] || [ "${TREED_BOOT_BACKEND}" = "extlinux" ]; then
  if [ -n "${ARMBIAN_ENV_FILE}" ] && [ -f "${ARMBIAN_ENV_FILE}" ]; then
    log_info "plymouth-initramfs-config: armbian backend uses ${ARMBIAN_ENV_FILE}"
  else
    log_warn "plymouth-initramfs-config: armbian backend detected, but armbianEnv.txt is missing"
  fi

  if [ -f /boot/extlinux/extlinux.conf ]; then
    if grep -qiE "initrd[[:space:]]+/?.*${initrd_name}" /boot/extlinux/extlinux.conf; then
      log_info "plymouth-initramfs-config: extlinux has initrd entry for ${initrd_name}"
    else
      log_warn "plymouth-initramfs-config: extlinux.conf has no explicit initrd entry for ${initrd_name}"
    fi
  fi

  log_info "plymouth-initramfs-config: OK (armbian backend, no config.txt rewrite)"
  exit 0
fi

if [ -z "${CONFIG_FILE}" ] || [ ! -f "${CONFIG_FILE}" ]; then
  log_error "plymouth-initramfs-config: config.txt not found for rpi backend: ${CONFIG_FILE:-<empty>}"
  exit 1
fi

# Блок 5: Идемпотентная правка config.txt (disable auto_initramfs + единая initramfs-строка).
backup_file_once "${CONFIG_FILE}"

# Убираем общую автоматику MainsailOS по initramfs
# (мы сами управляем initramfs строкой)
sed -i 's/^[[:space:]]*auto_initramfs=.*/# auto_initramfs disabled by TreeD loader/' "${CONFIG_FILE}"

# Удаляем любые старые initramfs-строки, чтобы не плодить их
sed -i '/^[[:space:]]*initramfs[[:space:]]\+/d' "${CONFIG_FILE}"

printf 'initramfs %s followkernel\n' "${initrd_name}" >> "${CONFIG_FILE}"
log_info "plymouth-initramfs-config: set initramfs line in ${CONFIG_FILE} to initramfs ${initrd_name} followkernel"

log_info "plymouth-initramfs-config: OK"
