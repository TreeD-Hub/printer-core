#!/bin/bash
set -euo pipefail

# ==========================================
# БИБЛИОТЕКА LOADER: RPI
# ==========================================
# Назначение:
# - Определяет модель Raspberry Pi и boot-пути (boot/config/cmdline).
# - Дает helper-функции для безопасного поиска boot-файлов на разных образах.

# Блок 1: Подключение общей библиотеки loader.
. "${REPO_DIR}/loader/lib/common.sh"

# Блок 2: Определение модели Raspberry Pi.
detect_rpi_model() {
  local model="unknown"

  if [ -r /proc/device-tree/model ]; then
    model=$(tr -d '\0' < /proc/device-tree/model || echo "unknown")
  elif [ -x /usr/bin/raspi-config ]; then
    model="Raspberry Pi (raspi-config present)"
  fi

  echo "$model"
}

# Блок 3: Проверка факта монтирования каталога.
is_mounted() {
  local dir="$1"
  if [ -r /proc/mounts ]; then
    awk -v d="$dir" '$2==d {found=1} END {exit !found}' /proc/mounts
  else
    return 1
  fi
}

# Блок 4: Поиск актуального boot-каталога с учетом mounted/наличия файлов.
detect_boot_dir() {
  local candidates=("/boot/firmware" "/boot")
  local dir

  # Приоритет 1: смонтированный каталог, где есть и config.txt, и cmdline.txt.
  for dir in "${candidates[@]}"; do
    if [ -d "${dir}" ] && is_mounted "${dir}" \
      && [ -f "${dir}/config.txt" ] && [ -f "${dir}/cmdline.txt" ]; then
      echo "${dir}"
      return 0
    fi
  done

  # Приоритет 2: смонтированный каталог, где виден хотя бы один boot-файл.
  for dir in "${candidates[@]}"; do
    if [ -d "${dir}" ] && is_mounted "${dir}" \
      && { [ -f "${dir}/config.txt" ] || [ -f "${dir}/cmdline.txt" ]; }; then
      echo "${dir}"
      return 0
    fi
  done

  # Приоритет 3: первый смонтированный кандидат, даже если файлы пока не видны.
  for dir in "${candidates[@]}"; do
    if [ -d "${dir}" ] && is_mounted "${dir}"; then
      echo "${dir}"
      return 0
    fi
  done

  echo "/boot"
}

# Блок 5: Поиск cmdline.txt с учетом приоритетного boot_dir.
detect_cmdline_file() {
  local boot_dir="${1:-}"
  local candidates=()
  local f

  if [ -n "${boot_dir}" ]; then
    candidates+=("${boot_dir}/cmdline.txt")
  fi
  candidates+=("/boot/firmware/cmdline.txt" "/boot/cmdline.txt")

  for f in "${candidates[@]}"; do
    if [ -f "${f}" ]; then
      echo "${f}"
      return 0
    fi
  done

  echo ""
}

# Блок 6: Поиск config.txt с учетом приоритетного boot_dir.
detect_config_file() {
  local boot_dir="${1:-}"
  local candidates=()
  local f

  if [ -n "${boot_dir}" ]; then
    candidates+=("${boot_dir}/config.txt")
  fi
  candidates+=("/boot/firmware/config.txt" "/boot/config.txt")

  for f in "${candidates[@]}"; do
    if [ -f "${f}" ]; then
      echo "${f}"
      return 0
    fi
  done

  echo ""
}
