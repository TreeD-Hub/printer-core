#!/bin/bash
set -euo pipefail

# ==========================================
# ШАГ LOADER: KLIPPER ADXL
# ==========================================
# Назначение:
# - Валидирует mandatory-контур ADXL/Input Shaper для профиля RN12.
# - Поддерживает 2 режима:
#   - rpi: ADXL345 через Raspberry Pi SPI + host MCU (`klipper-mcu.service`);
#   - ebb: onboard ADXL345 на EBB42 (без host MCU и без SPI на Pi).
# - Удаляет legacy marker-блок ADXL из `local_overrides.cfg`, если он остался от старой схемы.
# Контур:
# - required (fail-fast): ADXL и Input Shaper являются базовым контуром.

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
. "${REPO_DIR}/loader/lib/common.sh"
. "${REPO_DIR}/loader/lib/rpi.sh"

# Блок 1: Базовая инициализация шага и helper-предикаты.
ensure_root
log_info "Step klipper-adxl-rpi: validate mandatory ADXL345/Input Shaper contour"

is_true_local() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

# Блок 2: Параметры управления шагом (mandatory ADXL/Input Shaper).
TREED_ADXL_MODE="${TREED_ADXL_MODE:-auto}" # auto|rpi|ebb
TREED_ADXL_RPI_ENABLE="${TREED_ADXL_RPI_ENABLE:-1}" # legacy флаг, актуален только для rpi-режима
TREED_ADXL_RPI_SPI_BUS="${TREED_ADXL_RPI_SPI_BUS:-spidev0.0}"
TREED_ADXL_RPI_ENABLE_INPUT_SHAPER="${TREED_ADXL_RPI_ENABLE_INPUT_SHAPER:-1}"
TREED_ADXL_RPI_REBUILD_HOST_MCU="${TREED_ADXL_RPI_REBUILD_HOST_MCU:-0}"

if ! is_true_local "${TREED_ADXL_RPI_ENABLE_INPUT_SHAPER}"; then
  log_error "klipper-adxl-rpi: TREED_ADXL_RPI_ENABLE_INPUT_SHAPER=0 is not allowed (Input Shaper is mandatory)"
  exit 1
fi

PI_USER="${PI_USER:-pi}"
PI_HOME="${PI_HOME:-/home/${PI_USER}}"
if ! grp="$(pi_primary_group "${PI_USER}")"; then
  exit 1
fi

CONFIG_DIR="${PI_HOME}/printer_data/config"
PRINTER_CFG="${CONFIG_DIR}/printer.cfg"
LOCAL_OVERRIDES_CFG="${CONFIG_DIR}/local_overrides.cfg"
PROFILE_DIR="${CONFIG_DIR}/profiles/rn12_corexy_v1"
ADXL_SHIM_CFG="${PROFILE_DIR}/adxl345_rpi.cfg"
ADXL_RPI_CFG="${PROFILE_DIR}/legacy/adxl345_rpi.cfg"
EBB_CFG="${PROFILE_DIR}/ebb42_v1_2_usb.cfg"
INPUT_SHAPER_CFG="${PROFILE_DIR}/input_shaper.cfg"
KLIPPER_DIR="${PI_HOME}/klipper"
KLIPPER_HOST_MCU_BIN="/usr/local/bin/klipper_mcu"
KLIPPER_HOST_MCU_UNIT_SRC="${KLIPPER_DIR}/scripts/klipper-mcu.service"
KLIPPER_HOST_MCU_UNIT_DST="/etc/systemd/system/klipper-mcu.service"
KLIPPER_HOST_MCU_OUTDIR="out-treed-klipper-mcu-linux"
KLIPPER_HOST_MCU_KCONFIG="${KLIPPER_DIR}/${KLIPPER_HOST_MCU_OUTDIR}/linuxprocess.config"
SPI_DEV="/dev/${TREED_ADXL_RPI_SPI_BUS}"
SPI_CONFIG_FILE="${CONFIG_FILE:-}"
if [ -z "${SPI_CONFIG_FILE}" ] || [ ! -f "${SPI_CONFIG_FILE}" ]; then
  boot_dir="$(detect_boot_dir)"
  SPI_CONFIG_FILE="$(detect_config_file "${boot_dir}" 2>/dev/null || true)"
fi

# Блок 3: Проверка runtime-конфига профиля и выбор ADXL-режима (auto/rpi/ebb).
if [ ! -f "${PRINTER_CFG}" ]; then
  log_error "klipper-adxl-rpi: runtime printer.cfg not found: ${PRINTER_CFG}"
  exit 1
fi
if [ ! -d "${PROFILE_DIR}" ]; then
  log_error "klipper-adxl-rpi: profile dir not found: ${PROFILE_DIR}"
  exit 1
fi
if [ ! -f "${INPUT_SHAPER_CFG}" ]; then
  log_error "klipper-adxl-rpi: missing runtime Input Shaper config: ${INPUT_SHAPER_CFG}"
  exit 1
fi
if ! grep -qE '^[[:space:]]*\[include[[:space:]]+profiles/rn12_corexy_v1/input_shaper\.cfg\][[:space:]]*$' "${PRINTER_CFG}"; then
  log_error "klipper-adxl-rpi: printer.cfg must include profiles/rn12_corexy_v1/input_shaper.cfg"
  exit 1
fi

has_rpi_include=0
if grep -qE '^[[:space:]]*\[include[[:space:]]+profiles/rn12_corexy_v1/adxl345_rpi\.cfg\][[:space:]]*$' "${PRINTER_CFG}"; then
  has_rpi_include=1
fi

has_ebb_adxl=0
if [ -f "${EBB_CFG}" ] \
  && grep -qE '^[[:space:]]*\[adxl345\][[:space:]]*$' "${EBB_CFG}" \
  && grep -qE '^[[:space:]]*\[resonance_tester\][[:space:]]*$' "${EBB_CFG}" \
  && grep -qE '^[[:space:]]*accel_chip[[:space:]]*:[[:space:]]*adxl345[[:space:]]*$' "${EBB_CFG}"; then
  has_ebb_adxl=1
fi

ADXL_MODE_EFFECTIVE=""
case "${TREED_ADXL_MODE}" in
  rpi|RPI)
    ADXL_MODE_EFFECTIVE="rpi"
    ;;
  ebb|EBB)
    ADXL_MODE_EFFECTIVE="ebb"
    ;;
  auto|AUTO|'')
    if [ "${has_rpi_include}" = "1" ]; then
      ADXL_MODE_EFFECTIVE="rpi"
    elif [ "${has_ebb_adxl}" = "1" ]; then
      ADXL_MODE_EFFECTIVE="ebb"
    fi
    ;;
  *)
    log_error "klipper-adxl-rpi: invalid TREED_ADXL_MODE='${TREED_ADXL_MODE}' (expected auto|rpi|ebb)"
    exit 1
    ;;
esac

if [ -z "${ADXL_MODE_EFFECTIVE}" ]; then
  log_error "klipper-adxl-rpi: cannot resolve ADXL mode (need printer include adxl345_rpi.cfg or EBB adxl345+resonance_tester)"
  exit 1
fi

if [ "${ADXL_MODE_EFFECTIVE}" = "rpi" ] && ! is_true_local "${TREED_ADXL_RPI_ENABLE}"; then
  log_error "klipper-adxl-rpi: TREED_ADXL_RPI_ENABLE=0 is not allowed in rpi mode"
  exit 1
fi

# Блок 4: Включение SPI в config.txt (idempotent) и проверка runtime-узла spidev (только rpi-режим).
ensure_spi_enabled() {
  local marker_begin="# --- TREED ADXL RPI SPI BEGIN ---"
  local marker_end="# --- TREED ADXL RPI SPI END ---"
  local tmp=""

  if [ -z "${SPI_CONFIG_FILE}" ] || [ ! -f "${SPI_CONFIG_FILE}" ]; then
    log_warn "klipper-adxl-rpi: CONFIG_FILE is missing, cannot manage dtparam=spi=on"
    return 0
  fi

  if grep -qE '^[[:space:]]*dtparam[[:space:]]*=[[:space:]]*spi=on([[:space:]]*#.*)?$' "${SPI_CONFIG_FILE}"; then
    log_info "klipper-adxl-rpi: SPI already enabled in ${SPI_CONFIG_FILE}"
    return 0
  fi

  tmp="$(mktemp)"
  awk -v b="${marker_begin}" -v e="${marker_end}" '
    $0 == b { skip = 1; next }
    $0 == e { skip = 0; next }
    !skip { print }
  ' "${SPI_CONFIG_FILE}" > "${tmp}"
  cat >> "${tmp}" <<EOF
${marker_begin}
dtparam=spi=on
${marker_end}
EOF
  cp "${tmp}" "${SPI_CONFIG_FILE}"
  rm -f "${tmp}"
  log_info "klipper-adxl-rpi: enabled SPI in ${SPI_CONFIG_FILE} (may require reboot if /dev/spidev* absent)"
}

if [ "${ADXL_MODE_EFFECTIVE}" = "rpi" ]; then
  if [ ! -f "${ADXL_SHIM_CFG}" ]; then
    log_error "klipper-adxl-rpi: missing runtime ADXL shim in rpi mode: ${ADXL_SHIM_CFG}"
    exit 1
  fi
  if [ ! -f "${ADXL_RPI_CFG}" ]; then
    log_error "klipper-adxl-rpi: missing runtime ADXL config in rpi mode: ${ADXL_RPI_CFG}"
    exit 1
  fi
  if [ "${has_rpi_include}" != "1" ]; then
    log_error "klipper-adxl-rpi: rpi mode requires include profiles/rn12_corexy_v1/adxl345_rpi.cfg in printer.cfg"
    exit 1
  fi

  ensure_spi_enabled
  if [ -e "${SPI_DEV}" ]; then
    log_info "klipper-adxl-rpi: SPI device is present: ${SPI_DEV}"
  else
    log_warn "klipper-adxl-rpi: SPI device is not present yet: ${SPI_DEV} (reboot may be required)"
  fi
else
  if [ "${has_ebb_adxl}" != "1" ]; then
    log_error "klipper-adxl-rpi: ebb mode requires [adxl345]+[resonance_tester] in ${EBB_CFG}"
    exit 1
  fi
  log_info "klipper-adxl-rpi: using EBB onboard ADXL mode (SPI/host-MCU steps skipped)"
fi

# Блок 5: Сборка/установка host MCU Klipper для Linux process (Pi, только rpi-режим).
build_and_install_host_mcu() {
  local outdir="${KLIPPER_HOST_MCU_OUTDIR}"
  local flash_out="out"

  if [ ! -d "${KLIPPER_DIR}" ]; then
    log_error "klipper-adxl-rpi: Klipper source dir not found: ${KLIPPER_DIR}"
    return 1
  fi
  if [ ! -f "${KLIPPER_DIR}/scripts/flash-linux.sh" ] || [ ! -f "${KLIPPER_HOST_MCU_UNIT_SRC}" ]; then
    log_error "klipper-adxl-rpi: missing host-MCU scripts in ${KLIPPER_DIR}/scripts"
    return 1
  fi
  if [ ! -f "${KLIPPER_DIR}/test/configs/linuxprocess.config" ]; then
    log_error "klipper-adxl-rpi: missing linuxprocess config template in ${KLIPPER_DIR}/test/configs"
    return 1
  fi

  if [ -x "${KLIPPER_HOST_MCU_BIN}" ] && ! is_true_local "${TREED_ADXL_RPI_REBUILD_HOST_MCU}"; then
    log_info "klipper-adxl-rpi: host MCU binary already present, rebuild skipped (set TREED_ADXL_RPI_REBUILD_HOST_MCU=1 to rebuild)"
  else
    log_info "klipper-adxl-rpi: building host MCU (linux process)"
    sudo -u "${PI_USER}" mkdir -p "${KLIPPER_DIR}/${outdir}"
    sudo -u "${PI_USER}" cp "${KLIPPER_DIR}/test/configs/linuxprocess.config" "${KLIPPER_HOST_MCU_KCONFIG}"
    sudo -u "${PI_USER}" env KCONFIG_CONFIG="${KLIPPER_HOST_MCU_KCONFIG}" make -C "${KLIPPER_DIR}" O="${KLIPPER_DIR}/${outdir}" olddefconfig
    sudo -u "${PI_USER}" env KCONFIG_CONFIG="${KLIPPER_HOST_MCU_KCONFIG}" make -C "${KLIPPER_DIR}" O="${KLIPPER_DIR}/${outdir}" -j2

    if [ -f "${KLIPPER_DIR}/${outdir}/klipper.elf" ]; then
      flash_out="${outdir}"
    elif [ -f "${KLIPPER_DIR}/out/klipper.elf" ]; then
      # В некоторых сборках Klipper Linux-process бинарь все равно кладется в `${KLIPPER_DIR}/out`.
      flash_out="out"
    else
      log_error "klipper-adxl-rpi: host MCU build completed but klipper.elf not found in ${KLIPPER_DIR}/${outdir} or ${KLIPPER_DIR}/out"
      return 1
    fi

    log_info "klipper-adxl-rpi: installing host MCU binary from ${KLIPPER_DIR}/${flash_out}"
    (cd "${KLIPPER_DIR}" && ./scripts/flash-linux.sh "${flash_out}")
  fi

  install -m 0644 "${KLIPPER_HOST_MCU_UNIT_SRC}" "${KLIPPER_HOST_MCU_UNIT_DST}"
  systemctl daemon-reload
  systemctl enable --now klipper-mcu.service
  log_info "klipper-adxl-rpi: host MCU service enabled and started (klipper-mcu.service)"
}

if [ "${ADXL_MODE_EFFECTIVE}" = "rpi" ]; then
  build_and_install_host_mcu
fi

# Блок 6: Нормализация runtime ADXL-конфига под выбранный SPI bus (только rpi-режим).
ensure_runtime_adxl_spi_bus() {
  local target_bus="$1"
  local tmp=""

  tmp="$(mktemp)"
  awk -v bus="${target_bus}" '
    /^[[:space:]]*spi_bus[[:space:]]*:/ {
      print "spi_bus: " bus
      next
    }
    { print }
  ' "${ADXL_RPI_CFG}" > "${tmp}"
  cp "${tmp}" "${ADXL_RPI_CFG}"
  rm -f "${tmp}"
  log_info "klipper-adxl-rpi: set ADXL spi_bus=${target_bus} in ${ADXL_RPI_CFG}"
}

if [ "${ADXL_MODE_EFFECTIVE}" = "rpi" ]; then
  ensure_runtime_adxl_spi_bus "${TREED_ADXL_RPI_SPI_BUS}"
fi

# Блок 7: Очистка legacy managed-блока ADXL в local_overrides.cfg.
cleanup_legacy_adxl_local_overrides_block() {
  local marker_begin="# --- TREED ADXL345 (Pi SPI) BEGIN ---"
  local marker_end="# --- TREED ADXL345 (Pi SPI) END ---"
  local tmp=""

  if [ ! -f "${LOCAL_OVERRIDES_CFG}" ]; then
    return 0
  fi

  if ! grep -qF "${marker_begin}" "${LOCAL_OVERRIDES_CFG}"; then
    return 0
  fi

  tmp="$(mktemp)"
  awk -v b="${marker_begin}" -v e="${marker_end}" '
    $0 == b { skip = 1; next }
    $0 == e { skip = 0; next }
    !skip { print }
  ' "${LOCAL_OVERRIDES_CFG}" > "${tmp}"

  cp "${tmp}" "${LOCAL_OVERRIDES_CFG}"
  rm -f "${tmp}"
  log_info "klipper-adxl-rpi: removed legacy ADXL marker-block from ${LOCAL_OVERRIDES_CFG}"
}

cleanup_legacy_adxl_local_overrides_block

# Блок 8: Права и завершение шага (перезапуск Klipper делается следующим шагом/verify).
if [ "${ADXL_MODE_EFFECTIVE}" = "rpi" ]; then
  chown "${PI_USER}:${grp}" "${ADXL_SHIM_CFG}" "${ADXL_RPI_CFG}" "${INPUT_SHAPER_CFG}" || true
else
  chown "${PI_USER}:${grp}" "${EBB_CFG}" "${INPUT_SHAPER_CFG}" || true
fi
if [ -f "${LOCAL_OVERRIDES_CFG}" ]; then
  chown "${PI_USER}:${grp}" "${LOCAL_OVERRIDES_CFG}" || true
fi
if [ -d "${KLIPPER_DIR}/${KLIPPER_HOST_MCU_OUTDIR}" ]; then
  chown -R "${PI_USER}:${grp}" "${KLIPPER_DIR}/${KLIPPER_HOST_MCU_OUTDIR}" || true
fi

if systemctl is-active --quiet klipper.service; then
  log_info "klipper-adxl-rpi: klipper.service is active, restarting to apply ADXL config"
  systemctl restart klipper.service
else
  log_info "klipper-adxl-rpi: klipper.service restart deferred (service not active)"
fi

if [ "${ADXL_MODE_EFFECTIVE}" = "rpi" ]; then
  log_info "klipper-adxl-rpi: DONE mode=rpi (spi_bus=${TREED_ADXL_RPI_SPI_BUS}, input_shaper=1 mandatory)"
else
  log_info "klipper-adxl-rpi: DONE mode=ebb (input_shaper=1 mandatory)"
fi
