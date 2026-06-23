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
TREED_FW_MAIN_CONFIG="${TREED_FW_MAIN_CONFIG:-${REPO_DIR}/firmware/configs/treed_v2/main_octopus_pro_f446_can.config}"
TREED_FW_EBB_CONFIG="${TREED_FW_EBB_CONFIG:-${REPO_DIR}/firmware/configs/treed_v2/ebb42_can_stm32g0b1.config}"
TREED_FW_EDDY_CONFIG="${TREED_FW_EDDY_CONFIG:-${REPO_DIR}/firmware/configs/treed_v2/eddy_can_rp2040.config}"
TREED_EDDY_ENABLED="${TREED_EDDY_ENABLED:-1}"

case "${TREED_EDDY_ENABLED}" in
  1) ;;
  0)
    log_error "firmware-build: TREED_EDDY_ENABLED=0 is unsupported by treed_v2_corexy_v1; use TREED_FIRMWARE_BUILD_ENABLED=0 to skip all firmware builds"
    exit 1
    ;;
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

for required_cfg in "${TREED_FW_MAIN_CONFIG}" "${TREED_FW_EBB_CONFIG}" "${TREED_FW_EDDY_CONFIG}"; do
  if [ ! -f "${required_cfg}" ]; then
    log_error "firmware-build: required target config not found: ${required_cfg}"
    exit 1
  fi
done

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

# Блок 3: Проверка актуальности входов и подготовка каталога артефактов.
firmware_config_sha() {
  local path="$1"

  sha256sum "${path}" | awk '{print $1}'
}

KLIPPER_HEAD="unknown"
if command -v git >/dev/null 2>&1 && git -C "${TREED_KLIPPER_SRC_DIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  KLIPPER_HEAD="$(git -C "${TREED_KLIPPER_SRC_DIR}" rev-parse HEAD 2>/dev/null || printf '%s' "unknown")"
fi

FW_MAIN_SHA="$(firmware_config_sha "${TREED_FW_MAIN_CONFIG}")"
FW_EBB_SHA="$(firmware_config_sha "${TREED_FW_EBB_CONFIG}")"
FW_EDDY_SHA="$(firmware_config_sha "${TREED_FW_EDDY_CONFIG}")"

LATEST_LINK="${TREED_FIRMWARE_ARTIFACTS_DIR}/latest"

firmware_inputs_current() {
  local latest_dir=""
  local inputs_file=""
  local manifest_file=""
  local header=""
  local target=""
  local artifact=""
  local sha=""
  local config=""
  local artifact_count=0

  if [ ! -e "${LATEST_LINK}" ]; then
    return 1
  fi

  latest_dir="$(readlink -f "${LATEST_LINK}" 2>/dev/null || true)"
  if [ -z "${latest_dir}" ] || [ ! -d "${latest_dir}" ]; then
    return 1
  fi

  inputs_file="${latest_dir}/inputs.env"
  manifest_file="${latest_dir}/manifest.tsv"
  if [ ! -f "${inputs_file}" ] || [ ! -f "${manifest_file}" ] || [ ! -f "${latest_dir}/checksums.sha256" ]; then
    return 1
  fi

  grep -Fx "klipper_head=${KLIPPER_HEAD}" "${inputs_file}" >/dev/null || return 1
  grep -Fx "main_config_sha256=${FW_MAIN_SHA}" "${inputs_file}" >/dev/null || return 1
  grep -Fx "ebb_config_sha256=${FW_EBB_SHA}" "${inputs_file}" >/dev/null || return 1
  grep -Fx "eddy_enabled=${TREED_EDDY_ENABLED}" "${inputs_file}" >/dev/null || return 1
  grep -Fx "eddy_config_sha256=${FW_EDDY_SHA}" "${inputs_file}" >/dev/null || return 1

  while IFS=$'\t' read -r target artifact sha config; do
    if [ -z "${header}" ]; then
      header=1
      continue
    fi
    [ -n "${artifact}" ] || return 1
    [ -f "${artifact}" ] || return 1
    artifact_count=$((artifact_count+1))
  done < "${manifest_file}"

  [ "${artifact_count}" -gt 0 ] || return 1
  sha256sum -c "${latest_dir}/checksums.sha256" >/dev/null 2>&1 || return 1

  return 0
}

ensure_dir "${TREED_FIRMWARE_ARTIFACTS_DIR}"
if firmware_inputs_current; then
  log_info "firmware-build: existing artifacts match current inputs, skipping rebuild (${LATEST_LINK})"
  exit 0
fi

RUN_ID="$(date +"%Y%m%d-%H%M%S")"
RUN_DIR="${TREED_FIRMWARE_ARTIFACTS_DIR}/${RUN_ID}"
LOG_DIR="${RUN_DIR}/logs"
CFG_DIR="${RUN_DIR}/configs"
ART_DIR="${RUN_DIR}/artifacts"
REPORT_FILE="${RUN_DIR}/build-report.txt"
MANIFEST_FILE="${RUN_DIR}/manifest.tsv"
CHECKSUM_FILE="${RUN_DIR}/checksums.sha256"
INPUTS_FILE="${RUN_DIR}/inputs.env"

ensure_dir "${RUN_DIR}"
ensure_dir "${LOG_DIR}"
ensure_dir "${CFG_DIR}"
ensure_dir "${ART_DIR}"

cat > "${REPORT_FILE}" <<EOF
TreeD V2 firmware build report
started_at=$(date -Iseconds)
run_id=${RUN_ID}
klipper_src=${TREED_KLIPPER_SRC_DIR}
klipper_head=${KLIPPER_HEAD}
build_jobs=${BUILD_JOBS}
eddy_enabled=${TREED_EDDY_ENABLED}
EOF

cat > "${INPUTS_FILE}" <<EOF
klipper_head=${KLIPPER_HEAD}
main_config=${TREED_FW_MAIN_CONFIG}
main_config_sha256=${FW_MAIN_SHA}
ebb_config=${TREED_FW_EBB_CONFIG}
ebb_config_sha256=${FW_EBB_SHA}
eddy_enabled=${TREED_EDDY_ENABLED}
eddy_config=${TREED_FW_EDDY_CONFIG}
eddy_config_sha256=${FW_EDDY_SHA}
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

# Блок 6: Сборка обязательного Eddy target.
build_target "eddy_can" "${TREED_FW_EDDY_CONFIG}" '^CONFIG_MACH_RP2040=y$' "firmware-eddy-can.uf2" "out/klipper.uf2"

# Блок 7: Финализация "latest" ссылки и прав.
ln -sfn "${RUN_DIR}" "${LATEST_LINK}"
chown -R "${PI_USER}:${grp}" "${TREED_FIRMWARE_ARTIFACTS_DIR}" || true

printf 'completed_at=%s\n' "$(date -Iseconds)" >> "${REPORT_FILE}"
log_info "firmware-build: OK (run_dir=${RUN_DIR}, manifest=${MANIFEST_FILE}, checksums=${CHECKSUM_FILE})"
