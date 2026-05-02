#!/bin/bash
set -euo pipefail

# ==========================================
# ШАГ LOADER: KLIPPER CORE
# ==========================================
# Назначение:
# - Раскладывает staging-дерево Klipper в runtime-конфиг принтера.
# - Учитывает deploy mode и сохраняет идемпотентность шага.
# Контур:
# - required (формирует рабочий runtime-конфиг Klipper).

# Блок 1: Библиотеки и базовые проверки пользователя.
. "${REPO_DIR}/loader/lib/common.sh"

if [ -z "${PI_USER:-}" ]; then
  log_error "klipper-core: PI_USER is not set"
  exit 1
fi

if ! grp="$(pi_primary_group "${PI_USER}")"; then
  exit 1
fi

# Блок 2: Расчет путей и режима деплоя.
log_info "Step klipper-core: install full Klipper tree into /home/${PI_USER}/printer_data/config"

STAGE_DIR="${PI_HOME}/treed/klipper"
CONFIG_DIR="${PI_HOME}/printer_data/config"
DEPLOY_MODE="${TREED_DEPLOY_MODE_EFFECTIVE:-preserve}"
SAVE_CONFIG_MARKER="#*# <---------------------- SAVE_CONFIG ---------------------->"
LEGACY_PROFILE_REL_PATH="profiles/rn12_corexy_v1"

sanitize_save_config_block() {
  local src="$1"
  local dst="$2"

  awk '
    function normalize(line, out) {
      out = line
      sub(/^#\*#[[:space:]]*/, "", out)
      return tolower(out)
    }
    {
      normalized = normalize($0)
      if (normalized ~ /^\[[^]]+\][[:space:]]*$/) {
        section = normalized
        drop_section = (
          section ~ /^\[bltouch\]$/ ||
          section ~ /^\[probe\]$/ ||
          section ~ /^\[bed_mesh([[:space:]][^]]+)?\]$/
        )
      }

      if (drop_section) {
        next
      }

      if (section == "[stepper_z]" && normalized ~ /^position_endstop[[:space:]]*=/) {
        next
      }

      print
    }
  ' "${src}" > "${dst}"
}

case "${DEPLOY_MODE}" in
  clean|preserve)
    ;;
  *)
    log_error "klipper-core: unsupported TREED_DEPLOY_MODE_EFFECTIVE=${DEPLOY_MODE} (allowed: clean|preserve)"
    exit 1
    ;;
esac

# Блок 3: Валидация staging-дерева перед раскладкой.
if [ ! -d "${STAGE_DIR}" ]; then
  log_error "klipper-core: stage dir not found: ${STAGE_DIR}"
  exit 1
fi

if [ ! -f "${STAGE_DIR}/printer.cfg" ] || [ ! -d "${STAGE_DIR}/profiles" ]; then
  log_error "klipper-core: staging incomplete (missing printer.cfg or profiles): ${STAGE_DIR}"
  exit 1
fi

if [ -z "$(find "${STAGE_DIR}/profiles" -mindepth 1 -print -quit 2>/dev/null)" ]; then
  log_error "klipper-core: staging profiles directory is empty: ${STAGE_DIR}/profiles"
  exit 1
fi

ensure_dir "${CONFIG_DIR}"

# Блок 4: Preserve-режим — временно сохраняем локальные runtime-overrides.
TMP_KEEP=""
if [ "${DEPLOY_MODE}" = "preserve" ]; then
  TMP_KEEP="$(mktemp -d)"

  if [ -e "${CONFIG_DIR}/local_overrides.cfg" ]; then
    cp -a "${CONFIG_DIR}/local_overrides.cfg" "${TMP_KEEP}/" || true
    log_info "klipper-core: preserve mode, saving local_overrides.cfg"
  fi

  if [ -f "${CONFIG_DIR}/printer.cfg" ] && grep -Fq "${SAVE_CONFIG_MARKER}" "${CONFIG_DIR}/printer.cfg"; then
    awk -v marker="${SAVE_CONFIG_MARKER}" '
      index($0, marker) { keep = 1 }
      keep { print }
    ' "${CONFIG_DIR}/printer.cfg" > "${TMP_KEEP}/printer_save_config.block" || true

    if [ -s "${TMP_KEEP}/printer_save_config.block" ]; then
      TMP_SANITIZED="$(mktemp)"
      sanitize_save_config_block "${TMP_KEEP}/printer_save_config.block" "${TMP_SANITIZED}"
      mv "${TMP_SANITIZED}" "${TMP_KEEP}/printer_save_config.block"
    fi

    if [ -s "${TMP_KEEP}/printer_save_config.block" ]; then
      log_info "klipper-core: preserve mode, saving sanitized printer.cfg SAVE_CONFIG segment"
    else
      rm -f "${TMP_KEEP}/printer_save_config.block"
      log_warn "klipper-core: preserve mode, SAVE_CONFIG marker found but segment extraction is empty"
    fi
  fi

  if [ ! -e "${TMP_KEEP}/local_overrides.cfg" ] && [ ! -e "${TMP_KEEP}/printer_save_config.block" ]; then
    log_info "klipper-core: preserve mode, no local runtime overrides found"
  fi
else
  log_info "klipper-core: deploy mode ${DEPLOY_MODE}, runtime config will be rebuilt from staging"
fi

# Блок 5: Полная раскладка runtime-конфига из staging.
# Полная очистка runtime-слоя (без удаления самого каталога).
find "${CONFIG_DIR}" -mindepth 1 -maxdepth 1 -exec rm -rf {} +

# Полная раскладка дерева из staging в runtime.
cp -a "${STAGE_DIR}/." "${CONFIG_DIR}/"

# Блок 6: Очистка legacy-профилей, исключенных из V2 runtime-контура.
LEGACY_PROFILE_PATH="${CONFIG_DIR}/${LEGACY_PROFILE_REL_PATH}"
if [ -d "${LEGACY_PROFILE_PATH}" ]; then
  rm -rf "${LEGACY_PROFILE_PATH}"
  log_info "klipper-core: removed legacy runtime profile ${LEGACY_PROFILE_REL_PATH}"
fi

# Блок 7: Возврат preserve-override и финальная нормализация runtime.
# Возврат локальных runtime-overrides только в режиме preserve.
if [ -n "${TMP_KEEP}" ] && [ -d "${TMP_KEEP}" ]; then
  if [ -f "${TMP_KEEP}/local_overrides.cfg" ]; then
    cp -a "${TMP_KEEP}/local_overrides.cfg" "${CONFIG_DIR}/" || true
  fi

  if [ -f "${TMP_KEEP}/printer_save_config.block" ] && [ -f "${CONFIG_DIR}/printer.cfg" ]; then
    TMP_PRINTER="$(mktemp)"
    awk -v marker="${SAVE_CONFIG_MARKER}" '
      index($0, marker) { exit }
      { print }
    ' "${CONFIG_DIR}/printer.cfg" > "${TMP_PRINTER}"
    cat "${TMP_KEEP}/printer_save_config.block" >> "${TMP_PRINTER}"
    mv "${TMP_PRINTER}" "${CONFIG_DIR}/printer.cfg"
    log_info "klipper-core: restored printer.cfg SAVE_CONFIG segment"
  fi

  rm -rf "${TMP_KEEP}"
fi

# Блок 8: Гарантии пост-деплоя и финальная ownership-нормализация.
# Гарантируем наличие local_overrides.cfg после деплоя.
[ -f "${CONFIG_DIR}/local_overrides.cfg" ] || touch "${CONFIG_DIR}/local_overrides.cfg"

# В runtime не должно оставаться каталога treed.
rm -rf "${CONFIG_DIR}/treed" || true

chown -R "${PI_USER}:${grp}" "${CONFIG_DIR}"

log_info "klipper-core: OK (full tree installed to ${CONFIG_DIR})"
