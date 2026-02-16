#!/bin/bash
set -euo pipefail

. "${REPO_DIR}/loader/lib/common.sh"
ensure_root

log_info "Step moonraker-config: syncing Moonraker config"

SRC_CONF="${REPO_DIR}/moonraker/moonraker.conf"
DST_CONF="${PI_HOME}/printer_data/config/moonraker.conf"
SRC_BASE_DIR="${REPO_DIR}/moonraker/base"
DST_BASE_DIR="${PI_HOME}/printer_data/config/moonraker/base"
DST_GENERATED_DIR="${PI_HOME}/printer_data/config/moonraker/generated"
SRC_COMPONENT="${REPO_DIR}/moonraker/components/treed_shell_command.py"
COMPONENT_NAME="treed_shell_command.py"
DEPLOY_MODE="${TREED_DEPLOY_MODE_EFFECTIVE:-preserve}"

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

  # Prefer the currently running process path.
  py_path="$(ps -eo args 2>/dev/null | grep -Eo '/[^ ]*/moonraker/moonraker\.py' | head -n 1 || true)"
  if [ -n "${py_path}" ] && [ -f "${py_path}" ]; then
    candidate="$(dirname "${py_path}")/components"
    if [ -f "${candidate}/machine.py" ]; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  fi

  # Fallback to systemd unit definition when process is not running.
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

  # Common KIAUH / distro locations.
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

  # Fallback discovery while excluding this repo path.
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

deploy_base_fragments() {
  rm -rf "${DST_BASE_DIR}"
  ensure_dir "${DST_BASE_DIR}"
  cp -a "${SRC_BASE_DIR}/." "${DST_BASE_DIR}/"
  chown -R "${PI_USER}:${grp}" "${DST_BASE_DIR}" || true
  BASE_DEPLOYED=1
  log_info "Deployed Moonraker base fragments to ${DST_BASE_DIR}"
}

prune_treed_generated_fragments() {
  local file=""

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
#### reserved for loader-generated moonraker fragments
EOF
  fi
  chown -R "${PI_USER}:${grp}" "${DST_GENERATED_DIR}" || true
}

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
