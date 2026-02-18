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

# Блок 4: Preserve-режим — временно сохраняем local_overrides.cfg.
TMP_KEEP=""
if [ "${DEPLOY_MODE}" = "preserve" ] && [ -e "${CONFIG_DIR}/local_overrides.cfg" ]; then
  TMP_KEEP="$(mktemp -d)"
  cp -a "${CONFIG_DIR}/local_overrides.cfg" "${TMP_KEEP}/" || true
  log_info "klipper-core: preserve mode, saving local_overrides.cfg"
else
  log_info "klipper-core: deploy mode ${DEPLOY_MODE}, runtime config will be rebuilt from staging"
fi

# Блок 5: Полная раскладка runtime-конфига из staging.
# Полная очистка runtime-слоя (без удаления самого каталога).
find "${CONFIG_DIR}" -mindepth 1 -maxdepth 1 -exec rm -rf {} +

# Полная раскладка дерева из staging в runtime.
cp -a "${STAGE_DIR}/." "${CONFIG_DIR}/"

# Блок 6: Возврат preserve-override и финальная нормализация runtime.
# Возврат локального override только в режиме preserve.
if [ -n "${TMP_KEEP}" ] && [ -d "${TMP_KEEP}" ]; then
  cp -a "${TMP_KEEP}/." "${CONFIG_DIR}/" || true
  rm -rf "${TMP_KEEP}"
fi

# Гарантируем наличие local_overrides.cfg после деплоя.
[ -f "${CONFIG_DIR}/local_overrides.cfg" ] || touch "${CONFIG_DIR}/local_overrides.cfg"

# В runtime не должно оставаться каталога treed.
rm -rf "${CONFIG_DIR}/treed" || true

chown -R "${PI_USER}:${grp}" "${CONFIG_DIR}"

log_info "klipper-core: OK (full tree installed to ${CONFIG_DIR})"
