#!/bin/bash
set -euo pipefail

# ==========================================
# ШАГ LOADER: KLIPPER ADXL
# ==========================================
# Назначение:
# - Валидирует mandatory-контур ADXL/Input Shaper для V2-профиля.
# - Использует только onboard ADXL345 на EBB42 (без host MCU и SPI на Pi).
# - Удаляет legacy marker-блок ADXL из `local_overrides.cfg`, если он остался от старой схемы.
# Контур:
# - required (fail-fast): ADXL и Input Shaper являются базовым контуром.

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
. "${REPO_DIR}/loader/lib/common.sh"

# Блок 1: Базовая инициализация шага.
ensure_root
log_info "Step klipper-adxl-rpi: validate mandatory ADXL345/Input Shaper on EBB42"

PI_USER="${PI_USER:-pi}"
PI_HOME="${PI_HOME:-/home/${PI_USER}}"
if ! grp="$(pi_primary_group "${PI_USER}")"; then
  exit 1
fi

CONFIG_DIR="${PI_HOME}/printer_data/config"
PRINTER_CFG="${CONFIG_DIR}/printer.cfg"
LOCAL_OVERRIDES_CFG="${CONFIG_DIR}/local_overrides.cfg"
PROFILE_DIR="${CONFIG_DIR}/profiles/treed_v2_corexy_v1"
EBB_CFG="${PROFILE_DIR}/ebb42_can.cfg"
INPUT_SHAPER_CFG="${PROFILE_DIR}/input_shaper.cfg"

# Блок 2: Проверка runtime-конфига профиля.
if [ ! -f "${PRINTER_CFG}" ]; then
  log_error "klipper-adxl-rpi: runtime printer.cfg not found: ${PRINTER_CFG}"
  exit 1
fi
if [ ! -d "${PROFILE_DIR}" ]; then
  log_error "klipper-adxl-rpi: profile dir not found: ${PROFILE_DIR}"
  exit 1
fi
if [ ! -f "${EBB_CFG}" ]; then
  log_error "klipper-adxl-rpi: missing runtime EBB config: ${EBB_CFG}"
  exit 1
fi
if [ ! -f "${INPUT_SHAPER_CFG}" ]; then
  log_error "klipper-adxl-rpi: missing runtime Input Shaper config: ${INPUT_SHAPER_CFG}"
  exit 1
fi

if ! grep -qE '^[[:space:]]*\[include[[:space:]]+profiles/treed_v2_corexy_v1/input_shaper\.cfg\][[:space:]]*$' "${PRINTER_CFG}"; then
  log_error "klipper-adxl-rpi: printer.cfg must include profiles/treed_v2_corexy_v1/input_shaper.cfg"
  exit 1
fi

if ! grep -qE '^[[:space:]]*\[include[[:space:]]+profiles/treed_v2_corexy_v1/ebb42_can\.cfg\][[:space:]]*$' "${PRINTER_CFG}"; then
  log_error "klipper-adxl-rpi: printer.cfg must include profiles/treed_v2_corexy_v1/ebb42_can.cfg"
  exit 1
fi

if grep -qE '^[[:space:]]*\[include[[:space:]]+profiles/treed_v2_corexy_v1/adxl345_rpi\.cfg\][[:space:]]*$' "${PRINTER_CFG}"; then
  log_error "klipper-adxl-rpi: legacy include profiles/treed_v2_corexy_v1/adxl345_rpi.cfg is not supported"
  exit 1
fi

if ! grep -qE '^[[:space:]]*\[adxl345\][[:space:]]*$' "${EBB_CFG}" \
  || ! grep -qE '^[[:space:]]*cs_pin[[:space:]]*:[[:space:]]*EBBCan:PB12[[:space:]]*$' "${EBB_CFG}" \
  || ! grep -qE '^[[:space:]]*spi_bus[[:space:]]*:[[:space:]]*spi2_PB2_PB11_PB10[[:space:]]*$' "${EBB_CFG}" \
  || ! grep -qE '^[[:space:]]*\[resonance_tester\][[:space:]]*$' "${EBB_CFG}" \
  || ! grep -qE '^[[:space:]]*accel_chip[[:space:]]*:[[:space:]]*adxl345[[:space:]]*$' "${EBB_CFG}"; then
  log_error "klipper-adxl-rpi: ebb42_can.cfg must contain onboard ADXL345 + resonance_tester blocks"
  exit 1
fi

# Блок 3: Очистка legacy managed-блока ADXL в local_overrides.cfg.
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

# Блок 4: Права и завершение шага.
chown "${PI_USER}:${grp}" "${EBB_CFG}" "${INPUT_SHAPER_CFG}" || true
if [ -f "${LOCAL_OVERRIDES_CFG}" ]; then
  chown "${PI_USER}:${grp}" "${LOCAL_OVERRIDES_CFG}" || true
fi

if systemctl is-active --quiet klipper.service; then
  log_info "klipper-adxl-rpi: klipper.service is active, restarting to apply ADXL config"
  systemctl restart klipper.service
else
  log_info "klipper-adxl-rpi: klipper.service restart deferred (service not active)"
fi

log_info "klipper-adxl-rpi: DONE mode=ebb-only (input_shaper=1 mandatory)"
