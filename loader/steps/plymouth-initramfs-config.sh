#!/bin/bash
set -euo pipefail

# ==========================================
# ШАГ LOADER: PLYMOUTH INITRAMFS CONFIG
# ==========================================
# Назначение:
# - Прописывает строку initramfs в config.txt для текущего ядра.
# - Удаляет дубли и поддерживает идемпотентный результат.
# Контур:
# - required для согласованности boot-конфига и initrd.

# Блок 1: Библиотеки и RPi helper-функции.
. "${REPO_DIR}/loader/lib/common.sh"
. "${REPO_DIR}/loader/lib/rpi.sh"

# Блок 2: Старт шага и root-права.
log_info "Step plymouth-initramfs-config: wiring initramfs into config.txt"

ensure_root

# Блок 3: Вычисление целевого config.txt и имени initrd текущего ядра.
BOOT_DIR="${BOOT_DIR:-$(detect_boot_dir)}"
CONFIG_FILE="${CONFIG_FILE:-$(detect_config_file "${BOOT_DIR}")}"

kver="$(uname -r)"
initrd_name="initrd.img-${kver}"
initrd_path="${BOOT_DIR}/${initrd_name}"

# Блок 4: Защита от записи несуществующего initrd.
if [ ! -f "${initrd_path}" ]; then
  log_warn "plymouth-initramfs-config: initramfs file not found: ${initrd_path}"
  log_warn "plymouth-initramfs-config: config.txt not updated"
  exit 0
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
