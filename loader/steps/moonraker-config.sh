#!/bin/bash
set -euo pipefail

# ==========================================
# ШАГ LOADER: MOONRAKER CONFIG
# ==========================================
# Назначение:
# - Синхронизирует moonraker.conf, base-фрагменты и компонент shell_command.
# - Учитывает deploy mode и режим backup/preserve.
# Контур:
# - required (база API/интеграций TreeD и generated-фрагментов).

# Блок 1: Библиотеки и root-права.
. "${REPO_DIR}/loader/lib/common.sh"
ensure_root

# Блок 2: Старт шага и расчет путей/режима деплоя.
log_info "Step moonraker-config: syncing Moonraker config"

SRC_CONF="${REPO_DIR}/moonraker/moonraker.conf"
DST_CONF="${PI_HOME}/printer_data/config/moonraker.conf"
SRC_BASE_DIR="${REPO_DIR}/moonraker/base"
DST_BASE_DIR="${PI_HOME}/printer_data/config/moonraker/base"
DST_GENERATED_DIR="${PI_HOME}/printer_data/config/moonraker/generated"
SRC_COMPONENT="${REPO_DIR}/moonraker/components/treed_shell_command.py"
COMPONENT_NAME="treed_shell_command.py"
DEPLOY_MODE="${TREED_DEPLOY_MODE_EFFECTIVE:-preserve}"
TREED_MAINSAIL_WEB_PATH="${TREED_MAINSAIL_WEB_PATH:-}"

case "${DEPLOY_MODE}" in
  clean|preserve)
    ;;
  *)
    log_error "moonraker-config: unsupported TREED_DEPLOY_MODE_EFFECTIVE=${DEPLOY_MODE} (allowed: clean|preserve)"
    exit 1
    ;;
esac

CONFIG_DEPLOYED=0
BASE_DEPLOYED=0
COMPONENT_DEPLOYED=0
if ! grp="$(pi_primary_group "${PI_USER}")"; then
  exit 1
fi

# Блок 3: Вспомогательные функции валидации/деплоя.
validate_repo_moonraker_layout() {
  if [ ! -f "${SRC_CONF}" ]; then
    log_error "Moonraker entry config not found in repo: ${SRC_CONF}"
    exit 1
  fi

  if [ ! -d "${SRC_BASE_DIR}" ]; then
    log_error "Moonraker base fragments directory not found in repo: ${SRC_BASE_DIR}"
    exit 1
  fi

  if [ -z "$(find "${SRC_BASE_DIR}" -maxdepth 1 -type f -name '*.conf' -print -quit 2>/dev/null)" ]; then
    log_error "Moonraker base fragments directory is empty: ${SRC_BASE_DIR}"
    exit 1
  fi

  if ! grep -qE '^\[include[[:space:]]+moonraker/base/\*\.conf\][[:space:]]*$' "${SRC_CONF}"; then
    log_error "Moonraker entry config is missing include [include moonraker/base/*.conf]: ${SRC_CONF}"
    exit 1
  fi

  if ! grep -qE '^\[include[[:space:]]+moonraker/generated/\*\.conf\][[:space:]]*$' "${SRC_CONF}"; then
    log_error "Moonraker entry config is missing include [include moonraker/generated/*.conf]: ${SRC_CONF}"
    exit 1
  fi
}

find_moonraker_components_dir() {
  local py_path=""
  local candidate=""

  # Сначала пробуем путь из уже запущенного процесса.
  py_path="$(ps -eo args 2>/dev/null | grep -Eo '/[^ ]*/moonraker/moonraker\.py' | head -n 1 || true)"
  if [ -n "${py_path}" ] && [ -f "${py_path}" ]; then
    candidate="$(dirname "${py_path}")/components"
    if [ -f "${candidate}/machine.py" ]; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  fi

  # Если процесс не запущен, разбираем ExecStart из systemd unit.
  py_path="$(
    systemctl cat moonraker.service 2>/dev/null \
      | sed -n 's/^ExecStart=//p' \
      | tr ' ' '\n' \
      | tr -d '"' \
      | tr -d "'" \
      | grep -E '/moonraker/moonraker\.py$' \
      | head -n 1 || true
  )"
  if [ -n "${py_path}" ] && [ -f "${py_path}" ]; then
    candidate="$(dirname "${py_path}")/components"
    if [ -f "${candidate}/machine.py" ]; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  fi

  # Типовые пути KIAUH/дистрибутива.
  for candidate in \
    "${PI_HOME}/moonraker/moonraker/components" \
    "/home/${PI_USER}/moonraker/moonraker/components" \
    "/usr/share/moonraker/moonraker/components" \
    "/opt/moonraker/moonraker/components"
  do
    if [ -f "${candidate}/machine.py" ]; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done

  # Финальный fallback-поиск с исключением пути текущего репозитория.
  candidate="$(
    find /home /usr /opt \
      -maxdepth 5 \
      -type f \
      -path '*/moonraker/components/machine.py' \
      ! -path "${REPO_DIR}/*" \
      2>/dev/null | head -n 1 || true
  )"
  if [ -n "${candidate}" ]; then
    dirname "${candidate}"
    return 0
  fi

  return 1
}

deploy_treed_shell_component() {
  local components_dir=""
  local dst=""

  if [ ! -f "${SRC_COMPONENT}" ]; then
    log_error "TreeD Moonraker component not found in repo: ${SRC_COMPONENT}"
    exit 1
  fi

  if ! components_dir="$(find_moonraker_components_dir)"; then
    log_error "Moonraker components directory not found; cannot deploy ${COMPONENT_NAME}"
    exit 1
  fi

  dst="${components_dir}/${COMPONENT_NAME}"
  cp -f "${SRC_COMPONENT}" "${dst}"
  if [[ "${components_dir}" == "/home/${PI_USER}/"* ]]; then
    chown "${PI_USER}:${grp}" "${dst}" || true
  fi
  COMPONENT_DEPLOYED=1
  log_info "Deployed Moonraker component to ${dst}"
}

is_valid_mainsail_web_path() {
  local candidate="$1"

  if [ -z "${candidate}" ]; then
    return 1
  fi
  if [ ! -d "${candidate}" ]; then
    return 1
  fi
  if [ ! -f "${candidate}/release_info.json" ]; then
    return 1
  fi
  return 0
}

resolve_mainsail_web_path() {
  local candidate=""
  local candidates=()

  if [ -n "${TREED_MAINSAIL_WEB_PATH}" ]; then
    candidates+=("${TREED_MAINSAIL_WEB_PATH}")
  fi
  candidates+=(
    "${PI_HOME}/mainsail"
    "${PI_HOME}/printer_data/www/mainsail"
    "${PI_HOME}/printer_data/www"
    "/var/www/mainsail"
    "/var/www/html/mainsail"
    "/usr/share/mainsail"
  )

  for candidate in "${candidates[@]}"; do
    if is_valid_mainsail_web_path "${candidate}"; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done
  return 1
}

disable_mainsail_updater_section() {
  local core_cfg="$1"
  local tmp=""

  tmp="$(mktemp)"
  awk '
    BEGIN { in_section = 0 }
    /^[[:space:]]*\[update_manager mainsail\][[:space:]]*$/ {
      in_section = 1
      print "# [update_manager mainsail]"
      next
    }
    in_section && /^[[:space:]]*\[[^]]+\][[:space:]]*$/ {
      in_section = 0
    }
    in_section {
      if ($0 ~ /^[[:space:]]*$/) {
        print
      } else if ($0 ~ /^[[:space:]]*#/) {
        print
      } else {
        print "# " $0
      }
      next
    }
    { print }
  ' "${core_cfg}" > "${tmp}"
  mv "${tmp}" "${core_cfg}"
}

configure_mainsail_updater_section() {
  local core_cfg="${DST_BASE_DIR}/00-core.conf"
  local mainsail_path=""
  local mainsail_path_escaped=""

  if [ ! -f "${core_cfg}" ]; then
    log_warn "moonraker-config: base core fragment not found, skip mainsail updater tuning"
    return 0
  fi

  if ! grep -qE '^[[:space:]]*\[update_manager mainsail\][[:space:]]*$' "${core_cfg}"; then
    log_warn "moonraker-config: [update_manager mainsail] section not found in ${core_cfg}"
    return 0
  fi

  if mainsail_path="$(resolve_mainsail_web_path)"; then
    mainsail_path_escaped="$(printf '%s' "${mainsail_path}" | sed 's|[&|]|\\&|g')"
    sed -i -E "/^[[:space:]]*\\[update_manager mainsail\\][[:space:]]*$/,/^[[:space:]]*\\[[^]]+\\][[:space:]]*$/ s|^([[:space:]]*path:[[:space:]]*).*$|\\1${mainsail_path_escaped}|" "${core_cfg}"
    log_info "moonraker-config: mainsail updater enabled (path=${mainsail_path})"
  else
    disable_mainsail_updater_section "${core_cfg}"
    log_warn "moonraker-config: mainsail updater disabled (no valid web path with release_info.json)"
  fi
}

render_base_fragment_templates() {
  local file=""
  local pi_home_escaped=""
  local pi_user_escaped=""

  pi_home_escaped="$(printf '%s' "${PI_HOME}" | sed 's|[&|]|\\&|g')"
  pi_user_escaped="$(printf '%s' "${PI_USER}" | sed 's|[&|]|\\&|g')"

  while IFS= read -r -d '' file; do
    sed -i \
      -e "s|{{PI_HOME}}|${pi_home_escaped}|g" \
      -e "s|{{PI_USER}}|${pi_user_escaped}|g" \
      "${file}"
  done < <(find "${DST_BASE_DIR}" -maxdepth 1 -type f -name '*.conf' -print0 2>/dev/null)
}

deploy_base_fragments() {
  rm -rf "${DST_BASE_DIR}"
  ensure_dir "${DST_BASE_DIR}"
  cp -a "${SRC_BASE_DIR}/." "${DST_BASE_DIR}/"
  render_base_fragment_templates
  configure_mainsail_updater_section
  chown -R "${PI_USER}:${grp}" "${DST_BASE_DIR}" || true
  BASE_DEPLOYED=1
  log_info "Deployed Moonraker base fragments to ${DST_BASE_DIR}"
}

prune_treed_generated_fragments() {
  local file=""

  # Оставляем только placeholder, остальные generated-фрагменты пересоздаются шагами loader.
  ensure_dir "${DST_GENERATED_DIR}"

  while IFS= read -r -d '' file; do
    case "$(basename "${file}")" in
      00-placeholder.conf) continue ;;
    esac

    rm -f "${file}"
    log_info "Removed stale generated fragment: ${file}"
  done < <(find "${DST_GENERATED_DIR}" -maxdepth 1 -type f -name '*.conf' -print0 2>/dev/null)
}

ensure_generated_fragments_dir() {
  local placeholder=""
  prune_treed_generated_fragments
  ensure_dir "${DST_GENERATED_DIR}"
  placeholder="${DST_GENERATED_DIR}/00-placeholder.conf"
  if [ ! -f "${placeholder}" ]; then
    cat > "${placeholder}" <<'EOF'
#### зарезервировано под moonraker-фрагменты, генерируемые loader
EOF
  fi
  chown -R "${PI_USER}:${grp}" "${DST_GENERATED_DIR}" || true
}

# Блок 4: Основной сценарий деплоя moonraker-конфига и компонентов.
validate_repo_moonraker_layout

if [ "${DEPLOY_MODE}" = "preserve" ]; then
  backup_file_once "${DST_CONF}"
else
  log_info "moonraker-config: clean mode, skip backup for ${DST_CONF}"
fi
cp -f "${SRC_CONF}" "${DST_CONF}"
chown "${PI_USER}:${grp}" "${DST_CONF}" || true
CONFIG_DEPLOYED=1
log_info "Deployed Moonraker config to ${DST_CONF}"

deploy_base_fragments
ensure_generated_fragments_dir
deploy_treed_shell_component

if [ "${CONFIG_DEPLOYED}" -eq 1 ] || [ "${BASE_DEPLOYED}" -eq 1 ] || [ "${COMPONENT_DEPLOYED}" -eq 1 ]; then
  log_info "Moonraker restart is deferred to step crowsnest-webcam"
fi

log_info "moonraker-config: OK"
