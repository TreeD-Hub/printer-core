#!/bin/bash
set -euo pipefail

# ==========================================
# ШАГ LOADER: KLIPPERSCREEN THEME
# ==========================================
# Назначение:
# - Разворачивает тему TreeD для KlipperScreen и шрифт темы.
# - Поддерживает fallback ресурсов и проверку целостности темы.
# Контур:
# - required при наличии KlipperScreen; часть действий выполняется best-effort.

# Блок 1: Библиотеки, root-права и пользовательский контекст.
. "${REPO_DIR}/loader/lib/common.sh"

log_info "Step klipperscreen-theme: deploying TreeD KlipperScreen theme"

ensure_root

PI_USER="${PI_USER:-${SUDO_USER:-pi}}"
PI_HOME="${PI_HOME:-/home/${PI_USER}}"
if ! grp="$(pi_primary_group "${PI_USER}")"; then
  exit 1
fi

TREED_THEME_NAME="treed-oled"
THEME_SRC="${REPO_DIR}/klipperscreen/themes/${TREED_THEME_NAME}"
FONT_FILE_NAME="web_ibm_mda.ttf"
FONT_FAMILY_NAME="WebPlus IBM MDA"
FONT_SRC_PATH="${THEME_SRC}/${FONT_FILE_NAME}"
FONT_DST_DIR="/usr/local/share/fonts/treed"
FONT_DST_PATH="${FONT_DST_DIR}/${FONT_FILE_NAME}"

KS_HOME_DEFAULT="${PI_HOME}/KlipperScreen"
if [ -n "${TREED_KLIPPERSCREEN_HOME:-}" ]; then
  KS_HOME="${TREED_KLIPPERSCREEN_HOME}"
  log_info "klipperscreen-theme: using TREED_KLIPPERSCREEN_HOME=${KS_HOME}"
else
  KS_HOME="$(detect_klipperscreen_home "${KS_HOME_DEFAULT}")"
  log_info "klipperscreen-theme: resolved KlipperScreen home=${KS_HOME}"
fi

KS_STYLES_DIR="${KS_HOME}/styles"
THEME_DST="${KS_STYLES_DIR}/${TREED_THEME_NAME}"

KS_CONFIG_DIR="${PI_HOME}/printer_data/config"
KS_CONFIG_FILE="${KS_CONFIG_DIR}/KlipperScreen.conf"
KS_THEME="${TREED_KS_THEME:-${TREED_THEME_NAME}}"
KS_LANGUAGE="${TREED_KS_LANGUAGE:-ru}"
DEPLOY_MODE="${TREED_DEPLOY_MODE_EFFECTIVE:-preserve}"
THEME_DEPLOYED=0
THEME_CONFIG_UPDATED=0
FONT_DEPLOYED=0
APPLY_THEME=0
APPLY_LANGUAGE=0

# Блок 2: Валидация режима деплоя и обязательных исходников темы.
case "${DEPLOY_MODE}" in
  clean|preserve)
    ;;
  *)
    log_error "klipperscreen-theme: unsupported TREED_DEPLOY_MODE_EFFECTIVE=${DEPLOY_MODE} (allowed: clean|preserve)"
    exit 1
    ;;
esac

if [ ! -f "${THEME_SRC}/style.css" ]; then
  log_error "klipperscreen-theme: missing theme source ${THEME_SRC}/style.css"
  exit 1
fi

if [ ! -f "${FONT_SRC_PATH}" ]; then
  log_error "klipperscreen-theme: missing theme font ${FONT_SRC_PATH}"
  exit 1
fi

if [ -n "${KS_THEME}" ] && [ "${KS_THEME}" != "keep" ]; then
  APPLY_THEME=1
fi
if [ -n "${KS_LANGUAGE}" ] && [ "${KS_LANGUAGE}" != "keep" ]; then
  APPLY_LANGUAGE=1
fi

# Из style.css извлекаем обязательные иконки, чтобы проверить полноту набора темы.
# Блок 3: Вспомогательные функции проверки и записи конфигурации.
extract_required_theme_icons() {
  local style_file="$1"
  if [ ! -f "${style_file}" ]; then
    return 0
  fi

  grep -Eo "images/[^\"' )?#;]+" "${style_file}" 2>/dev/null \
    | sed 's|^images/||' \
    | sort -u
}

missing_required_icons() {
  local images_dir="$1"
  local required_icons="$2"
  local icon=""

  while IFS= read -r icon; do
    [ -z "${icon}" ] && continue
    if [ ! -f "${images_dir}/${icon}" ]; then
      printf '%s\n' "${icon}"
    fi
  done <<< "${required_icons}"
}

REQUIRED_THEME_ICONS="$(extract_required_theme_icons "${THEME_SRC}/style.css" || true)"
if [ -n "${REQUIRED_THEME_ICONS}" ]; then
  log_info "klipperscreen-theme: required theme icons from style.css: $(printf '%s' "${REQUIRED_THEME_ICONS}" | tr '\n' ' ')"
else
  log_info "klipperscreen-theme: no explicit images/* references in ${THEME_SRC}/style.css"
fi

icon_pack_is_usable() {
  local candidate="$1"
  local missing=""

  if [ ! -d "${candidate}" ]; then
    return 1
  fi
  if [ -z "$(find "${candidate}" -maxdepth 1 -type f -print -quit 2>/dev/null)" ]; then
    return 1
  fi
  if [ -z "${REQUIRED_THEME_ICONS}" ]; then
    return 0
  fi

  missing="$(missing_required_icons "${candidate}" "${REQUIRED_THEME_ICONS}" || true)"
  if [ -n "${missing}" ]; then
    return 1
  fi

  return 0
}

find_fallback_icon_pack() {
  local candidate=""

  for candidate in \
    "${KS_STYLES_DIR}/material-dark/images" \
    "${KS_STYLES_DIR}/material-light/images" \
    "${KS_STYLES_DIR}/z-bolt/images"
  do
    if icon_pack_is_usable "${candidate}"; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done

  while IFS= read -r candidate; do
    [ -z "${candidate}" ] && continue
    if icon_pack_is_usable "${candidate}"; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done < <(find "${KS_STYLES_DIR}" -mindepth 2 -maxdepth 2 -type d -name images 2>/dev/null)

  return 1
}

set_ks_main_key() {
  local cfg_file="$1"
  local cfg_key="$2"
  local cfg_value="$3"
  local tmp_cfg=""

  if [ ! -f "${cfg_file}" ]; then
    cat > "${cfg_file}" <<EOF
[main]
${cfg_key} = ${cfg_value}
EOF
    return 0
  fi

  tmp_cfg="$(mktemp)"
  awk -v key="${cfg_key}" -v value="${cfg_value}" '
    BEGIN {
      in_main = 0
      main_seen = 0
      key_written = 0
    }
    {
      if ($0 ~ /^\[main\][[:space:]]*$/) {
        if (in_main && !key_written) {
          print key " = " value
          key_written = 1
        }
        print $0
        in_main = 1
        main_seen = 1
        next
      }
      if (in_main && $0 ~ /^\[[^]]+\][[:space:]]*$/) {
        if (!key_written) {
          print key " = " value
          key_written = 1
        }
        in_main = 0
      }
      if (in_main && $0 ~ ("^[[:space:]]*" key "[[:space:]]*[:=][[:space:]]*")) {
        if (!key_written) {
          print key " = " value
          key_written = 1
        }
        next
      }
      print $0
    }
    END {
      if (in_main && !key_written) {
        print key " = " value
      }
      if (!main_seen) {
        if (NR > 0) {
          print ""
        }
        print "[main]"
        print key " = " value
      }
    }
  ' "${cfg_file}" > "${tmp_cfg}"
  mv "${tmp_cfg}" "${cfg_file}"
}

deploy_treed_font() {
  ensure_dir "${FONT_DST_DIR}"

  if [ ! -f "${FONT_DST_PATH}" ] || ! cmp -s "${FONT_SRC_PATH}" "${FONT_DST_PATH}"; then
    cp -f "${FONT_SRC_PATH}" "${FONT_DST_PATH}"
    chmod 0644 "${FONT_DST_PATH}"
    log_info "klipperscreen-theme: deployed font ${FONT_FAMILY_NAME} -> ${FONT_DST_PATH}"
  else
    log_info "klipperscreen-theme: font ${FONT_FAMILY_NAME} already up to date"
  fi

  if command -v fc-cache >/dev/null 2>&1; then
    fc-cache -f "${FONT_DST_DIR}" >/dev/null 2>&1
  else
    log_error "klipperscreen-theme: fc-cache not found, cannot register font ${FONT_FAMILY_NAME}"
    exit 1
  fi

  FONT_DEPLOYED=1
}

# Блок 4: Деплой шрифта темы.
deploy_treed_font

# Блок 5: Деплой файлов темы и fallback-иконок.
# Копируем тему полностью и при необходимости дополняем резервным набором иконок.
if [ -d "${KS_STYLES_DIR}" ]; then
  ensure_dir "${THEME_DST}"
  find "${THEME_DST}" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
  cp -a "${THEME_SRC}/." "${THEME_DST}/"

  if [ ! -d "${THEME_DST}/images" ] \
    || [ -z "$(find "${THEME_DST}/images" -maxdepth 1 -type f -print -quit 2>/dev/null)" ]; then
    fallback_images=""
    if fallback_images="$(find_fallback_icon_pack)"; then
      ensure_dir "${THEME_DST}/images"
      cp -a "${fallback_images}/." "${THEME_DST}/images/"
      log_info "klipperscreen-theme: copied fallback icon pack from ${fallback_images}"
    else
      log_warn "klipperscreen-theme: fallback icon pack not found, icons may be missing"
    fi
  fi

  chown -R "${PI_USER}:${grp}" "${THEME_DST}" || true
  THEME_DEPLOYED=1
else
  log_warn "klipperscreen-theme: styles dir not found (${KS_STYLES_DIR}), theme files deploy skipped"
fi

# Блок 6: Строгая проверка и запись theme/language в KlipperScreen.conf.
if [ "${APPLY_THEME}" = "1" ]; then
  # Строгая валидация для treed-oled: style.css, images/ и обязательные иконки должны существовать.
  if [ "${KS_THEME}" = "${TREED_THEME_NAME}" ]; then
    if [ ! -f "${THEME_DST}/style.css" ]; then
      log_error "klipperscreen-theme: requested theme ${TREED_THEME_NAME} but deployed style is missing (${THEME_DST}/style.css)"
      exit 1
    fi
    if [ ! -d "${THEME_DST}/images" ] \
      || [ -z "$(find "${THEME_DST}/images" -maxdepth 1 -type f -print -quit 2>/dev/null)" ]; then
      log_error "klipperscreen-theme: requested theme ${TREED_THEME_NAME} but icon pack is missing (${THEME_DST}/images)"
      exit 1
    fi
    if [ -n "${REQUIRED_THEME_ICONS}" ]; then
      missing_icons="$(missing_required_icons "${THEME_DST}/images" "${REQUIRED_THEME_ICONS}" || true)"
      if [ -n "${missing_icons}" ]; then
        log_error "klipperscreen-theme: requested theme ${TREED_THEME_NAME} missing required icons: $(printf '%s' "${missing_icons}" | tr '\n' ' ')"
        exit 1
      fi
    fi
  fi
fi

if [ "${APPLY_THEME}" = "1" ] || [ "${APPLY_LANGUAGE}" = "1" ]; then
  # Настройки пишем только в секцию [main], не затрагивая другие секции конфига.
  ensure_dir "${KS_CONFIG_DIR}"
  if [ "${DEPLOY_MODE}" = "preserve" ]; then
    backup_file_once "${KS_CONFIG_FILE}"
  else
    log_info "klipperscreen-theme: clean mode, skip backup for ${KS_CONFIG_FILE}"
  fi

  if [ "${APPLY_THEME}" = "1" ]; then
    set_ks_main_key "${KS_CONFIG_FILE}" "theme" "${KS_THEME}"
  fi
  if [ "${APPLY_LANGUAGE}" = "1" ]; then
    set_ks_main_key "${KS_CONFIG_FILE}" "language" "${KS_LANGUAGE}"
  fi

  chown "${PI_USER}:${grp}" "${KS_CONFIG_FILE}" || true
  THEME_CONFIG_UPDATED=1
else
  log_info "klipperscreen-theme: config update skipped (TREED_KS_THEME=${KS_THEME:-empty}, TREED_KS_LANGUAGE=${KS_LANGUAGE:-empty})"
fi

# Блок 7: Перезапуск KlipperScreen для применения темы/шрифта.
if systemctl cat KlipperScreen.service >/dev/null 2>&1; then
  # Перезапуск нужен, чтобы тема/язык и шрифт применились сразу.
  if systemctl is-active --quiet KlipperScreen.service; then
    systemctl restart KlipperScreen.service
    log_info "klipperscreen-theme: restarted KlipperScreen.service"
  else
    log_info "klipperscreen-theme: KlipperScreen.service is not active, restart skipped"
  fi
else
  log_warn "klipperscreen-theme: KlipperScreen.service not found, restart skipped"
fi

log_info "klipperscreen-theme: OK (theme=${TREED_THEME_NAME}, deployed=${THEME_DEPLOYED}, config_updated=${THEME_CONFIG_UPDATED}, font=${FONT_FAMILY_NAME}, font_deployed=${FONT_DEPLOYED}, selected_theme=${KS_THEME}, selected_language=${KS_LANGUAGE})"
