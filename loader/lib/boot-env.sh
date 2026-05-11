#!/bin/bash
set -euo pipefail

# ==========================================
# БИБЛИОТЕКА LOADER: BOOT ENV
# ==========================================
# Назначение:
# - Определяет host boot-backend и boot-пути.
# - Дает helper-функции для безопасной работы с boot-файлами и armbianEnv.txt/extlinux.
# Контур:
# - required library для boot-aware шагов loader.
# - read-only функции детекта не меняют систему; set_* helper меняет только явно переданный файл.

# Блок 1: Подключение общей библиотеки loader.
. "${REPO_DIR}/loader/lib/common.sh"

# Блок 2: Набор стандартных кандидатов boot-раздела.
boot_dir_candidates() {
  printf '%s\n' "/boot/firmware" "/boot"
}

# Блок 3: Определение модели host-платы.
detect_host_model() {
  local model="unknown"

  if [ -r /proc/device-tree/model ]; then
    model=$(tr -d '\0' < /proc/device-tree/model || echo "unknown")
  elif [ -x /usr/bin/raspi-config ]; then
    model="Raspberry Pi (raspi-config present)"
  else
    model="$(uname -m 2>/dev/null || echo "unknown")"
  fi

  echo "${model}"
}

# Блок 4: Проверка факта монтирования каталога.
is_mounted() {
  local dir="$1"
  if [ -r /proc/mounts ]; then
    awk -v d="$dir" '$2==d {found=1} END {exit !found}' /proc/mounts
  else
    return 1
  fi
}

# Блок 5: Поиск актуального boot-каталога с учетом mounted/наличия файлов.
detect_boot_dir() {
  local candidates=()
  local dir
  local candidate=""

  mapfile -t candidates < <(boot_dir_candidates)

  if [ -n "${TREED_BOOT_DIR_OVERRIDE:-}" ] && [ -d "${TREED_BOOT_DIR_OVERRIDE}" ]; then
    echo "${TREED_BOOT_DIR_OVERRIDE}"
    return 0
  fi

  # Приоритет 1: смонтированный каталог, где есть boot-файлы.
  for dir in "${candidates[@]}"; do
    if [ -d "${dir}" ] && is_mounted "${dir}" && {
      [ -f "${dir}/armbianEnv.txt" ] || [ -f "${dir}/config.txt" ] || [ -f "${dir}/cmdline.txt" ];
    }; then
      echo "${dir}"
      return 0
    fi
  done

  # Приоритет 2: каталог с boot-файлами, даже если mount не виден.
  for dir in "${candidates[@]}"; do
    if [ -d "${dir}" ] && {
      [ -f "${dir}/armbianEnv.txt" ] || [ -f "${dir}/config.txt" ] || [ -f "${dir}/cmdline.txt" ];
    }; then
      echo "${dir}"
      return 0
    fi
  done

  # Приоритет 3: первый смонтированный кандидат.
  for dir in "${candidates[@]}"; do
    if [ -d "${dir}" ] && is_mounted "${dir}"; then
      echo "${dir}"
      return 0
    fi
  done

  # Приоритет 4: первый существующий кандидат.
  for dir in "${candidates[@]}"; do
    if [ -d "${dir}" ]; then
      echo "${dir}"
      return 0
    fi
  done

  candidate="$(printf '%s\n' "${candidates[@]}" | head -n 1)"
  if [ -n "${candidate}" ]; then
    echo "${candidate}"
    return 0
  fi

  echo "/boot"
}

# Блок 6: Поиск cmdline.txt с учетом приоритетного boot_dir.
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

# Блок 7: Поиск config.txt с учетом приоритетного boot_dir.
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

# Блок 8: Поиск /boot/armbianEnv.txt с учетом boot_dir.
detect_armbian_env_file() {
  local boot_dir="${1:-}"
  local candidates=()
  local f

  if [ -n "${boot_dir}" ]; then
    candidates+=("${boot_dir}/armbianEnv.txt")
  fi
  candidates+=("/boot/armbianEnv.txt" "/boot/firmware/armbianEnv.txt")

  for f in "${candidates[@]}"; do
    if [ -f "${f}" ]; then
      echo "${f}"
      return 0
    fi
  done

  echo ""
}

# Блок 8a: Поиск extlinux.conf с учетом boot_dir.
detect_extlinux_file() {
  local boot_dir="${1:-}"
  local candidates=()
  local f

  if [ -n "${boot_dir}" ]; then
    candidates+=("${boot_dir}/extlinux/extlinux.conf")
  fi
  candidates+=("/boot/extlinux/extlinux.conf" "/boot/firmware/extlinux/extlinux.conf")

  for f in "${candidates[@]}"; do
    if [ -f "${f}" ]; then
      echo "${f}"
      return 0
    fi
  done

  echo ""
}

# Блок 9: Определение boot-backend.
detect_boot_backend() {
  local boot_dir="${1:-}"
  local cmdline_file=""
  local config_file=""
  local armbian_env=""
  local extlinux_file=""

  if [ -n "${TREED_BOOT_BACKEND:-}" ]; then
    case "${TREED_BOOT_BACKEND}" in
      rpi|armbian|extlinux)
        echo "${TREED_BOOT_BACKEND}"
        return 0
        ;;
    esac
  fi

  if [ -z "${boot_dir}" ]; then
    boot_dir="$(detect_boot_dir)"
  fi

  cmdline_file="$(detect_cmdline_file "${boot_dir}")"
  config_file="$(detect_config_file "${boot_dir}")"
  armbian_env="$(detect_armbian_env_file "${boot_dir}")"

  if [ -n "${cmdline_file}" ] && [ -f "${cmdline_file}" ] \
    && [ -n "${config_file}" ] && [ -f "${config_file}" ]; then
    echo "rpi"
    return 0
  fi

  if [ -n "${armbian_env}" ] && [ -f "${armbian_env}" ]; then
    echo "armbian"
    return 0
  fi

  if [ -f /etc/armbian-release ]; then
    echo "armbian"
    return 0
  fi

  extlinux_file="$(detect_extlinux_file "${boot_dir}")"
  if [ -n "${extlinux_file}" ] && [ -f "${extlinux_file}" ]; then
    echo "extlinux"
    return 0
  fi

  echo "unknown"
}

# Блок 10: Чтение значения ключа key=value из armbianEnv.
get_armbian_env_value() {
  local env_file="$1"
  local key="$2"

  if [ -z "${env_file}" ] || [ ! -f "${env_file}" ] || [ -z "${key}" ]; then
    echo ""
    return 0
  fi

  sed -nE "s|^[[:space:]]*${key}[[:space:]]*=[[:space:]]*(.*)$|\\1|p" "${env_file}" \
    | sed -E 's/[[:space:]]+$//' \
    | tail -n 1
}

# Блок 11: Идемпотентная запись ключа key=value в armbianEnv.
set_armbian_env_value() {
  local env_file="$1"
  local key="$2"
  local value="$3"
  local tmp=""

  if [ -z "${env_file}" ] || [ -z "${key}" ]; then
    return 1
  fi

  if [ ! -f "${env_file}" ]; then
    return 1
  fi

  tmp="$(mktemp)"
  awk -v k="${key}" -v v="${value}" '
    BEGIN { done = 0 }
    {
      if ($0 ~ "^[[:space:]]*" k "[[:space:]]*=") {
        if (!done) {
          print k "=" v
          done = 1
        }
        next
      }
      print
    }
    END {
      if (!done) {
        print k "=" v
      }
    }
  ' "${env_file}" > "${tmp}"

  cat "${tmp}" > "${env_file}"
  rm -f "${tmp}"
}
