#!/bin/bash
set -euo pipefail

# ==========================================
# ШАГ LOADER: MOONRAKER CONFIG
# ==========================================
# Назначение:
# - Синхронизирует moonraker.conf, base-фрагменты и компоненты TreeD.
# - Учитывает deploy mode и режим backup/preserve.
# Контур:
# - required (база API/интеграций TreeD и generated-фрагментов).

# Блок 1: Библиотеки и root-права.
. "${REPO_DIR}/loader/lib/common.sh"
. "${REPO_DIR}/loader/lib/runtime-repo.sh"
ensure_root

# Блок 2: Старт шага и расчет путей/режима деплоя.
log_info "Step moonraker-config: syncing Moonraker config"

SRC_CONF="${REPO_DIR}/moonraker/moonraker.conf"
DST_CONF="${PI_HOME}/printer_data/config/moonraker.conf"
SRC_BASE_DIR="${REPO_DIR}/moonraker/base"
DST_BASE_DIR="${PI_HOME}/printer_data/config/moonraker/base"
DST_GENERATED_DIR="${PI_HOME}/printer_data/config/moonraker/generated"
SRC_COMPONENT_DIR="${REPO_DIR}/moonraker/components"
MOONRAKER_RUNTIME_DIR="${TREED_MOONRAKER_SRC_DIR:-${PI_HOME}/moonraker}"
MOONRAKER_COMPONENTS_DIR="${MOONRAKER_RUNTIME_DIR}/moonraker/components"
MOONRAKER_ENV_DIR="${TREED_MOONRAKER_ENV_DIR:-${PI_HOME}/moonraker-env}"
DEPLOY_MODE="${TREED_DEPLOY_MODE_EFFECTIVE:-preserve}"
TREED_MAINSAIL_WEB_PATH="${TREED_MAINSAIL_WEB_PATH:-/var/www/mainsail}"
TREED_CROWSNEST_SRC_DIR="${TREED_CROWSNEST_SRC_DIR:-${PI_HOME}/crowsnest}"
TREED_CROWSNEST_REPO="${TREED_CROWSNEST_REPO:-https://github.com/mainsail-crew/crowsnest.git}"
MOONRAKER_ASVC="${PI_HOME}/printer_data/moonraker.asvc"

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
UPDATE_COMMAND_DEPLOYED=0
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

  if [ ! -d "${SRC_COMPONENT_DIR}" ]; then
    log_error "Moonraker components directory not found in repo: ${SRC_COMPONENT_DIR}"
    exit 1
  fi

  if [ -z "$(find "${SRC_COMPONENT_DIR}" -maxdepth 1 -type f -name '*.py' -print -quit 2>/dev/null)" ]; then
    log_error "Moonraker components directory has no Python components: ${SRC_COMPONENT_DIR}"
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
  if [ -f "${MOONRAKER_COMPONENTS_DIR}/machine.py" ]; then
    printf '%s\n' "${MOONRAKER_COMPONENTS_DIR}"
    return 0
  fi

  return 1
}

deploy_treed_moonraker_components() {
  local components_dir=""
  local component_name=""
  local deployed_count=0
  local src=""
  local dst=""

  if ! components_dir="$(find_moonraker_components_dir)"; then
    log_error "Moonraker components directory not found; cannot deploy TreeD components"
    exit 1
  fi

  runtime_repo_add_excludes "${MOONRAKER_RUNTIME_DIR}" \
    '/moonraker/components/treed_*.py' \
    '/moonraker/components/__pycache__/treed_*.pyc'

  while IFS= read -r -d '' src; do
    component_name="$(basename "${src}")"
    dst="${components_dir}/${component_name}"
    cp -f "${src}" "${dst}"
    if [[ "${components_dir}" == "/home/${PI_USER}/"* ]]; then
      chown "${PI_USER}:${grp}" "${dst}" || true
    fi
    deployed_count=$((deployed_count+1))
    log_info "Deployed Moonraker component to ${dst}"
  done < <(find "${SRC_COMPONENT_DIR}" -maxdepth 1 -type f -name '*.py' -print0 2>/dev/null)

  if [ "${deployed_count}" -eq 0 ]; then
    log_error "No TreeD Moonraker components found in ${SRC_COMPONENT_DIR}"
    exit 1
  fi

  if [ ! -x "${MOONRAKER_ENV_DIR}/bin/python" ]; then
    log_error "Moonraker runtime Python not found: ${MOONRAKER_ENV_DIR}/bin/python"
    exit 1
  fi
  if ! "${MOONRAKER_ENV_DIR}/bin/python" -m py_compile "${components_dir}"/treed_*.py; then
    log_error "TreeD Moonraker components failed Python compile check in ${components_dir}"
    exit 1
  fi

  COMPONENT_DEPLOYED=1
}

restart_and_verify_moonraker_components() {
  local tmp=""
  local code=""
  local attempt=""
  local required_components=(treed_shell_command treed_detection treed_print_feedback treed_host_network treed_resonance treed_filament_sensor treed_update treed_recovery treed_autoprinter)
  local retries="${TREED_MOONRAKER_HTTP_RETRIES:-30}"

  systemctl restart moonraker.service
  tmp="$(mktemp '/tmp/treed_moonraker_components_XXXXXX.json')"
  for attempt in $(seq 1 "${retries}"); do
    code="$(curl -m 4 -sS -o "${tmp}" -w '%{http_code}' 'http://127.0.0.1:7125/server/info' || true)"
    if [ "${code}" = "200" ] && grep -qE '"components"[[:space:]]*:' "${tmp}"; then
      break
    fi
    sleep 1
  done

  if [ "${code}" != "200" ]; then
    log_error "Moonraker API not ready after component deploy (http=${code:-n/a})"
    rm -f "${tmp}"
    exit 1
  fi
  # Имя в failed_components не подтверждает загрузку; проверяем поля штатного JSON API.
  if ! "${MOONRAKER_ENV_DIR}/bin/python" - "${tmp}" "${required_components[@]}" <<'PY'
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as source:
        result = json.load(source)["result"]
    loaded = result["components"]
    failed = result["failed_components"]
    if not all(isinstance(items, list) and all(isinstance(item, str) for item in items)
               for items in (loaded, failed)):
        raise ValueError("invalid component lists")
except (OSError, KeyError, TypeError, ValueError) as error:
    raise SystemExit(f"Invalid Moonraker server/info: {error}")

required = set(sys.argv[2:])
unavailable = (required - set(loaded)) | (required & set(failed))
if unavailable:
    raise SystemExit("Moonraker components are not loaded: " + ", ".join(sorted(unavailable)))
PY
  then
    log_error "Moonraker required component verification failed"
    rm -f "${tmp}"
    exit 1
  fi
  rm -f "${tmp}"
  log_info "moonraker-config: all required TreeD components loaded"
}

install_update_file() {
  local mode="$1" source="$2" target="$3" temporary=""
  temporary="$(mktemp "${target}.XXXXXX")"
  install -m "${mode}" "${source}" "${temporary}"
  sync -f "${temporary}"
  mv -f "${temporary}" "${target}"
  sync -f "$(dirname "${target}")"
}

deploy_treed_update_command() {
  local apply_src="${REPO_DIR}/runtime-scripts/treed-update/treed-update-apply"
  local service_src="${REPO_DIR}/runtime-scripts/treed-update/treed-update-service"
  local core_src="${REPO_DIR}/runtime-scripts/treed-update/treed-core-update"
  local loading_src="${REPO_DIR}/runtime-scripts/treed-update/treed-loading-screen"
  local ab_src="${REPO_DIR}/runtime-scripts/treed-ab/treed-ab"
  local worker_unit_src="${REPO_DIR}/runtime-scripts/treed-update/treed-update.service"
  local path_unit_src="${REPO_DIR}/runtime-scripts/treed-update/treed-update.path"
  local recover_unit_src="${REPO_DIR}/runtime-scripts/treed-update/treed-update-recover.service"
  local env_file="/etc/default/treed-update"
  local sudoers_file="/etc/sudoers.d/treed-update"
  local src=""

  # Блок 3.1: Root-worker, runtime updater TreeD и отдельный fail-closed A/B probe.
  for src in "${apply_src}" "${service_src}" "${core_src}" "${loading_src}" "${ab_src}" "${worker_unit_src}" "${path_unit_src}" "${recover_unit_src}"; do
    if [ ! -f "${src}" ]; then
      log_error "treed update runtime file not found in repo: ${src}"
      exit 1
    fi
  done
  # В managed-режиме эти команды уже атомарно доставлены core-транзакцией.
  if [ "${TREED_UPDATE_MANAGED_LOADER:-0}" != "1" ]; then
    install_update_file 0755 "${apply_src}" /usr/local/sbin/treed-update-apply
    install_update_file 0755 "${service_src}" /usr/local/sbin/treed-update-service
    install_update_file 0755 "${core_src}" /usr/local/sbin/treed-core-update
    install_update_file 0755 "${loading_src}" /usr/local/sbin/treed-loading-screen
  fi
  install_update_file 0755 "${ab_src}" /usr/local/sbin/treed-ab
  install_update_file 0644 "${worker_unit_src}" /etc/systemd/system/treed-update.service
  install_update_file 0644 "${path_unit_src}" /etc/systemd/system/treed-update.path
  install_update_file 0644 "${recover_unit_src}" /etc/systemd/system/treed-update-recover.service
  install -d -m 0755 -o root -g root /var/lib/treed-update /var/log/treed-update

  # Блок 3.2: Устраняем только пустой каталог от прежнего MakeDirectory=yes.
  if [ -L /var/lib/treed-update/state.json ]; then
    log_error "treed update state must not be a symlink: /var/lib/treed-update/state.json"
    exit 1
  fi
  if [ -d /var/lib/treed-update/state.json ]; then
    systemctl stop treed-update.path
    if ! rmdir /var/lib/treed-update/state.json; then
      log_error "treed update state directory is not empty; refusing to remove its contents"
      exit 1
    fi
    log_warn "treed update: removed empty state.json directory created by the old path unit"
  fi
  systemctl daemon-reload
  systemctl enable treed-update-recover.service
  systemctl enable --now treed-update.path

  if [ "${TREED_UPDATE_MANAGED_LOADER:-0}" != "1" ] || [ ! -e "${env_file}" ]; then
  cat > "${env_file}" <<EOF
TREED_UPDATE_REPO_DIR="${PI_HOME}/treed/printer-core"
TREED_UPDATE_PI_USER="${PI_USER}"
TREED_UPDATE_PI_HOME="${PI_HOME}"
TREED_UPDATE_PI_GROUP="${PI_GROUP}"
TREED_UPDATE_STATE_FILE="/var/lib/treed-update/state.json"
TREED_UPDATE_LOG_FILE="/var/log/treed-update/worker.log"
TREED_UPDATE_LOCK_FILE="/run/lock/treed-update.lock"
TREED_UPDATE_UNIT="treed-update.service"
TREED_CORE_KLIPPER_DIR="${TREED_KLIPPER_SRC_DIR:-${PI_HOME}/klipper}"
TREED_CORE_MOONRAKER_DIR="${MOONRAKER_RUNTIME_DIR}"
TREED_SHELL_RUNTIME_DIR="${PI_HOME}/treed/treed-shell-runtime"
TREED_SHELL_SERVICE="treed-shell.service"
EOF
  chmod 0644 "${env_file}"
  else
    log_info "moonraker-config: preserving existing updater environment ${env_file}"
  fi

  cat > "${sudoers_file}" <<EOF
${PI_USER} ALL=(root) NOPASSWD: /usr/local/sbin/treed-update-service submit *
EOF
  chmod 0440 "${sudoers_file}"
  if command -v visudo >/dev/null 2>&1; then
    visudo -cf "${sudoers_file}" >/dev/null
  fi

  UPDATE_COMMAND_DEPLOYED=1
  log_info "moonraker-config: deployed /usr/local/sbin/treed-update-apply"
}

is_valid_mainsail_web_path() {
  local candidate="$1"
  if [ -z "${candidate}" ]; then
    return 1
  fi
  if [ ! -d "${candidate}" ]; then
    return 1
  fi
  if [ ! -f "${candidate}/index.html" ]; then
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
    "/var/www/mainsail"
    "/var/www/html/mainsail"
    "${PI_HOME}/printer_data/www/mainsail"
    "${PI_HOME}/printer_data/www"
    "${PI_HOME}/mainsail"
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

is_valid_crowsnest_repo_path() {
  local candidate="$1"
  if [ -z "${candidate}" ]; then
    return 1
  fi
  if [ ! -d "${candidate}/.git" ]; then
    return 1
  fi
  # Совместимость с legacy и v5:
  # - legacy: updater через tools/pkglist.sh
  # - v5: updater через system-dependencies.json + requirements.txt
  if [ -f "${candidate}/tools/pkglist.sh" ]; then
    return 0
  fi
  if [ -f "${candidate}/system-dependencies.json" ] && [ -f "${candidate}/requirements.txt" ]; then
    return 0
  fi
  return 1
}

resolve_crowsnest_repo_path() {
  local candidate=""
  local candidates=()

  if [ -n "${TREED_CROWSNEST_SRC_DIR}" ]; then
    candidates+=("${TREED_CROWSNEST_SRC_DIR}")
  fi
  candidates+=(
    "${PI_HOME}/crowsnest"
    "/home/${PI_USER}/crowsnest"
  )

  for candidate in "${candidates[@]}"; do
    if is_valid_crowsnest_repo_path "${candidate}"; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done
  return 1
}

disable_update_manager_section() {
  local core_cfg="$1"
  local section_name="$2"
  local tmp=""

  tmp="$(mktemp)"
  awk -v section_name="${section_name}" '
    BEGIN { in_section = 0 }
    $0 ~ "^[[:space:]]*\\[update_manager " section_name "\\][[:space:]]*$" {
      in_section = 1
      print "# [update_manager " section_name "]"
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

set_update_manager_section_option() {
  local core_cfg="$1"
  local section_name="$2"
  local option_name="$3"
  local option_value="$4"
  local option_value_escaped=""

  option_value_escaped="$(printf '%s' "${option_value}" | sed 's|[&|]|\\&|g')"
  sed -i -E "/^[[:space:]]*\\[update_manager ${section_name}\\][[:space:]]*$/,/^[[:space:]]*\\[[^]]+\\][[:space:]]*$/ s|^([[:space:]]*${option_name}:[[:space:]]*).*$|\\1${option_value_escaped}|" "${core_cfg}"
}

ensure_moonraker_allowed_service() {
  local service_name="$1"

  ensure_dir "$(dirname "${MOONRAKER_ASVC}")"
  if [ ! -f "${MOONRAKER_ASVC}" ]; then
    touch "${MOONRAKER_ASVC}"
  fi

  if grep -qE "^[[:space:]]*${service_name}([.]service)?[[:space:]]*$" "${MOONRAKER_ASVC}"; then
    return 0
  fi

  printf '%s\n' "${service_name}" >> "${MOONRAKER_ASVC}"
  chown "${PI_USER}:${grp}" "${MOONRAKER_ASVC}" || true
  log_info "moonraker-config: allowed service added (${service_name})"
}

configure_mainsail_updater_section() {
  local core_cfg="${DST_BASE_DIR}/00-core.conf"
  local mainsail_path=""

  if [ ! -f "${core_cfg}" ]; then
    log_warn "moonraker-config: base core fragment not found, skip mainsail updater tuning"
    return 0
  fi

  if ! grep -qE '^[[:space:]]*\[update_manager mainsail\][[:space:]]*$' "${core_cfg}"; then
    log_warn "moonraker-config: [update_manager mainsail] section not found in ${core_cfg}"
    return 0
  fi

  if mainsail_path="$(resolve_mainsail_web_path)"; then
    set_update_manager_section_option "${core_cfg}" "mainsail" "path" "${mainsail_path}"
    log_info "moonraker-config: mainsail updater enabled (path=${mainsail_path})"
  else
    disable_update_manager_section "${core_cfg}" "mainsail"
    log_warn "moonraker-config: mainsail updater disabled (no valid web path with release_info.json)"
  fi
}

configure_crowsnest_updater_section() {
  local core_cfg="${DST_BASE_DIR}/00-core.conf"
  local crowsnest_path=""

  if [ ! -f "${core_cfg}" ]; then
    log_warn "moonraker-config: base core fragment not found, skip crowsnest updater tuning"
    return 0
  fi

  if ! grep -qE '^[[:space:]]*\[update_manager crowsnest\][[:space:]]*$' "${core_cfg}"; then
    log_warn "moonraker-config: [update_manager crowsnest] section not found in ${core_cfg}"
    return 0
  fi

  if crowsnest_path="$(resolve_crowsnest_repo_path)"; then
    set_update_manager_section_option "${core_cfg}" "crowsnest" "path" "${crowsnest_path}"
    set_update_manager_section_option "${core_cfg}" "crowsnest" "origin" "${TREED_CROWSNEST_REPO}"
    ensure_moonraker_allowed_service "crowsnest"
    log_info "moonraker-config: crowsnest updater enabled (path=${crowsnest_path})"
  else
    disable_update_manager_section "${core_cfg}" "crowsnest"
    log_warn "moonraker-config: crowsnest updater disabled (no compatible git checkout metadata)"
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

merge_preserved_autoprinter_section() {
  python3 - "$1" "$2" <<'PY'
import re, sys
from pathlib import Path

old_path, new_path = map(Path, sys.argv[1:])
old_text, new_text = old_path.read_text(encoding="utf-8"), new_path.read_text(encoding="utf-8")
old, new = old_text.splitlines(), new_text.splitlines()
section = re.compile(r"^\s*\[treed_autoprinter\]\s*$", re.I)
header = re.compile(r"^\s*\[[^]]+\]\s*$")
legacy_keys = {"enabled", "execution_enabled", "bed_check_enabled"}

def body(lines):
    for start, line in enumerate(lines):
        if section.fullmatch(line):
            end = start + 1
            while end < len(lines) and not header.fullmatch(lines[end]):
                end += 1
            return lines[start], lines[start + 1:end]
    return None, []

def without_legacy_keys(lines):
    result = []
    for line in lines:
        match = re.match(r"^\s*([A-Za-z0-9_]+)\s*[:=]", line)
        if match and match.group(1).lower() in legacy_keys:
            continue
        result.append(line)
    return result

old_header, old_body = body(old)
new_header, new_body = body(new)
original_old_body = old_body
old_body = without_legacy_keys(old_body)
new_body = without_legacy_keys(new_body)
if old_text == new_text and old_body == original_old_body:
    raise SystemExit(0)
if old_header and new_header:
    keys = set()
    for line in old_body:
        match = re.match(r"^\s*([A-Za-z0-9_]+)\s*[:=]", line)
        if match:
            keys.add(match.group(1).lower())
    additions = []
    for line in new_body:
        match = re.match(r"^\s*([A-Za-z0-9_]+)\s*[:=]", line)
        if match and match.group(1).lower() not in keys:
            additions.append(line)
            keys.add(match.group(1).lower())
    merged = old_body + additions
    start = next(i for i, line in enumerate(new) if section.fullmatch(line))
    end = start + 1
    while end < len(new) and not header.fullmatch(new[end]):
        end += 1
    new[start:end] = [new_header, *merged]
    new_path.write_text("\n".join(new) + "\n", encoding="utf-8")
PY
}


deploy_base_fragments() {
  local preserved_core=""
  local recovery_core="${DST_BASE_DIR}.preserve-core.conf"
  if [ "${DEPLOY_MODE}" = "clean" ]; then
    rm -f "${recovery_core}"
  fi
  if [ "${DEPLOY_MODE}" = "preserve" ]; then
    if [ -L "${recovery_core}" ] || { [ -e "${recovery_core}" ] && [ ! -f "${recovery_core}" ]; }; then
      log_error "moonraker-config: invalid recovery file ${recovery_core}"
      return 1
    fi
    if [ -f "${recovery_core}" ]; then
      preserved_core="${recovery_core}"
    elif [ -e "${DST_BASE_DIR}/00-core.conf" ] || [ -L "${DST_BASE_DIR}/00-core.conf" ]; then
      if [ -L "${DST_BASE_DIR}/00-core.conf" ] || [ ! -f "${DST_BASE_DIR}/00-core.conf" ]; then
        log_error "moonraker-config: refusing non-regular core config"
        return 1
      fi
      preserved_core="$(mktemp "${recovery_core}.XXXXXX")"
      cp -p "${DST_BASE_DIR}/00-core.conf" "${preserved_core}"
      sync -f "${preserved_core}"
      mv "${preserved_core}" "${recovery_core}"
      preserved_core="${recovery_core}"
      sync -f "$(dirname "${recovery_core}")"
    fi
  fi
  rm -rf "${DST_BASE_DIR}"
  ensure_dir "${DST_BASE_DIR}"
  cp -a "${SRC_BASE_DIR}/." "${DST_BASE_DIR}/"
  render_base_fragment_templates
  if [ -n "${preserved_core}" ]; then
    merge_preserved_autoprinter_section "${preserved_core}" "${DST_BASE_DIR}/00-core.conf"
    sync -f "${DST_BASE_DIR}"
    rm -f "${preserved_core}"
    sync -f "$(dirname "${preserved_core}")"
  fi
  configure_mainsail_updater_section
  configure_crowsnest_updater_section
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
if [ "${TREED_UPDATE_MANAGED_LOADER:-0}" = "1" ]; then
  # Файлы и контрольные суммы уже подготовил вызывающий updater, в том числе старой версии.
  ensure_moonraker_allowed_service "treed-shell"
  deploy_treed_update_command
  restart_and_verify_moonraker_components
  log_info "moonraker-config: managed runtime already installed; preserved local configs"
  exit 0
fi

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
ensure_moonraker_allowed_service "treed-shell"
ensure_generated_fragments_dir
deploy_treed_moonraker_components
deploy_treed_update_command

restart_and_verify_moonraker_components

log_info "moonraker-config: OK"
