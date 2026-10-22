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
PROFILES_DIR="${CONFIG_DIR}/profiles"
ACTIVE_PROFILE_NAME="treed_v2_corexy_v1"
ACTIVE_PROFILE_PATH="${PROFILES_DIR}/${ACTIVE_PROFILE_NAME}"

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
        drop_section = 0
        if (section ~ /^\[bltouch\]$/ ||
            section ~ /^\[probe\]$/ ||
            section ~ /^\[bed_mesh([[:space:]][^]]+)?\]$/) {
          drop_section = 1
        }
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

# Блок 4: Preserve-режим — сохраняем локальные данные до успешного восстановления.
TMP_KEEP=""
if [ "${DEPLOY_MODE}" = "preserve" ]; then
  TMP_KEEP="$(dirname "${CONFIG_DIR}")/.treed-config-preserve"
  if [ -L "${TMP_KEEP}" ] || { [ -e "${TMP_KEEP}" ] && [ ! -d "${TMP_KEEP}" ]; }; then
    log_error "klipper-core: invalid recovery directory ${TMP_KEEP}"
    exit 1
  fi
  if [ -f "${TMP_KEEP}/.complete" ] && [ ! -L "${TMP_KEEP}/.complete" ]; then
    for preserve_name in treed_variables.cfg local_overrides.cfg filament_motion_runtime.cfg printer_save_config.block; do
      preserve_path="${TMP_KEEP}/${preserve_name}"
      if [ -L "${preserve_path}" ] || { [ -e "${preserve_path}" ] && [ ! -f "${preserve_path}" ]; }; then
        log_error "klipper-core: invalid preserved file ${preserve_path}"
        exit 1
      fi
    done
    log_info "klipper-core: resuming preserved settings from ${TMP_KEEP}"
  else
  for preserve_name in treed_variables.cfg local_overrides.cfg filament_motion_runtime.cfg printer.cfg; do
    preserve_path="${CONFIG_DIR}/${preserve_name}"
    if [ -e "${preserve_path}" ] || [ -L "${preserve_path}" ]; then
      if [ ! -f "${preserve_path}" ] || [ -L "${preserve_path}" ]; then
        log_error "klipper-core: ${preserve_name} must be a regular file; refusing to rebuild runtime"
        exit 1
      fi
    fi
  done

  # Неполный снимок не разрешал очистку runtime; его можно собрать заново.
  rm -rf "${TMP_KEEP}"
  mkdir -m 700 "${TMP_KEEP}"

  # Настройки режимов и калибровки принадлежат принтеру, а не staging.
  if [ -e "${CONFIG_DIR}/treed_variables.cfg" ]; then
    cp -a "${CONFIG_DIR}/treed_variables.cfg" "${TMP_KEEP}/" || { log_error "klipper-core: cannot preserve treed_variables.cfg; recovery copy unavailable in ${TMP_KEEP}"; exit 1; }
  fi

  if [ -e "${CONFIG_DIR}/local_overrides.cfg" ]; then
    cp -a "${CONFIG_DIR}/local_overrides.cfg" "${TMP_KEEP}/" || { log_error "klipper-core: cannot preserve local_overrides.cfg; recovery copy unavailable in ${TMP_KEEP}"; exit 1; }
    log_info "klipper-core: preserve mode, saving local_overrides.cfg"
  fi

  if [ -e "${CONFIG_DIR}/filament_motion_runtime.cfg" ]; then
    cp -a "${CONFIG_DIR}/filament_motion_runtime.cfg" "${TMP_KEEP}/" || { log_error "klipper-core: cannot preserve filament_motion_runtime.cfg; recovery copy unavailable in ${TMP_KEEP}"; exit 1; }
    log_info "klipper-core: preserve mode, saving filament_motion_runtime.cfg"
  fi

  SAVE_CONFIG_FOUND=0
  if [ -e "${CONFIG_DIR}/printer.cfg" ]; then
    if grep -Fq "${SAVE_CONFIG_MARKER}" "${CONFIG_DIR}/printer.cfg"; then
      SAVE_CONFIG_FOUND=1
    else
      grep_status=$?
      if [ "${grep_status}" -ne 1 ]; then
        log_error "klipper-core: cannot read printer.cfg for SAVE_CONFIG; runtime not rebuilt"
        exit 1
      fi
    fi
  fi

  if [ "${SAVE_CONFIG_FOUND}" = "1" ]; then
    awk -v marker="${SAVE_CONFIG_MARKER}" '
      index($0, marker) { keep = 1 }
      keep { print }
    ' "${CONFIG_DIR}/printer.cfg" > "${TMP_KEEP}/printer_save_config.block" || { log_error "klipper-core: cannot snapshot printer.cfg SAVE_CONFIG; recovery copy unavailable in ${TMP_KEEP}"; exit 1; }

    if [ -s "${TMP_KEEP}/printer_save_config.block" ]; then
      TMP_SANITIZED="$(mktemp)" || { log_error "klipper-core: cannot allocate SAVE_CONFIG sanitizer output; snapshot retained in ${TMP_KEEP}"; exit 1; }
      sanitize_save_config_block "${TMP_KEEP}/printer_save_config.block" "${TMP_SANITIZED}" || { log_error "klipper-core: cannot sanitize printer.cfg SAVE_CONFIG in ${TMP_KEEP}"; exit 1; }
      mv "${TMP_SANITIZED}" "${TMP_KEEP}/printer_save_config.block" || { log_error "klipper-core: cannot publish sanitized SAVE_CONFIG snapshot in ${TMP_KEEP}"; exit 1; }
    fi

    if [ -s "${TMP_KEEP}/printer_save_config.block" ]; then
      log_info "klipper-core: preserve mode, saving sanitized printer.cfg SAVE_CONFIG segment"
    else
      log_error "klipper-core: SAVE_CONFIG marker found but snapshot is empty; runtime not rebuilt (recovery data: ${TMP_KEEP})"
      exit 1
    fi
  fi

  if [ ! -e "${TMP_KEEP}/local_overrides.cfg" ] && [ ! -e "${TMP_KEEP}/filament_motion_runtime.cfg" ] && [ ! -e "${TMP_KEEP}/printer_save_config.block" ] && [ ! -e "${TMP_KEEP}/treed_variables.cfg" ]; then
    log_info "klipper-core: preserve mode, no local runtime overrides found"
  fi
  sync -f "${TMP_KEEP}"
  touch "${TMP_KEEP}/.complete"
  sync -f "${TMP_KEEP}"
  fi
else
  log_info "klipper-core: deploy mode ${DEPLOY_MODE}, runtime config will be rebuilt from staging"
  # Явный clean отменяет сохранение предыдущей незавершённой установки.
  rm -rf "$(dirname "${CONFIG_DIR}")/.treed-config-preserve"
fi

# Блок 5: Полная раскладка runtime-конфига из staging.
# Moonraker сохранит локальные параметры на своём шаге, поэтому до него их не удаляем.
if [ "${DEPLOY_MODE}" = "preserve" ]; then
  find "${CONFIG_DIR}" -mindepth 1 -maxdepth 1 ! -name moonraker ! -name moonraker.conf -exec rm -rf {} +
else
  find "${CONFIG_DIR}" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
fi

# Полная раскладка дерева из staging в runtime.
cp -a "${STAGE_DIR}/." "${CONFIG_DIR}/"

# Блок 6: Очистка runtime-профилей, не входящих в V2-контур.
if [ ! -d "${ACTIVE_PROFILE_PATH}" ]; then
  log_error "klipper-core: required V2 profile is missing after deploy: ${ACTIVE_PROFILE_PATH}"
  exit 1
fi

if [ -d "${PROFILES_DIR}" ]; then
  while IFS= read -r stale_profile; do
    [ -z "${stale_profile}" ] && continue
    rm -rf "${stale_profile}"
    log_info "klipper-core: removed non-V2 runtime profile $(basename "${stale_profile}")"
  done < <(find "${PROFILES_DIR}" -mindepth 1 -maxdepth 1 -type d ! -name "${ACTIVE_PROFILE_NAME}" -print)
fi

# Блок 7: Возврат preserve-override и финальная нормализация runtime.
# Возврат локальных runtime-overrides только в режиме preserve.
if [ -n "${TMP_KEEP}" ] && [ -d "${TMP_KEEP}" ]; then
  if [ -f "${TMP_KEEP}/treed_variables.cfg" ]; then
    cp -a "${TMP_KEEP}/treed_variables.cfg" "${CONFIG_DIR}/" || { log_error "klipper-core: cannot restore treed_variables.cfg; recovery copy retained in ${TMP_KEEP}"; exit 1; }
  fi
  if [ -f "${TMP_KEEP}/local_overrides.cfg" ]; then
    cp -a "${TMP_KEEP}/local_overrides.cfg" "${CONFIG_DIR}/" || { log_error "klipper-core: cannot restore local_overrides.cfg; recovery copy retained in ${TMP_KEEP}"; exit 1; }
    # Убираем устаревшие переключатели, сохраняя локальное значение Z-hop.
    sed -i \
      -e '/^\[include profiles\/treed_v2_corexy_v1\/eddy_force_move_calibration\.cfg\]$/d' \
      -e '/^[[:space:]]*SET_GCODE_VARIABLE MACRO=_TREED_PAUSE_PARK_CFG VARIABLE=park_y_raw_fallback /d' \
      -e '/^[[:space:]]*SET_GCODE_VARIABLE MACRO=_TREED_EDDY_Z_OFFSET_AUTOSAVE_STATE VARIABLE=enabled /d' \
      -e 's/^\([[:space:]]*SET_GCODE_VARIABLE MACRO=\)_TREED_Z_HOP_BEFORE_XY\( VARIABLE=z_hop \)/\1_TREED_EDDY_Z0_CFG\2/' \
      "${CONFIG_DIR}/local_overrides.cfg" || { log_error "klipper-core: cannot normalize local_overrides.cfg; recovery copy retained in ${TMP_KEEP}"; exit 1; }
  fi

  if [ -f "${TMP_KEEP}/filament_motion_runtime.cfg" ]; then
    cp -a "${TMP_KEEP}/filament_motion_runtime.cfg" "${CONFIG_DIR}/" || { log_error "klipper-core: cannot restore filament_motion_runtime.cfg; recovery copy retained in ${TMP_KEEP}"; exit 1; }
    # Сохраняем локальную чувствительность и переводим старый section на счётчик импульсов.
    sed -i 's/^\[filament_motion_sensor filament_motion\]$/[treed_filament_motion_sensor filament_motion]/' "${CONFIG_DIR}/filament_motion_runtime.cfg" || { log_error "klipper-core: cannot normalize filament_motion_runtime.cfg; recovery copy retained in ${TMP_KEEP}"; exit 1; }
    log_info "klipper-core: restored filament_motion_runtime.cfg"
  fi

  if [ -f "${TMP_KEEP}/printer_save_config.block" ] && [ -f "${CONFIG_DIR}/printer.cfg" ]; then
    TMP_PRINTER="$(mktemp)" || { log_error "klipper-core: cannot allocate printer.cfg restore output; recovery copy retained in ${TMP_KEEP}"; exit 1; }
    # Сохранённая PID-калибровка имеет приоритет над начальными значениями staging.
    # Комментируем только присутствующие в autosave поля, как штатный SAVE_CONFIG.
    awk -v marker="${SAVE_CONFIG_MARKER}" '
      FNR == 1 { section = "" }
      NR == FNR {
        line = tolower($0)
        sub(/^#\*#[[:space:]]*/, "", line)
        if (line ~ /^\[[^]]+\][[:space:]]*$/) {
          section = line
          sub(/[[:space:]]+$/, "", section)
        }
        if ((section == "[extruder]" || section == "[heater_bed]") &&
            line ~ /^(control|pid_kp|pid_ki|pid_kd)[[:space:]]*=/) {
          sub(/[[:space:]]*=.*$/, "", line)
          saved_pid[section, line] = 1
        }
        next
      }
      index($0, marker) { exit }
      {
        line = tolower($0)
        if (line ~ /^\[[^]]+\][[:space:]]*$/) {
          section = line
          sub(/[[:space:]]+$/, "", section)
        }
        sub(/[[:space:]]*:.*$/, "", line)
        if (saved_pid[section, line]) { $0 = "#" $0 }
      }
      { print }
    ' "${TMP_KEEP}/printer_save_config.block" "${CONFIG_DIR}/printer.cfg" > "${TMP_PRINTER}" || { log_error "klipper-core: cannot merge printer.cfg SAVE_CONFIG; recovery copy retained in ${TMP_KEEP}"; exit 1; }
    cat "${TMP_KEEP}/printer_save_config.block" >> "${TMP_PRINTER}" || { log_error "klipper-core: cannot append printer.cfg SAVE_CONFIG; recovery copy retained in ${TMP_KEEP}"; exit 1; }
    mv "${TMP_PRINTER}" "${CONFIG_DIR}/printer.cfg" || { log_error "klipper-core: cannot publish printer.cfg SAVE_CONFIG; recovery copy retained in ${TMP_KEEP}"; exit 1; }
    log_info "klipper-core: restored printer.cfg SAVE_CONFIG segment"
  fi

  sync -f "${CONFIG_DIR}"
  rm -rf "${TMP_KEEP}"
  sync -f "$(dirname "${CONFIG_DIR}")"
fi

# Блок 8: Гарантии пост-деплоя и финальная ownership-нормализация.
# Гарантируем наличие local_overrides.cfg после деплоя.
[ -f "${CONFIG_DIR}/local_overrides.cfg" ] || touch "${CONFIG_DIR}/local_overrides.cfg"

# В runtime не должно оставаться каталога treed.
rm -rf "${CONFIG_DIR}/treed" || true

chown -R "${PI_USER}:${grp}" "${CONFIG_DIR}"

log_info "klipper-core: OK (full tree installed to ${CONFIG_DIR})"
