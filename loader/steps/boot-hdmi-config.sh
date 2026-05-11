#!/bin/bash
set -euo pipefail

# ==========================================
# ШАГ LOADER: BOOT HDMI CONFIG
# ==========================================
# Назначение:
# - Настраивает boot HDMI-параметры backend-aware.
# - RPi backend: сохраняет legacy fixed 960x544 через config.txt.
# - Rock Pi / Armbian / Extlinux backend: по умолчанию включает автодетект EDID
#   за счет удаления принудительных kernel video= токенов.
# - Fixed-режим для Rock Pi остается доступен через TREED_HDMI_MODE=fixed.
# Контур:
# - required (формирует boot-конфиг дисплея и параметры boot UI).

# Блок 1: Библиотеки boot-aware шага.
. "${REPO_DIR}/loader/lib/common.sh"
. "${REPO_DIR}/loader/lib/boot-env.sh"

# Блок 2: Helper-функции нормализации HDMI/kernel video параметров.
remove_extraargs_tokens_by_prefix() {
  local extraargs="${1:-}"
  local prefix="${2:-}"
  local token=""
  local -a tokens=()
  local -a filtered=()

  read -r -a tokens <<< "${extraargs}"
  for token in "${tokens[@]}"; do
    [ -z "${token}" ] && continue
    case "${token}" in
      "${prefix}"*) ;;
      *) filtered+=("${token}") ;;
    esac
  done

  printf '%s\n' "${filtered[*]}"
}

append_extraargs_token() {
  local extraargs="${1:-}"
  local token_to_add="${2:-}"

  if [ -z "${token_to_add}" ]; then
    printf '%s\n' "${extraargs}"
    return 0
  fi

  if [ -z "${extraargs}" ]; then
    printf '%s\n' "${token_to_add}"
  else
    printf '%s\n' "${extraargs} ${token_to_add}"
  fi
}

detect_connected_drm_connector() {
  local status_file=""
  local drm_dir=""
  local drm_name=""
  local connector=""
  local first_connected=""
  local first_hdmi=""

  for status_file in /sys/class/drm/card*-*/status; do
    [ -f "${status_file}" ] || continue
    [ "$(cat "${status_file}" 2>/dev/null || true)" = "connected" ] || continue

    drm_dir="$(dirname "${status_file}")"
    drm_name="$(basename "${drm_dir}")"
    connector="$(printf '%s\n' "${drm_name}" | sed -E 's/^card[0-9]+-//')"

    [ -z "${first_connected}" ] && first_connected="${connector}"
    case "${connector}" in
      HDMI-*|HDMI-A-*|HDMI-B-*)
        first_hdmi="${connector}"
        break
        ;;
    esac
  done

  if [ -n "${first_hdmi}" ]; then
    printf '%s\n' "${first_hdmi}"
  else
    printf '%s\n' "${first_connected}"
  fi
}

build_kernel_video_token() {
  local connector="${TREED_HDMI_CONNECTOR:-auto}"
  local fallback_connector="${TREED_HDMI_CONNECTOR_FALLBACK:-HDMI-A-1}"
  local resolution="${TREED_HDMI_RESOLUTION:-960x544}"
  local refresh="${TREED_HDMI_REFRESH:-60}"
  local full_mode="${TREED_HDMI_VIDEO_MODE:-}"
  local rotate="${TREED_HDMI_ROTATE:-}"
  local mode=""

  if [ "${connector}" = "auto" ]; then
    connector="$(detect_connected_drm_connector || true)"
    if [ -z "${connector}" ]; then
      connector="${fallback_connector}"
      log_warn "boot-hdmi-config: no connected DRM connector found, fallback connector=${connector}"
    else
      log_info "boot-hdmi-config: detected connected DRM connector=${connector}"
    fi
  fi

  if [ -n "${full_mode}" ]; then
    case "${full_mode}" in
      *:*) mode="${full_mode}" ;;
      *) mode="${connector}:${full_mode}" ;;
    esac
  else
    mode="${connector}:${resolution}"
    if [ -n "${refresh}" ]; then
      mode="${mode}@${refresh}"
    fi
  fi

  if [ -n "${rotate}" ]; then
    mode="${mode},rotate=${rotate}"
  fi

  printf 'video=%s\n' "${mode}"
}

normalize_hdmi_mode() {
  local mode="${1:-auto}"

  case "${mode}" in
    auto|fixed|off)
      printf '%s\n' "${mode}"
      ;;
    *)
      log_error "boot-hdmi-config: TREED_HDMI_MODE must be auto, fixed or off; got ${mode}"
      exit 1
      ;;
  esac
}

# Блок 3: Старт шага и расчет host boot-контекста.
log_info "Step boot-hdmi-config: configuring HDMI output backend-aware"

BOOT_DIR="$(detect_boot_dir)"
CONFIG_FILE="$(detect_config_file "${BOOT_DIR}")"
TREED_BOOT_BACKEND="${TREED_BOOT_BACKEND:-$(detect_boot_backend "${BOOT_DIR}")}"
ARMBIAN_ENV_FILE="${ARMBIAN_ENV_FILE:-$(detect_armbian_env_file "${BOOT_DIR}")}"
EXTLINUX_FILE="${EXTLINUX_FILE:-$(detect_extlinux_file "${BOOT_DIR}")}"

ensure_root

# Rock Pi / Armbian policy:
# - auto  (default): remove forced video= and let DRM/EDID select resolution.
# - fixed: add video=<connector>:<resolution>@<refresh>; connector can be auto-detected.
# - off:   do not touch existing video= tokens.
TREED_HDMI_MODE="$(normalize_hdmi_mode "${TREED_HDMI_MODE:-auto}")"
TREED_HDMI_CONNECTOR="${TREED_HDMI_CONNECTOR:-auto}"
TREED_HDMI_RESOLUTION="${TREED_HDMI_RESOLUTION:-960x544}"
TREED_HDMI_REFRESH="${TREED_HDMI_REFRESH:-60}"
TREED_HDMI_CONNECTOR_FALLBACK="${TREED_HDMI_CONNECTOR_FALLBACK:-HDMI-A-1}"

# Backward-compatible full-mode override for old env name.
if [ -z "${TREED_HDMI_VIDEO_MODE:-}" ] && [ -n "${TREED_ARMBIAN_VIDEO_MODE:-}" ]; then
  TREED_HDMI_VIDEO_MODE="${TREED_ARMBIAN_VIDEO_MODE}"
fi

if [ "${TREED_BOOT_BACKEND}" = "armbian" ]; then
  TREED_ARMBIAN_VERBOSITY="${TREED_ARMBIAN_VERBOSITY:-1}"
  TREED_ARMBIAN_BOOTLOGO="${TREED_ARMBIAN_BOOTLOGO:-true}"
  TREED_ARMBIAN_CONSOLE="${TREED_ARMBIAN_CONSOLE:-both}"

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

  case "${TREED_HDMI_MODE}" in
    auto)
      armbian_extraargs_new="$(remove_extraargs_tokens_by_prefix "${armbian_extraargs_raw}" "video=")"
      set_armbian_env_value "${ARMBIAN_ENV_FILE}" "extraargs" "${armbian_extraargs_new}"
      log_info "boot-hdmi-config: armbian HDMI mode=auto, removed forced video= tokens for EDID/kernel autodetect"
      ;;
    fixed)
      video_token="$(build_kernel_video_token)"
      armbian_extraargs_new="$(remove_extraargs_tokens_by_prefix "${armbian_extraargs_raw}" "video=")"
      armbian_extraargs_new="$(append_extraargs_token "${armbian_extraargs_new}" "${video_token}")"
      set_armbian_env_value "${ARMBIAN_ENV_FILE}" "extraargs" "${armbian_extraargs_new}"
      log_info "boot-hdmi-config: armbian HDMI mode=fixed, extraargs includes ${video_token}"
      ;;
    off)
      log_info "boot-hdmi-config: armbian HDMI mode=off, existing video= tokens preserved"
      ;;
  esac

  log_info "boot-hdmi-config: armbian backend updated ${ARMBIAN_ENV_FILE}"
  log_info "boot-hdmi-config: verbosity=${TREED_ARMBIAN_VERBOSITY}, bootlogo=${TREED_ARMBIAN_BOOTLOGO}, console=${TREED_ARMBIAN_CONSOLE}"
  log_info "boot-hdmi-config: OK"
  exit 0
fi

if [ "${TREED_BOOT_BACKEND}" = "extlinux" ]; then
  if [ -z "${EXTLINUX_FILE}" ] || [ ! -f "${EXTLINUX_FILE}" ]; then
    log_error "boot-hdmi-config: extlinux backend requires extlinux.conf"
    exit 1
  fi

  case "${TREED_HDMI_MODE}" in
    auto) video_token="" ;;
    fixed) video_token="$(build_kernel_video_token)" ;;
    off) video_token="__TREED_PRESERVE_VIDEO__" ;;
  esac

  backup_file_once "${EXTLINUX_FILE}"
  tmp="$(mktemp)"
  awk -v mode="${TREED_HDMI_MODE}" -v video_token="${video_token}" '
    function rewrite_append(args,      i, n, a, t, out) {
      out = ""
      n = split(args, a, /[[:space:]]+/)
      for (i = 1; i <= n; i++) {
        t = a[i]
        if (t == "") {
          continue
        }
        if (mode != "off" && t ~ /^video=/) {
          continue
        }
        out = (out == "" ? t : out " " t)
      }
      if (mode == "fixed" && video_token != "") {
        out = (out == "" ? video_token : out " " video_token)
      }
      return out
    }
    {
      if ($0 ~ /^[[:space:]]*append[[:space:]]+/) {
        prefix = $0
        sub(/append[[:space:]]+.*/, "", prefix)
        args = $0
        sub(/^[[:space:]]*append[[:space:]]+/, "", args)
        print prefix "append " rewrite_append(args)
        next
      }
      print
    }
  ' "${EXTLINUX_FILE}" > "${tmp}"
  cat "${tmp}" > "${EXTLINUX_FILE}"
  rm -f "${tmp}"

  log_info "boot-hdmi-config: extlinux backend updated ${EXTLINUX_FILE}"
  case "${TREED_HDMI_MODE}" in
    auto) log_info "boot-hdmi-config: extlinux HDMI mode=auto, removed forced video= tokens for EDID/kernel autodetect" ;;
    fixed) log_info "boot-hdmi-config: extlinux HDMI mode=fixed, append includes ${video_token}" ;;
    off) log_info "boot-hdmi-config: extlinux HDMI mode=off, existing video= tokens preserved" ;;
  esac
  log_info "boot-hdmi-config: OK"
  exit 0
fi

# RPi legacy backend: сохраняем старое поведение под штатный 960x544 экран.
if [ -z "${CONFIG_FILE}" ] || [ ! -f "${CONFIG_FILE}" ]; then
  log_error "boot-hdmi-config: config.txt not found: ${CONFIG_FILE:-<empty>}"
  exit 1
fi

backup_file_once "${CONFIG_FILE}"

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
  last_gpu_value="$(printf '%s\n' "${last_gpu_line}" | sed -nE 's|^[[:space:]]*gpu_mem[[:space:]]*=[[:space:]]*([0-9]+).*|\1|p')"
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
