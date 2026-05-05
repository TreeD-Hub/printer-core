#!/bin/bash
set -euo pipefail

# ==========================================
# ШАГ LOADER: FIRMWARE BUILD
# ==========================================
# Назначение:
# - Выполняет автоматическую сборку прошивок Klipper для V2-контуров MCU.
# - Публикует артефакты, checksum и build-report без автопрошивки.
# Контур:
# - required (ошибка сборки любого required target прерывает provisioning).

# Блок 1: Библиотеки и root-предусловия.
. "${REPO_DIR}/loader/lib/common.sh"

log_info "Step firmware-build: build firmware artifacts for V2 targets"
ensure_root

PI_USER="${PI_USER:-pi}"
PI_HOME="${PI_HOME:-/home/${PI_USER}}"
if ! grp="$(pi_primary_group "${PI_USER}")"; then
  exit 1
fi

TREED_FIRMWARE_BUILD_ENABLED="${TREED_FIRMWARE_BUILD_ENABLED:-1}"
case "${TREED_FIRMWARE_BUILD_ENABLED}" in
  0|1) ;;
  *)
    log_error "firmware-build: TREED_FIRMWARE_BUILD_ENABLED must be 0 or 1, got: ${TREED_FIRMWARE_BUILD_ENABLED}"
    exit 1
    ;;
esac

if [ "${TREED_FIRMWARE_BUILD_ENABLED}" = "0" ]; then
  log_info "firmware-build: disabled by TREED_FIRMWARE_BUILD_ENABLED=0"
  exit 0
fi

# Блок 2: Контракт путей/таргетов сборки.
TREED_KLIPPER_SRC_DIR="${TREED_KLIPPER_SRC_DIR:-${PI_HOME}/klipper}"
TREED_FIRMWARE_ARTIFACTS_DIR="${TREED_FIRMWARE_ARTIFACTS_DIR:-${PI_HOME}/treed/firmware-artifacts/treed-v2}"
TREED_FW_MAIN_CONFIG="${TREED_FW_MAIN_CONFIG:-${REPO_DIR}/firmware/configs/treed_v2/main_octopus_pro_f446_usb.config}"
TREED_FW_EBB_CONFIG="${TREED_FW_EBB_CONFIG:-${REPO_DIR}/firmware/configs/treed_v2/ebb42_can_stm32g0b1.config}"
TREED_FW_EDDY_CONFIG="${TREED_FW_EDDY_CONFIG:-${REPO_DIR}/firmware/configs/treed_v2/eddy_can_rp2040.config}"
TREED_EDDY_ENABLED="${TREED_EDDY_ENABLED:-1}"

case "${TREED_EDDY_ENABLED}" in
  0|1) ;;
  *)
    log_error "firmware-build: TREED_EDDY_ENABLED must be 0 or 1, got: ${TREED_EDDY_ENABLED}"
    exit 1
    ;;
esac

for abs_path_var in TREED_KLIPPER_SRC_DIR TREED_FIRMWARE_ARTIFACTS_DIR TREED_FW_MAIN_CONFIG TREED_FW_EBB_CONFIG TREED_FW_EDDY_CONFIG; do
  abs_path_val="$(eval "printf '%s' \"\${${abs_path_var}}\"")"
  case "${abs_path_val}" in
    /*) ;;
    *)
      log_error "firmware-build: ${abs_path_var} must be absolute path, got: ${abs_path_val}"
      exit 1
      ;;
  esac
done

if [ ! -d "${TREED_KLIPPER_SRC_DIR}" ]; then
  log_error "firmware-build: Klipper source dir not found: ${TREED_KLIPPER_SRC_DIR}"
  exit 1
fi
if [ ! -f "${TREED_KLIPPER_SRC_DIR}/Makefile" ]; then
  log_error "firmware-build: Makefile not found in ${TREED_KLIPPER_SRC_DIR}"
  exit 1
fi

for required_cfg in "${TREED_FW_MAIN_CONFIG}" "${TREED_FW_EBB_CONFIG}"; do
  if [ ! -f "${required_cfg}" ]; then
    log_error "firmware-build: required target config not found: ${required_cfg}"
    exit 1
  fi
done
if [ "${TREED_EDDY_ENABLED}" = "1" ] && [ ! -f "${TREED_FW_EDDY_CONFIG}" ]; then
  log_error "firmware-build: TREED_EDDY_ENABLED=1 but config is missing: ${TREED_FW_EDDY_CONFIG}"
  exit 1
fi

for cmd in make sha256sum; do
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    log_error "firmware-build: required command not found: ${cmd}"
    exit 1
  fi
done

BUILD_JOBS="$(nproc 2>/dev/null || echo 1)"
case "${BUILD_JOBS}" in
  ''|*[!0-9]*) BUILD_JOBS=1 ;;
esac
if [ "${BUILD_JOBS}" -le 0 ]; then
  BUILD_JOBS=1
fi

# Блок 3: Подготовка каталога артефактов и отчетов.
RUN_ID="$(date +"%Y%m%d-%H%M%S")"
RUN_DIR="${TREED_FIRMWARE_ARTIFACTS_DIR}/${RUN_ID}"
LATEST_LINK="${TREED_FIRMWARE_ARTIFACTS_DIR}/latest"
LOG_DIR="${RUN_DIR}/logs"
CFG_DIR="${RUN_DIR}/configs"
ART_DIR="${RUN_DIR}/artifacts"
REPORT_FILE="${RUN_DIR}/build-report.txt"
MANIFEST_FILE="${RUN_DIR}/manifest.tsv"
CHECKSUM_FILE="${RUN_DIR}/checksums.sha256"

ensure_dir "${TREED_FIRMWARE_ARTIFACTS_DIR}"
ensure_dir "${RUN_DIR}"
ensure_dir "${LOG_DIR}"
ensure_dir "${CFG_DIR}"
ensure_dir "${ART_DIR}"

cat > "${REPORT_FILE}" <<EOF
TreeD V2 firmware build report
started_at=$(date -Iseconds)
run_id=${RUN_ID}
klipper_src=${TREED_KLIPPER_SRC_DIR}
build_jobs=${BUILD_JOBS}
eddy_enabled=${TREED_EDDY_ENABLED}
EOF

printf 'target\tartifact\tsha256\tconfig\n' > "${MANIFEST_FILE}"
> "${CHECKSUM_FILE}"

# Блок 4: Функция сборки target с fail-fast и журналом.
build_target() {
  local target_name="$1"
  local target_config="$2"
  local expected_config_marker="$3"
  local artifact_name="$4"
  local build_output_rel="${5:-out/klipper.bin}"

  local target_log="${LOG_DIR}/${target_name}.log"
  local target_cfg="${CFG_DIR}/${target_name}.config"
  local target_art_dir="${ART_DIR}/${target_name}"
  local target_artifact="${target_art_dir}/${artifact_name}"
  local build_output="${TREED_KLIPPER_SRC_DIR}/${build_output_rel}"
  local target_sha=""

  ensure_dir "${target_art_dir}"
  cp -f "${target_config}" "${target_cfg}"
  chown "${PI_USER}:${grp}" "${target_cfg}" || true

  log_info "firmware-build: building target=${target_name} config=${target_config}"
  if sudo -u "${PI_USER}" -H bash -lc "set -euo pipefail; cd '${TREED_KLIPPER_SRC_DIR}'; make clean; make KCONFIG_CONFIG='${target_cfg}' olddefconfig; grep -qE '${expected_config_marker}' '${target_cfg}'; make -j${BUILD_JOBS} KCONFIG_CONFIG='${target_cfg}'" > "${target_log}" 2>&1; then
    :
  else
    log_error "firmware-build: target ${target_name} failed (see ${target_log})"
    exit 1
  fi

  if [ ! -f "${build_output}" ]; then
    log_error "firmware-build: ${build_output_rel} not found after target ${target_name}"
    exit 1
  fi

  cp -f "${build_output}" "${target_artifact}"
  target_sha="$(sha256sum "${target_artifact}" | awk '{print $1}')"
  printf '%s\t%s\t%s\t%s\n' "${target_name}" "${target_artifact}" "${target_sha}" "${target_config}" >> "${MANIFEST_FILE}"
  printf '%s  %s\n' "${target_sha}" "${target_artifact}" >> "${CHECKSUM_FILE}"
  printf 'target=%s status=ok artifact=%s sha256=%s\n' "${target_name}" "${target_artifact}" "${target_sha}" >> "${REPORT_FILE}"

  log_info "firmware-build: target ${target_name} OK (${target_artifact})"
}

# Блок 5: Сборка required target-ов.
build_target "main_octopus" "${TREED_FW_MAIN_CONFIG}" '^CONFIG_MACH_STM32F446=y$' "firmware-main-octopus.bin"
build_target "ebb42_can" "${TREED_FW_EBB_CONFIG}" '^CONFIG_MACH_STM32G0B1=y$' "firmware-ebb42-can.bin"

# Блок 6: Optional target Eddy.
if [ "${TREED_EDDY_ENABLED}" = "1" ]; then
  build_target "eddy_can" "${TREED_FW_EDDY_CONFIG}" '^CONFIG_MACH_RP2040=y$' "firmware-eddy-can.uf2" "out/klipper.uf2"
else
  printf 'target=eddy_can status=skipped reason=TREED_EDDY_ENABLED=0\n' >> "${REPORT_FILE}"
  log_info "firmware-build: target eddy_can skipped (TREED_EDDY_ENABLED=0)"
fi

# Блок 7: Финализация "latest" ссылки и прав.
ln -sfn "${RUN_DIR}" "${LATEST_LINK}"
chown -R "${PI_USER}:${grp}" "${TREED_FIRMWARE_ARTIFACTS_DIR}" || true

printf 'completed_at=%s\n' "$(date -Iseconds)" >> "${REPORT_FILE}"
log_info "firmware-build: OK (run_dir=${RUN_DIR}, manifest=${MANIFEST_FILE}, checksums=${CHECKSUM_FILE})"
