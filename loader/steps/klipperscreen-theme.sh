#!/bin/bash
set -euo pipefail

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
DEPLOY_MODE="${TREED_DEPLOY_MODE_EFFECTIVE:-preserve}"
THEME_DEPLOYED=0
THEME_CONFIG_UPDATED=0

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

find_fallback_icon_pack() {
  local candidate=""

  for candidate in \
    "${KS_STYLES_DIR}/material-dark/images" \
    "${KS_STYLES_DIR}/material-light/images" \
    "${KS_STYLES_DIR}/z-bolt/images"
  do
    if [ -d "${candidate}" ] \
      && [ -n "$(find "${candidate}" -maxdepth 1 -type f -print -quit 2>/dev/null)" ]; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done

  candidate="$(
    find "${KS_STYLES_DIR}" -mindepth 2 -maxdepth 2 -type d -name images 2>/dev/null \
      | head -n 1 || true
  )"
  if [ -n "${candidate}" ] \
    && [ -n "$(find "${candidate}" -maxdepth 1 -type f -print -quit 2>/dev/null)" ]; then
    printf '%s\n' "${candidate}"
    return 0
  fi

  return 1
}

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

if [ -n "${KS_THEME}" ] && [ "${KS_THEME}" != "keep" ]; then
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
  fi

  ensure_dir "${KS_CONFIG_DIR}"
  if [ "${DEPLOY_MODE}" = "preserve" ]; then
    backup_file_once "${KS_CONFIG_FILE}"
  else
    log_info "klipperscreen-theme: clean mode, skip backup for ${KS_CONFIG_FILE}"
  fi

  if [ ! -f "${KS_CONFIG_FILE}" ]; then
    cat > "${KS_CONFIG_FILE}" <<EOF
[main]
theme = ${KS_THEME}
EOF
  else
    tmp_cfg="$(mktemp)"
    awk -v theme="${KS_THEME}" '
      BEGIN {
        in_main = 0
        main_seen = 0
        theme_written = 0
      }
      {
        if ($0 ~ /^\[main\][[:space:]]*$/) {
          if (in_main && !theme_written) {
            print "theme = " theme
            theme_written = 1
          }
          print $0
          in_main = 1
          main_seen = 1
          next
        }
        if (in_main && $0 ~ /^\[[^]]+\][[:space:]]*$/) {
          if (!theme_written) {
            print "theme = " theme
            theme_written = 1
          }
          in_main = 0
        }
        if (in_main && $0 ~ /^[[:space:]]*theme[[:space:]]*[:=][[:space:]]*/) {
          if (!theme_written) {
            print "theme = " theme
            theme_written = 1
          }
          next
        }
        print $0
      }
      END {
        if (in_main && !theme_written) {
          print "theme = " theme
          theme_written = 1
        }
        if (!main_seen) {
          if (NR > 0) {
            print ""
          }
          print "[main]"
          print "theme = " theme
        }
      }
    ' "${KS_CONFIG_FILE}" > "${tmp_cfg}"
    mv "${tmp_cfg}" "${KS_CONFIG_FILE}"
  fi
  chown "${PI_USER}:${grp}" "${KS_CONFIG_FILE}" || true
  THEME_CONFIG_UPDATED=1
else
  log_info "klipperscreen-theme: theme switch skipped (TREED_KS_THEME=${KS_THEME:-empty})"
fi

if systemctl cat KlipperScreen.service >/dev/null 2>&1; then
  if systemctl is-active --quiet KlipperScreen.service; then
    systemctl restart KlipperScreen.service
    log_info "klipperscreen-theme: restarted KlipperScreen.service"
  else
    log_info "klipperscreen-theme: KlipperScreen.service is not active, restart skipped"
  fi
else
  log_warn "klipperscreen-theme: KlipperScreen.service not found, restart skipped"
fi

log_info "klipperscreen-theme: OK (theme=${TREED_THEME_NAME}, deployed=${THEME_DEPLOYED}, config_updated=${THEME_CONFIG_UPDATED}, selected=${KS_THEME})"
