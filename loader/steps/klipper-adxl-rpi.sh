#!/bin/bash
set -euo pipefail

# ==========================================
# ШАГ LOADER: KLIPPER ADXL RPI
# ==========================================
# Назначение:
# - Поднимает host MCU (`klipper-mcu.service`) на Raspberry Pi для ADXL345 по SPI.
# - Включает managed-блок ADXL include в `local_overrides.cfg` (opt-in через env).
# - Подготавливает runtime к проверке ADXL в `verify.sh`.
# Контур:
# - optional (best-effort); реальная обязательность задается через verify/ENV.

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
. "${REPO_DIR}/loader/lib/common.sh"
. "${REPO_DIR}/loader/lib/rpi.sh"

# Блок 1: Базовая инициализация шага и helper-предикаты.
ensure_root
log_info "Step klipper-adxl-rpi: optional ADXL345 via Raspberry Pi SPI"

is_true_local() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

# Блок 2: Параметры управления шагом (opt-in).
TREED_ADXL_RPI_ENABLE="${TREED_ADXL_RPI_ENABLE:-0}"
TREED_ADXL_RPI_SPI_BUS="${TREED_ADXL_RPI_SPI_BUS:-spidev0.0}"
TREED_ADXL_RPI_ENABLE_INPUT_SHAPER="${TREED_ADXL_RPI_ENABLE_INPUT_SHAPER:-0}"
TREED_ADXL_RPI_REBUILD_HOST_MCU="${TREED_ADXL_RPI_REBUILD_HOST_MCU:-0}"

if ! is_true_local "${TREED_ADXL_RPI_ENABLE}"; then
  log_info "klipper-adxl-rpi: disabled (set TREED_ADXL_RPI_ENABLE=1 to enable)"
  exit 0
fi

PI_USER="${PI_USER:-pi}"
PI_HOME="${PI_HOME:-/home/${PI_USER}}"
if ! grp="$(pi_primary_group "${PI_USER}")"; then
  exit 1
fi

CONFIG_DIR="${PI_HOME}/printer_data/config"
LOCAL_OVERRIDES_CFG="${CONFIG_DIR}/local_overrides.cfg"
PROFILE_DIR="${CONFIG_DIR}/profiles/rn12_hbot_v1"
ADXL_CFG="${PROFILE_DIR}/optional_adxl345_rpi.cfg"
RESONANCE_CFG="${PROFILE_DIR}/optional_resonance_tester.cfg"
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

# Блок 3: Проверка runtime-конфига профиля перед включением managed-блока.
if [ ! -d "${PROFILE_DIR}" ]; then
  log_error "klipper-adxl-rpi: profile dir not found: ${PROFILE_DIR}"
  exit 1
fi
if [ ! -f "${ADXL_CFG}" ]; then
  log_error "klipper-adxl-rpi: missing runtime ADXL config: ${ADXL_CFG}"
  exit 1
fi
if [ ! -f "${RESONANCE_CFG}" ]; then
  log_error "klipper-adxl-rpi: missing runtime resonance config: ${RESONANCE_CFG}"
  exit 1
fi

# Блок 4: Включение SPI в config.txt (idempotent) и проверка runtime-узла spidev.
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

ensure_spi_enabled
if [ -e "${SPI_DEV}" ]; then
  log_info "klipper-adxl-rpi: SPI device is present: ${SPI_DEV}"
else
  log_warn "klipper-adxl-rpi: SPI device is not present yet: ${SPI_DEV} (reboot may be required)"
fi

# Блок 5: Сборка/установка host MCU Klipper для Linux process (Pi).
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

build_and_install_host_mcu

# Блок 6: Нормализация runtime ADXL-конфига под выбранный SPI bus.
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
  ' "${ADXL_CFG}" > "${tmp}"
  cp "${tmp}" "${ADXL_CFG}"
  rm -f "${tmp}"
  log_info "klipper-adxl-rpi: set ADXL spi_bus=${target_bus} in ${ADXL_CFG}"
}

ensure_runtime_adxl_spi_bus "${TREED_ADXL_RPI_SPI_BUS}"

# Блок 7: Managed-блок в local_overrides.cfg для включения ADXL optional include.
ensure_adxl_local_overrides_block() {
  local marker_begin="# --- TREED ADXL345 (Pi SPI) BEGIN ---"
  local marker_end="# --- TREED ADXL345 (Pi SPI) END ---"
  local input_shaper_line="# [include profiles/rn12_hbot_v1/optional_input_shaper.cfg]"
  local tmp=""

  if is_true_local "${TREED_ADXL_RPI_ENABLE_INPUT_SHAPER}"; then
    input_shaper_line="[include profiles/rn12_hbot_v1/optional_input_shaper.cfg]"
  fi

  touch "${LOCAL_OVERRIDES_CFG}"
  tmp="$(mktemp)"
  awk -v b="${marker_begin}" -v e="${marker_end}" '
    $0 == b { skip = 1; next }
    $0 == e { skip = 0; next }
    !skip { print }
  ' "${LOCAL_OVERRIDES_CFG}" > "${tmp}"

  cat >> "${tmp}" <<EOF
${marker_begin}
[include profiles/rn12_hbot_v1/optional_adxl345_rpi.cfg]
[include profiles/rn12_hbot_v1/optional_resonance_tester.cfg]
${input_shaper_line}
${marker_end}
EOF

  cp "${tmp}" "${LOCAL_OVERRIDES_CFG}"
  rm -f "${tmp}"
  log_info "klipper-adxl-rpi: updated managed ADXL block in ${LOCAL_OVERRIDES_CFG}"
}

ensure_adxl_local_overrides_block

# Блок 8: Права и завершение шага (перезапуск Klipper делается следующим шагом/verify).
chown "${PI_USER}:${grp}" "${LOCAL_OVERRIDES_CFG}" "${ADXL_CFG}" "${RESONANCE_CFG}" || true
if [ -d "${KLIPPER_DIR}/${KLIPPER_HOST_MCU_OUTDIR}" ]; then
  chown -R "${PI_USER}:${grp}" "${KLIPPER_DIR}/${KLIPPER_HOST_MCU_OUTDIR}" || true
fi

if systemctl is-active --quiet klipper.service; then
  log_info "klipper-adxl-rpi: klipper.service is active, restarting to apply ADXL config"
  systemctl restart klipper.service
else
  log_info "klipper-adxl-rpi: klipper.service restart deferred (service not active)"
fi

log_info "klipper-adxl-rpi: DONE (spi_bus=${TREED_ADXL_RPI_SPI_BUS}, input_shaper=$(is_true_local "${TREED_ADXL_RPI_ENABLE_INPUT_SHAPER}" && echo 1 || echo 0))"
