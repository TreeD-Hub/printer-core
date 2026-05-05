#!/bin/bash
set -euo pipefail

# ==========================================
# ШАГ LOADER: PLYMOUTH CMDLINE
# ==========================================
# Назначение:
# - Нормализует параметры boot cmdline для splash и чистого boot-лога.
# - RPi backend: правит `cmdline.txt`; Armbian backend: правит `extraargs` в `armbianEnv.txt`.
# Контур:
# - required (влияет на boot-поведение и визуальный startup).

# Блок 1: Библиотеки и старт шага.
. "${REPO_DIR}/loader/lib/common.sh"
. "${REPO_DIR}/loader/lib/rpi.sh"

log_info "Step plymouth-cmdline: updating kernel cmdline for plymouth"

# Блок 1a: Определение boot-backend.
BOOT_DIR="${BOOT_DIR:-$(detect_boot_dir)}"
TREED_BOOT_BACKEND="${TREED_BOOT_BACKEND:-$(detect_boot_backend "${BOOT_DIR}")}"
ARMBIAN_ENV_FILE="${ARMBIAN_ENV_FILE:-$(detect_armbian_env_file "${BOOT_DIR}")}"
EXTLINUX_FILE="${EXTLINUX_FILE:-$(detect_extlinux_file "${BOOT_DIR}")}"

# Блок 1b: Целевой набор токенов (общий для RPi/Armbian).
target_tokens=(
  quiet
  splash
  plymouth.ignore-serial-consoles
  vt.global_cursor_default=0
  consoleblank=0
  loglevel=3
  logo.nologo
  vt.handoff=7
  usbcore.autosuspend=-1
)

# Блок 1c: Armbian backend — нормализуем extraargs в armbianEnv.txt.
if [ "${TREED_BOOT_BACKEND}" = "armbian" ]; then
  if [ -z "${ARMBIAN_ENV_FILE}" ] || [ ! -f "${ARMBIAN_ENV_FILE}" ]; then
    log_error "plymouth-cmdline: armbian backend requires armbianEnv.txt"
    exit 1
  fi

  backup_file_once "${ARMBIAN_ENV_FILE}"

  extraargs_raw="$(get_armbian_env_value "${ARMBIAN_ENV_FILE}" "extraargs")"
  extraargs_raw="${extraargs_raw#\"}"
  extraargs_raw="${extraargs_raw%\"}"
  read -r -a tokens <<< "${extraargs_raw}"

  new_tokens=()
  for t in "${tokens[@]}"; do
    case "$t" in
      quiet|splash|plymouth.ignore-serial-consoles|vt.global_cursor_default=*|consoleblank=*|loglevel=*|logo.nologo|plymouth.debug|vt.handoff=*|plymouth.enable=0|usbcore.autosuspend=*)
        ;;
      console=serial0,*|console=ttyAMA0,*|console=ttyS0,*)
        ;;
      *)
        new_tokens+=("$t")
        ;;
    esac
  done

  for t in "${target_tokens[@]}"; do
    new_tokens+=("${t}")
  done

  set_armbian_env_value "${ARMBIAN_ENV_FILE}" "extraargs" "${new_tokens[*]}"
  log_info "plymouth-cmdline: OK (armbian backend, updated extraargs in ${ARMBIAN_ENV_FILE})"
  exit 0
fi

# Блок 1d: Extlinux backend — нормализуем append токены в extlinux.conf.
if [ "${TREED_BOOT_BACKEND}" = "extlinux" ]; then
  if [ -z "${EXTLINUX_FILE}" ] || [ ! -f "${EXTLINUX_FILE}" ]; then
    log_error "plymouth-cmdline: extlinux backend requires extlinux.conf"
    exit 1
  fi

  backup_file_once "${EXTLINUX_FILE}"

  tmp="$(mktemp)"
  awk '
    function normalize_append(args,      i, n, a, t, out) {
      out = ""
      n = split(args, a, /[[:space:]]+/)
      for (i = 1; i <= n; i++) {
        t = a[i]
        if (t == "" ||
            t == "quiet" ||
            t == "splash" ||
            t == "plymouth.ignore-serial-consoles" ||
            t == "logo.nologo" ||
            t == "plymouth.debug" ||
            t == "plymouth.enable=0" ||
            t ~ /^vt.global_cursor_default=/ ||
            t ~ /^consoleblank=/ ||
            t ~ /^loglevel=/ ||
            t ~ /^vt.handoff=/ ||
            t ~ /^usbcore.autosuspend=/ ||
            t ~ /^console=serial0,/ ||
            t ~ /^console=ttyAMA0,/ ||
            t ~ /^console=ttyS0,/ ||
            t ~ /^console=ttyFIQ0,/) {
          continue
        }
        out = (out == "" ? t : out " " t)
      }

      out = (out == "" ? "quiet" : out " quiet")
      out = out " splash"
      out = out " plymouth.ignore-serial-consoles"
      out = out " vt.global_cursor_default=0"
      out = out " consoleblank=0"
      out = out " loglevel=3"
      out = out " logo.nologo"
      out = out " vt.handoff=7"
      out = out " usbcore.autosuspend=-1"
      return out
    }
    {
      if ($0 ~ /^[[:space:]]*append[[:space:]]+/) {
        prefix = $0
        sub(/append[[:space:]]+.*/, "", prefix)
        args = $0
        sub(/^[[:space:]]*append[[:space:]]+/, "", args)
        print prefix "append " normalize_append(args)
        next
      }
      print
    }
  ' "${EXTLINUX_FILE}" > "${tmp}"
  cat "${tmp}" > "${EXTLINUX_FILE}"
  rm -f "${tmp}"

  log_info "plymouth-cmdline: OK (extlinux backend, updated append in ${EXTLINUX_FILE})"
  exit 0
fi

# Блок 2: Определение и валидация пути cmdline.txt.
if [ -z "${CMDLINE_FILE:-}" ]; then
  if [ -f /boot/firmware/cmdline.txt ]; then
    CMDLINE_FILE=/boot/firmware/cmdline.txt
  elif [ -f /boot/cmdline.txt ]; then
    CMDLINE_FILE=/boot/cmdline.txt
  fi
fi

if [ -z "${CMDLINE_FILE:-}" ] || [ ! -f "${CMDLINE_FILE}" ]; then
  log_error "plymouth-cmdline: CMDLINE_FILE missing or invalid: ${CMDLINE_FILE:-<empty>}"
  exit 1
fi

backup_file_once "${CMDLINE_FILE}"

# Блок 3: Чтение текущей cmdline и разбор на токены.
tmp="$(mktemp)"
tr -d '\r\n' < "${CMDLINE_FILE}" > "${tmp}"
current="$(cat "${tmp}")"
rm -f "${tmp}"

if [ -z "${current}" ]; then
  log_error "plymouth-cmdline: cmdline is empty"
  exit 1
fi

read -r -a tokens <<< "${current}"

new_tokens=()
# Блок 4: Фильтрация конфликтных токенов и serial-console.
# Убираем конфликтные/дублирующие токены, включая serial-console.
for t in "${tokens[@]}"; do
  case "$t" in
    quiet|splash|plymouth.ignore-serial-consoles|vt.global_cursor_default=*|consoleblank=*|loglevel=*|logo.nologo|plymouth.debug|vt.handoff=*|plymouth.enable=0|usbcore.autosuspend=*)
      ;;
    console=serial0,*|console=ttyAMA0,*|console=ttyS0,*)
      ;;
    *)
      new_tokens+=("$t")
      ;;
  esac
done

# Блок 5: Добавление целевого набора токенов в фиксированном порядке.
for t in "${target_tokens[@]}"; do
  new_tokens+=("${t}")
done

# Блок 6: Запись итоговой cmdline.
new_line="${new_tokens[*]}"
printf '%s\n' "${new_line}" > "${CMDLINE_FILE}"

log_info "plymouth-cmdline: OK"
