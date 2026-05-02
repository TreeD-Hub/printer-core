#!/bin/bash
set -euo pipefail

# ==========================================
# ШАГ LOADER: BOOT HDMI CONFIG
# ==========================================
# Назначение:
# - Настраивает boot HDMI-параметры backend-aware.
# - RPi backend: правит HDMI-блок и `gpu_mem` в `config.txt`.
# - Armbian backend: правит `verbosity/bootlogo/console/extraargs` в `armbianEnv.txt`.
# Контур:
# - required (формирует boot-конфиг дисплея и проверяемый gpu_mem).

# Блок 1: Библиотеки и функции определения boot-путей.
. "${REPO_DIR}/loader/lib/common.sh"
. "${REPO_DIR}/loader/lib/rpi.sh"

# Блок 1a: Helper-нормализация токена в extraargs (armbian backend).
upsert_extraargs_token() {
  local extraargs="${1:-}"
  local token_to_set="${2:-}"
  local token_key=""
  local token=""
  local -a tokens=()
  local -a filtered=()
  local result=""

  token_key="${token_to_set%%=*}"
  read -r -a tokens <<< "${extraargs}"
  for token in "${tokens[@]}"; do
    [ -z "${token}" ] && continue
    case "${token}" in
      "${token_key}"=*)
        ;;
      *)
        filtered+=("${token}")
        ;;
    esac
  done
  filtered+=("${token_to_set}")
  result="${filtered[*]}"
  printf '%s\n' "${result}"
}

# Блок 2: Старт шага и определение config.txt.
log_info "Step boot-hdmi-config: configuring HDMI output for 960x544 display"

BOOT_DIR="$(detect_boot_dir)"
CONFIG_FILE="$(detect_config_file "${BOOT_DIR}")"
TREED_BOOT_BACKEND="${TREED_BOOT_BACKEND:-$(detect_boot_backend "${BOOT_DIR}")}"
ARMBIAN_ENV_FILE="${ARMBIAN_ENV_FILE:-$(detect_armbian_env_file "${BOOT_DIR}")}"

ensure_root

# Блок 2a: Armbian backend (без config.txt/gpu_mem).
if [ "${TREED_BOOT_BACKEND}" = "armbian" ]; then
  TREED_ARMBIAN_VERBOSITY="${TREED_ARMBIAN_VERBOSITY:-1}"
  TREED_ARMBIAN_BOOTLOGO="${TREED_ARMBIAN_BOOTLOGO:-true}"
  TREED_ARMBIAN_CONSOLE="${TREED_ARMBIAN_CONSOLE:-both}"
  TREED_ARMBIAN_VIDEO_MODE="${TREED_ARMBIAN_VIDEO_MODE:-HDMI-A-1:960x544@60}"

  if [ -z "${ARMBIAN_ENV_FILE}" ] || [ ! -f "${ARMBIAN_ENV_FILE}" ]; then
    log_error "boot-hdmi-config: armbianEnv.txt not found for armbian backend"
    exit 1
  fi

  case "${TREED_ARMBIAN_VERBOSITY}" in
    ''|*[!0-9]*)
      log_error "boot-hdmi-config: TREED_ARMBIAN_VERBOSITY must be numeric, got ${TREED_ARMBIAN_VERBOSITY}"
      exit 1
      ;;
  esac

  backup_file_once "${ARMBIAN_ENV_FILE}"

  set_armbian_env_value "${ARMBIAN_ENV_FILE}" "verbosity" "${TREED_ARMBIAN_VERBOSITY}"
  set_armbian_env_value "${ARMBIAN_ENV_FILE}" "bootlogo" "${TREED_ARMBIAN_BOOTLOGO}"
  set_armbian_env_value "${ARMBIAN_ENV_FILE}" "console" "${TREED_ARMBIAN_CONSOLE}"

  armbian_extraargs_raw="$(get_armbian_env_value "${ARMBIAN_ENV_FILE}" "extraargs")"
  armbian_extraargs_raw="${armbian_extraargs_raw#\"}"
  armbian_extraargs_raw="${armbian_extraargs_raw%\"}"
  armbian_extraargs_new="$(upsert_extraargs_token "${armbian_extraargs_raw}" "video=${TREED_ARMBIAN_VIDEO_MODE}")"
  set_armbian_env_value "${ARMBIAN_ENV_FILE}" "extraargs" "${armbian_extraargs_new}"

  log_info "boot-hdmi-config: armbian backend updated ${ARMBIAN_ENV_FILE}"
  log_info "boot-hdmi-config: verbosity=${TREED_ARMBIAN_VERBOSITY}, bootlogo=${TREED_ARMBIAN_BOOTLOGO}, console=${TREED_ARMBIAN_CONSOLE}"
  log_info "boot-hdmi-config: extraargs includes video=${TREED_ARMBIAN_VIDEO_MODE}"
  log_info "boot-hdmi-config: OK"
  exit 0
fi

if [ -z "${CONFIG_FILE}" ] || [ ! -f "${CONFIG_FILE}" ]; then
  log_error "boot-hdmi-config: config.txt not found: ${CONFIG_FILE:-<empty>}"
  exit 1
fi

# Блок 3: Нормализация gpu_mem и удаление дублей.
backup_file_once "${CONFIG_FILE}"

# Держим gpu_mem не ниже порога для стабильного UI и ожидаемого результата verify.
GPU_MEM_MIN=96
gpu_count="$(grep -cE '^[[:space:]]*gpu_mem[[:space:]]*=' "${CONFIG_FILE}" 2>/dev/null || true)"
last_gpu_info="$(grep -nE '^[[:space:]]*gpu_mem[[:space:]]*=' "${CONFIG_FILE}" 2>/dev/null | tail -n 1 || true)"

if [ "${gpu_count}" -eq 0 ]; then
  if [ -n "$(tail -c 1 "${CONFIG_FILE}" 2>/dev/null)" ]; then
    printf '\n' >> "${CONFIG_FILE}"
  fi
  printf 'gpu_mem=%s\n' "${GPU_MEM_MIN}" >> "${CONFIG_FILE}"
  log_info "Set gpu_mem=${GPU_MEM_MIN} in ${CONFIG_FILE}"
else
  last_gpu_lineno="${last_gpu_info%%:*}"
  last_gpu_line="${last_gpu_info#*:}"
  last_gpu_value="$(printf '%s\n' "${last_gpu_line}" | sed -nE 's|^[[:space:]]*gpu_mem[[:space:]]*=[[:space:]]*([0-9]+).*|\\1|p')"
  case "${last_gpu_value}" in ''|*[!0-9]*) last_gpu_value=0;; esac

  if [ "${last_gpu_value}" -lt "${GPU_MEM_MIN}" ]; then
    sed -i -E "${last_gpu_lineno}s|^([[:space:]]*gpu_mem[[:space:]]*=[[:space:]]*)[^#[:space:]]*(.*)$|\\1${GPU_MEM_MIN}\\2|" "${CONFIG_FILE}"
    log_info "Updated gpu_mem to ${GPU_MEM_MIN} in ${CONFIG_FILE}"
  else
    log_info "gpu_mem already >= ${GPU_MEM_MIN} in ${CONFIG_FILE} (gpu_mem=${last_gpu_value})"
  fi

  if [ "${gpu_count}" -gt 1 ]; then
    tmp="$(mktemp)"
    awk -v keep="${last_gpu_lineno}" '
      NR==keep {print; next}
      $0 ~ /^[[:space:]]*gpu_mem[[:space:]]*=/ {next}
      {print}
    ' "${CONFIG_FILE}" > "${tmp}"
    cat "${tmp}" > "${CONFIG_FILE}"
    rm -f "${tmp}"
    log_info "Removed duplicate gpu_mem entries in ${CONFIG_FILE}"
  fi
fi

BEGIN_TREED_HDMI="# BEGIN TreeD HDMI"
END_TREED_HDMI="# END TreeD HDMI"

# Блок 4: Поиск потенциальных конфликтов HDMI/dtparam вне managed-блока.
# Ищем потенциальные конфликты вне managed-блока TreeD (только предупреждения).
conflicts="$(awk -v b="${BEGIN_TREED_HDMI}" -v e="${END_TREED_HDMI}" '
  BEGIN { inblk=0 }
  $0==b { inblk=1; next }
  $0==e { inblk=0; next }
  inblk { next }
  $0 ~ /^[[:space:]]*#/ { next }
  {
    line=$0
    sub(/^[[:space:]]+/, "", line)
    sub(/[[:space:]]+$/, "", line)

    if (line ~ /^hdmi_/) {
      if (match(line, /^hdmi_group[[:space:]]*=[[:space:]]*([0-9]+)/, m)) {
        if (m[1] != 2) print $0
      } else if (match(line, /^hdmi_mode[[:space:]]*=[[:space:]]*([0-9]+)/, m)) {
        if (m[1] != 87) print $0
      } else if (match(line, /^hdmi_drive[[:space:]]*=[[:space:]]*([0-9]+)/, m)) {
        if (m[1] != 2) print $0
      } else if (line ~ /^hdmi_cvt[[:space:]]*=/) {
        v=line
        sub(/^hdmi_cvt[[:space:]]*=[[:space:]]*/, "", v)
        sub(/[[:space:]]*#.*/, "", v)
        gsub(/[[:space:]]+/, " ", v)
        sub(/^[[:space:]]+/, "", v)
        sub(/[[:space:]]+$/, "", v)
        if (v != "960 544 60 6 0 0 0") print $0
      } else {
        print $0
      }
    } else if (line ~ /^dtparam[[:space:]]*=/) {
      v=line
      sub(/^dtparam[[:space:]]*=[[:space:]]*/, "", v)
      sub(/[[:space:]]*#.*/, "", v)
      sub(/[[:space:]]+$/, "", v)

      if (v ~ /(^|,)i2c_arm=/ && v !~ /(^|,)i2c_arm=on(,|$)/) print $0
      if (v ~ /(^|,)spi=/ && v !~ /(^|,)spi=on(,|$)/) print $0
    }
  }
' "${CONFIG_FILE}" 2>/dev/null || true)"

if [ -n "${conflicts}" ]; then
  log_warn "boot-hdmi-config: potential conflicting HDMI/dtparam lines found outside managed TreeD HDMI block:"
  while IFS= read -r l; do
    [ -n "${l}" ] && log_warn "${l}"
  done <<< "${conflicts}"
fi

# Блок 5: Проверка целостности парных маркеров managed-блока.
# Проверяем парность маркеров, чтобы не повредить config.txt при битом блоке.
if grep -qF "${BEGIN_TREED_HDMI}" "${CONFIG_FILE}" 2>/dev/null || grep -qF "${END_TREED_HDMI}" "${CONFIG_FILE}" 2>/dev/null; then
  if ! awk -v b="${BEGIN_TREED_HDMI}" -v e="${END_TREED_HDMI}" '
    BEGIN { inblk=0; ok=1 }
    $0==b { if (inblk) ok=0; inblk=1 }
    $0==e { if (!inblk) ok=0; inblk=0 }
    END { if (inblk) ok=0; exit ok?0:1 }
  ' "${CONFIG_FILE}" 2>/dev/null; then
    log_error "boot-hdmi-config: managed HDMI block markers are inconsistent in ${CONFIG_FILE}"
    exit 1
  fi
fi

# Блок 6: Обновление или создание managed TreeD HDMI-блока.
if grep -qF "${BEGIN_TREED_HDMI}" "${CONFIG_FILE}" 2>/dev/null; then
  tmp="$(mktemp)"
  awk -v b="${BEGIN_TREED_HDMI}" -v e="${END_TREED_HDMI}" '
    BEGIN { inblk=0; replaced=0 }
    $0==b {
      inblk=1
      if (replaced==0) {
        print b
        print "hdmi_group=2"
        print "hdmi_mode=87"
        print "hdmi_cvt=960 544 60 6 0 0 0"
        print "hdmi_drive=2"
        print "disable_overscan=1"
        print "disable_splash=1"
        print "dtparam=i2c_arm=on"
        print "dtparam=spi=on"
        print e
        replaced=1
      }
      next
    }
    $0==e { if (inblk) { inblk=0; next } }
    !inblk { print }
  ' "${CONFIG_FILE}" > "${tmp}"
  cat "${tmp}" > "${CONFIG_FILE}"
  rm -f "${tmp}"
  log_info "Updated managed TreeD HDMI block in ${CONFIG_FILE}"
else
  if [ -n "$(tail -c 1 "${CONFIG_FILE}" 2>/dev/null)" ]; then
    printf '\n' >> "${CONFIG_FILE}"
  fi
  cat >>"${CONFIG_FILE}" <<EOC
# BEGIN TreeD HDMI
hdmi_group=2
hdmi_mode=87
hdmi_cvt=960 544 60 6 0 0 0
hdmi_drive=2
disable_overscan=1
disable_splash=1
dtparam=i2c_arm=on
dtparam=spi=on
# END TreeD HDMI
EOC
  log_info "Appended managed TreeD HDMI block to ${CONFIG_FILE}"
fi

log_info "boot-hdmi-config: OK"
