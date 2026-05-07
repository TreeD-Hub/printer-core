#!/bin/bash
set -euo pipefail

# ==========================================
# ШАГ LOADER: RUNTIME BOOTSTRAP
# ==========================================
# Назначение:
# - Подготавливает базовый runtime-контур Klipper/Moonraker на "чистой" системе.
# - Создает/обновляет venv, systemd unit-файлы и минимальные runtime-каталоги.
# Контур:
# - required (без bootstrap не поднимутся required-сервисы maintenance-start/verify).

# Блок 1: Библиотеки и root-права.
. "${REPO_DIR}/loader/lib/common.sh"
ensure_root

# Блок 2: Контракт пользователя/режима bootstrap.
if [ -z "${PI_USER:-}" ] || [ -z "${PI_HOME:-}" ]; then
  log_error "runtime-bootstrap: PI_USER/PI_HOME is not set"
  exit 1
fi

if ! PI_GROUP="$(pi_primary_group "${PI_USER}")"; then
  exit 1
fi

TREED_RUNTIME_BOOTSTRAP="${TREED_RUNTIME_BOOTSTRAP:-1}"
if [ "${TREED_RUNTIME_BOOTSTRAP}" != "1" ]; then
  log_info "runtime-bootstrap: skipped (TREED_RUNTIME_BOOTSTRAP=${TREED_RUNTIME_BOOTSTRAP})"
  exit 0
fi

# Блок 3: Нормализация путей/репозиториев runtime-контуров.
KLIPPER_DIR="${TREED_KLIPPER_SRC_DIR:-${PI_HOME}/klipper}"
KLIPPER_REPO="${TREED_KLIPPER_REPO:-https://github.com/Klipper3d/klipper.git}"
KLIPPER_REF="${TREED_KLIPPER_REF:-}"
KLIPPY_ENV_DIR="${TREED_KLIPPY_ENV_DIR:-${PI_HOME}/klippy-env}"
TREED_MAIN_MCU_CANBUS_UUID="${TREED_MAIN_MCU_CANBUS_UUID:-d372e54bf965}"
TREED_CAN_IFACE="${TREED_CAN_IFACE:-can0}"
TREED_EBB_CANBUS_UUID="${TREED_EBB_CANBUS_UUID:-efaf957ab20f}"
TREED_EDDY_ENABLED="${TREED_EDDY_ENABLED:-1}"
TREED_EDDY_CANBUS_UUID="${TREED_EDDY_CANBUS_UUID:-95485b93332a}"
TREED_KLIPPER_PREFLIGHT="${TREED_KLIPPER_PREFLIGHT:-1}"
TREED_KLIPPER_PREFLIGHT_WAIT_SEC="${TREED_KLIPPER_PREFLIGHT_WAIT_SEC:-12}"
TREED_KLIPPER_PREFLIGHT_INTERVAL_SEC="${TREED_KLIPPER_PREFLIGHT_INTERVAL_SEC:-1}"
TREED_KLIPPER_PREFLIGHT_CAN_UUIDS_REQUIRED="${TREED_KLIPPER_PREFLIGHT_CAN_UUIDS_REQUIRED:-0}"
MOONRAKER_DIR="${TREED_MOONRAKER_SRC_DIR:-${PI_HOME}/moonraker}"
MOONRAKER_ENV_DIR="${TREED_MOONRAKER_ENV_DIR:-${PI_HOME}/moonraker-env}"
MOONRAKER_REPO="${TREED_MOONRAKER_REPO:-https://github.com/Arksine/moonraker.git}"
MOONRAKER_REF="${TREED_MOONRAKER_REF:-}"
MOONRAKER_POLKIT_SETUP="${TREED_MOONRAKER_POLKIT_SETUP:-1}"
MOONRAKER_POLKIT_REQUIRED="${TREED_MOONRAKER_POLKIT_REQUIRED:-0}"
MOONRAKER_RECREATE="${TREED_MOONRAKER_RECREATE:-0}"
CROWSNEST_DIR="${TREED_CROWSNEST_SRC_DIR:-${PI_HOME}/crowsnest}"
CROWSNEST_REPO="${TREED_CROWSNEST_REPO:-https://github.com/mainsail-crew/crowsnest.git}"
CROWSNEST_REF="${TREED_CROWSNEST_REF:-}"
CROWSNEST_INSTALL="${TREED_CROWSNEST_INSTALL:-1}"
CROWSNEST_RECREATE="${TREED_CROWSNEST_RECREATE:-0}"
CROWSNEST_UPDATE="${TREED_CROWSNEST_UPDATE:-1}"

PRINTER_DATA_DIR="${PI_HOME}/printer_data"
PRINTER_CFG_DIR="${PRINTER_DATA_DIR}/config"
PRINTER_LOG_DIR="${PRINTER_DATA_DIR}/logs"
PRINTER_GCODE_DIR="${PRINTER_DATA_DIR}/gcodes"
PRINTER_COMMS_DIR="${PRINTER_DATA_DIR}/comms"
KLIPPY_API_SOCK="${PRINTER_COMMS_DIR}/klippy.sock"
CROWSNEST_ENV_FILE="${PRINTER_DATA_DIR}/systemd/crowsnest.env"
CROWSNEST_VENV_DIR="${PI_HOME}/crowsnest-env"
KLIPPER_PREFLIGHT_ENV_FILE="/etc/default/treed-klipper-preflight"
KLIPPER_PREFLIGHT_SCRIPT="/usr/local/sbin/treed-klipper-preflight.sh"

# Блок 4: Вспомогательные функции (run-as-user, clone/update, venv, requirements).
run_as_pi() {
  local cmd="$1"
  sudo -u "${PI_USER}" -H bash -lc "${cmd}"
}

refresh_repo_metadata() {
  local repo_dir="$1"
  local is_shallow=""

  is_shallow="$(run_as_pi "set -euo pipefail; cd '${repo_dir}'; git rev-parse --is-shallow-repository 2>/dev/null || printf 'false'")"
  if [ "${is_shallow}" = "true" ]; then
    # Moonraker определяет версии через git describe; shallow-история часто не содержит ближайший semver-tag.
    if run_as_pi "set -euo pipefail; cd '${repo_dir}'; git fetch --unshallow --tags --prune origin >/dev/null 2>&1"; then
      return 0
    fi
    log_warn "runtime-bootstrap: failed to unshallow ${repo_dir}, falling back to tag fetch"
  fi

  run_as_pi "set -euo pipefail; cd '${repo_dir}'; git fetch --tags --prune origin >/dev/null 2>&1 || true"
}

ensure_repo_present() {
  local repo_dir="$1"
  local repo_url="$2"
  local repo_ref="$3"

  if [ "${repo_dir}" = "${MOONRAKER_DIR}" ] && [ "${MOONRAKER_RECREATE}" = "1" ]; then
    log_warn "runtime-bootstrap: forcing moonraker repo recreate (${repo_dir})"
    rm -rf "${repo_dir}"
  fi

  if [ "${repo_dir}" = "${CROWSNEST_DIR}" ] && [ "${CROWSNEST_RECREATE}" = "1" ]; then
    log_warn "runtime-bootstrap: forcing crowsnest repo recreate (${repo_dir})"
    rm -rf "${repo_dir}"
  fi

  if [ -d "${repo_dir}" ] && [ ! -d "${repo_dir}/.git" ]; then
    log_warn "runtime-bootstrap: ${repo_dir} exists without .git, recreating from ${repo_url}"
    rm -rf "${repo_dir}"
  fi

  if [ -d "${repo_dir}/.git" ]; then
    if ! run_as_pi "set -euo pipefail; cd '${repo_dir}'; git remote get-url origin >/dev/null 2>&1"; then
      log_warn "runtime-bootstrap: ${repo_dir} has no origin remote, recreating from ${repo_url}"
      rm -rf "${repo_dir}"
    else
      refresh_repo_metadata "${repo_dir}"
    fi
  fi

  if [ -d "${repo_dir}/.git" ]; then
    if [ -n "${repo_ref}" ]; then
      run_as_pi "set -euo pipefail; cd '${repo_dir}'; git fetch --tags --prune; git checkout '${repo_ref}'"
    fi
    return 0
  fi

  ensure_dir "$(dirname "${repo_dir}")"
  run_as_pi "set -euo pipefail; git clone '${repo_url}' '${repo_dir}'"
  if [ -n "${repo_ref}" ]; then
    run_as_pi "set -euo pipefail; cd '${repo_dir}'; git fetch --tags --prune; git checkout '${repo_ref}'"
  fi
}

ensure_python_venv() {
  local env_dir="$1"
  local req_file="$2"

  if [ ! -d "${env_dir}" ]; then
    run_as_pi "set -euo pipefail; python3 -m venv '${env_dir}'"
  fi

  run_as_pi "set -euo pipefail; '${env_dir}/bin/python' -m pip install --upgrade pip setuptools wheel"
  if ! run_as_pi "set -euo pipefail; '${env_dir}/bin/python' -c 'import pkg_resources' >/dev/null 2>&1"; then
    log_warn "runtime-bootstrap: pkg_resources is missing in ${env_dir}, pinning setuptools<81"
    run_as_pi "set -euo pipefail; '${env_dir}/bin/python' -m pip install 'setuptools<81'"
  fi
  if ! run_as_pi "set -euo pipefail; '${env_dir}/bin/python' -c 'import pkg_resources' >/dev/null 2>&1"; then
    log_error "runtime-bootstrap: pkg_resources is still missing in ${env_dir}"
    exit 1
  fi
  run_as_pi "set -euo pipefail; '${env_dir}/bin/pip' install -r '${req_file}'"
}

install_klipper_preflight() {
  ensure_dir "$(dirname "${KLIPPER_PREFLIGHT_SCRIPT}")"

  cat > "${KLIPPER_PREFLIGHT_ENV_FILE}" <<EOF
TREED_KLIPPER_PREFLIGHT=${TREED_KLIPPER_PREFLIGHT}
TREED_KLIPPER_PREFLIGHT_WAIT_SEC=${TREED_KLIPPER_PREFLIGHT_WAIT_SEC}
TREED_KLIPPER_PREFLIGHT_INTERVAL_SEC=${TREED_KLIPPER_PREFLIGHT_INTERVAL_SEC}
TREED_KLIPPER_PREFLIGHT_CAN_UUIDS_REQUIRED=${TREED_KLIPPER_PREFLIGHT_CAN_UUIDS_REQUIRED}
TREED_MAIN_MCU_CANBUS_UUID=${TREED_MAIN_MCU_CANBUS_UUID}
TREED_CAN_IFACE=${TREED_CAN_IFACE}
TREED_EBB_CANBUS_UUID=${TREED_EBB_CANBUS_UUID}
TREED_EDDY_ENABLED=${TREED_EDDY_ENABLED}
TREED_EDDY_CANBUS_UUID=${TREED_EDDY_CANBUS_UUID}
KLIPPER_DIR=${KLIPPER_DIR}
KLIPPY_ENV_DIR=${KLIPPY_ENV_DIR}
EOF
  chmod 0644 "${KLIPPER_PREFLIGHT_ENV_FILE}"

  cat > "${KLIPPER_PREFLIGHT_SCRIPT}" <<'EOF'
#!/bin/bash
set -euo pipefail

# ==========================================
# RUNTIME PREFLIGHT: KLIPPER START
# ==========================================
# Назначение:
# - Перед стартом Klipper ждет фактическую готовность CAN-интерфейса и CAN MCU.
# - CAN UUID readiness is diagnostic by default; strict mode is opt-in.
# - Заменяет фиксированный sleep на condition-based ожидание с ранним выходом.
# Контур:
# - required для стабильного холодного включения V2.

ENV_FILE="/etc/default/treed-klipper-preflight"
if [ -f "${ENV_FILE}" ]; then
  # shellcheck disable=SC1090
  . "${ENV_FILE}"
fi

TREED_KLIPPER_PREFLIGHT="${TREED_KLIPPER_PREFLIGHT:-1}"
TREED_KLIPPER_PREFLIGHT_WAIT_SEC="${TREED_KLIPPER_PREFLIGHT_WAIT_SEC:-12}"
TREED_KLIPPER_PREFLIGHT_INTERVAL_SEC="${TREED_KLIPPER_PREFLIGHT_INTERVAL_SEC:-1}"
TREED_KLIPPER_PREFLIGHT_CAN_UUIDS_REQUIRED="${TREED_KLIPPER_PREFLIGHT_CAN_UUIDS_REQUIRED:-0}"
TREED_MAIN_MCU_CANBUS_UUID="${TREED_MAIN_MCU_CANBUS_UUID:-}"
TREED_CAN_IFACE="${TREED_CAN_IFACE:-can0}"
TREED_EBB_CANBUS_UUID="${TREED_EBB_CANBUS_UUID:-}"
TREED_EDDY_ENABLED="${TREED_EDDY_ENABLED:-1}"
TREED_EDDY_CANBUS_UUID="${TREED_EDDY_CANBUS_UUID:-}"
KLIPPER_DIR="${KLIPPER_DIR:-/home/pi/klipper}"
KLIPPY_ENV_DIR="${KLIPPY_ENV_DIR:-/home/pi/klippy-env}"

log_info() {
  echo "[klipper-preflight] $*"
}

log_error() {
  echo "[klipper-preflight] ERROR: $*" >&2
}

is_positive_int() {
  case "${1:-}" in
    ''|*[!0-9]*) return 1 ;;
  esac
  [ "$1" -gt 0 ]
}

wait_until_deadline() {
  local check_cmd="$1"
  local success_msg="$2"
  local error_msg="$3"
  local deadline="$4"

  while true; do
    if eval "${check_cmd}"; then
      log_info "${success_msg}"
      return 0
    fi
    if [ "${SECONDS}" -ge "${deadline}" ]; then
      log_error "${error_msg}"
      return 1
    fi
    sleep "${TREED_KLIPPER_PREFLIGHT_INTERVAL_SEC}"
  done
}

if [ "${TREED_KLIPPER_PREFLIGHT}" != "1" ]; then
  log_info "skipped (TREED_KLIPPER_PREFLIGHT=${TREED_KLIPPER_PREFLIGHT})"
  exit 0
fi

if ! is_positive_int "${TREED_KLIPPER_PREFLIGHT_WAIT_SEC}"; then
  log_error "TREED_KLIPPER_PREFLIGHT_WAIT_SEC must be positive integer, got: ${TREED_KLIPPER_PREFLIGHT_WAIT_SEC}"
  exit 1
fi
if ! is_positive_int "${TREED_KLIPPER_PREFLIGHT_INTERVAL_SEC}"; then
  log_error "TREED_KLIPPER_PREFLIGHT_INTERVAL_SEC must be positive integer, got: ${TREED_KLIPPER_PREFLIGHT_INTERVAL_SEC}"
  exit 1
fi
case "${TREED_KLIPPER_PREFLIGHT_CAN_UUIDS_REQUIRED}" in
  0|1) ;;
  *)
    log_error "TREED_KLIPPER_PREFLIGHT_CAN_UUIDS_REQUIRED must be 0 or 1, got: ${TREED_KLIPPER_PREFLIGHT_CAN_UUIDS_REQUIRED}"
    exit 1
    ;;
esac

deadline=$((SECONDS + TREED_KLIPPER_PREFLIGHT_WAIT_SEC))

IP_BIN="$(command -v ip || true)"
if [ -z "${IP_BIN}" ]; then
  if [ "${TREED_KLIPPER_PREFLIGHT_CAN_UUIDS_REQUIRED}" = "1" ]; then
    log_error "ip command not found"
    exit 1
  fi
  log_info "CAN interface readiness check unavailable, ip command not found"
  exit 0
fi

if [ "${TREED_KLIPPER_PREFLIGHT_CAN_UUIDS_REQUIRED}" = "1" ]; then
  wait_until_deadline \
    "\"${IP_BIN}\" link show '${TREED_CAN_IFACE}' >/dev/null 2>&1" \
    "CAN interface is present (${TREED_CAN_IFACE})" \
    "CAN interface is missing after ${TREED_KLIPPER_PREFLIGHT_WAIT_SEC}s (${TREED_CAN_IFACE})" \
    "${deadline}"

  wait_until_deadline \
    "\"${IP_BIN}\" link show '${TREED_CAN_IFACE}' 2>/dev/null | grep -q '<[^>]*UP[^>]*>'" \
    "CAN interface is UP (${TREED_CAN_IFACE})" \
    "CAN interface is not UP after ${TREED_KLIPPER_PREFLIGHT_WAIT_SEC}s (${TREED_CAN_IFACE})" \
    "${deadline}"
else
  if "${IP_BIN}" link show "${TREED_CAN_IFACE}" >/dev/null 2>&1; then
    log_info "CAN interface is present (${TREED_CAN_IFACE})"
  else
    log_info "CAN interface is missing (${TREED_CAN_IFACE}, non-blocking)"
  fi

  if "${IP_BIN}" link show "${TREED_CAN_IFACE}" 2>/dev/null | grep -q '<[^>]*UP[^>]*>'; then
    log_info "CAN interface is UP (${TREED_CAN_IFACE})"
  else
    log_info "CAN interface is not UP (${TREED_CAN_IFACE}, non-blocking)"
  fi
fi

if [ -z "${TREED_MAIN_MCU_CANBUS_UUID}" ]; then
  if [ "${TREED_KLIPPER_PREFLIGHT_CAN_UUIDS_REQUIRED}" = "1" ]; then
    log_error "TREED_MAIN_MCU_CANBUS_UUID is empty"
    exit 1
  fi
  log_info "TREED_MAIN_MCU_CANBUS_UUID is empty; CAN UUID readiness check skipped"
  exit 0
fi

if [ -z "${TREED_EBB_CANBUS_UUID}" ]; then
  if [ "${TREED_KLIPPER_PREFLIGHT_CAN_UUIDS_REQUIRED}" = "1" ]; then
    log_error "TREED_EBB_CANBUS_UUID is empty"
    exit 1
  fi
  log_info "TREED_EBB_CANBUS_UUID is empty; CAN UUID readiness check skipped"
  exit 0
fi

required_uuids=("${TREED_MAIN_MCU_CANBUS_UUID}" "${TREED_EBB_CANBUS_UUID}")
if [ "${TREED_EDDY_ENABLED}" = "1" ]; then
  if [ -z "${TREED_EDDY_CANBUS_UUID}" ]; then
    if [ "${TREED_KLIPPER_PREFLIGHT_CAN_UUIDS_REQUIRED}" = "1" ]; then
      log_error "TREED_EDDY_CANBUS_UUID is empty while TREED_EDDY_ENABLED=1"
      exit 1
    fi
    log_info "TREED_EDDY_CANBUS_UUID is empty while TREED_EDDY_ENABLED=1; CAN UUID readiness check skipped"
    exit 0
  fi
  required_uuids+=("${TREED_EDDY_CANBUS_UUID}")
fi

PY_BIN="${KLIPPY_ENV_DIR}/bin/python"
QUERY_SCRIPT="${KLIPPER_DIR}/scripts/canbus_query.py"
if [ ! -x "${PY_BIN}" ]; then
  if [ "${TREED_KLIPPER_PREFLIGHT_CAN_UUIDS_REQUIRED}" = "0" ]; then
    log_info "CAN UUID readiness check unavailable, python runtime not executable: ${PY_BIN}"
    exit 0
  fi
  log_error "python runtime not found or not executable: ${PY_BIN}"
  exit 1
fi
if [ ! -f "${QUERY_SCRIPT}" ]; then
  if [ "${TREED_KLIPPER_PREFLIGHT_CAN_UUIDS_REQUIRED}" = "0" ]; then
    log_info "CAN UUID readiness check unavailable, canbus_query.py not found: ${QUERY_SCRIPT}"
    exit 0
  fi
  log_error "canbus_query.py not found: ${QUERY_SCRIPT}"
  exit 1
fi
QUERY_TIMEOUT_BIN="$(command -v timeout || true)"

run_canbus_query() {
  if [ -n "${QUERY_TIMEOUT_BIN}" ]; then
    "${QUERY_TIMEOUT_BIN}" 2 "${PY_BIN}" "${QUERY_SCRIPT}" "${TREED_CAN_IFACE}"
  else
    "${PY_BIN}" "${QUERY_SCRIPT}" "${TREED_CAN_IFACE}"
  fi
}

if [ "${TREED_KLIPPER_PREFLIGHT_CAN_UUIDS_REQUIRED}" = "0" ]; then
  query_output="$(run_canbus_query 2>&1 || true)"
  missing=""
  for uuid in "${required_uuids[@]}"; do
    if ! printf '%s\n' "${query_output}" | grep -Eiq "(^|[^0-9A-Fa-f])${uuid}([^0-9A-Fa-f]|$)"; then
      missing="${missing} ${uuid}"
    fi
  done
  if [ -z "${missing}" ]; then
    log_info "CAN MCU ready:${required_uuids[*]}"
  else
    log_info "CAN MCU readiness not confirmed, missing:${missing} (non-blocking)"
    printf '%s\n' "${query_output}" >&2
  fi
  exit 0
fi

while true; do
  query_output="$(run_canbus_query 2>&1 || true)"
  missing=""
  for uuid in "${required_uuids[@]}"; do
    if ! printf '%s\n' "${query_output}" | grep -Eiq "(^|[^0-9A-Fa-f])${uuid}([^0-9A-Fa-f]|$)"; then
      missing="${missing} ${uuid}"
    fi
  done

  if [ -z "${missing}" ]; then
    log_info "CAN MCU ready:${required_uuids[*]}"
    exit 0
  fi

  if [ "${SECONDS}" -ge "${deadline}" ]; then
    if [ "${TREED_KLIPPER_PREFLIGHT_CAN_UUIDS_REQUIRED}" = "1" ]; then
      log_error "CAN MCU not ready after ${TREED_KLIPPER_PREFLIGHT_WAIT_SEC}s, missing:${missing}"
      printf '%s\n' "${query_output}" >&2
      exit 1
    fi
    log_info "CAN MCU readiness not confirmed after ${TREED_KLIPPER_PREFLIGHT_WAIT_SEC}s, missing:${missing} (non-blocking)"
    printf '%s\n' "${query_output}" >&2
    exit 0
  fi

  sleep "${TREED_KLIPPER_PREFLIGHT_INTERVAL_SEC}"
done
EOF
  chmod 0755 "${KLIPPER_PREFLIGHT_SCRIPT}"
}

install_moonraker_policykit_rules() {
  local script_path="${MOONRAKER_DIR}/scripts/set-policykit-rules.sh"

  if [ "${MOONRAKER_POLKIT_SETUP}" != "1" ]; then
    log_info "runtime-bootstrap: moonraker policykit setup skipped (TREED_MOONRAKER_POLKIT_SETUP=${MOONRAKER_POLKIT_SETUP})"
    return 0
  fi

  if [ ! -f "${script_path}" ]; then
    if [ "${MOONRAKER_POLKIT_REQUIRED}" = "1" ]; then
      log_error "runtime-bootstrap: policykit script is missing: ${script_path}"
      exit 1
    fi
    log_warn "runtime-bootstrap: policykit script is missing, skip setup (${script_path})"
    return 0
  fi

  if [ ! -x "${script_path}" ]; then
    chmod 0755 "${script_path}"
  fi

  if ! command -v pkaction >/dev/null 2>&1; then
    if [ "${MOONRAKER_POLKIT_REQUIRED}" = "1" ]; then
      log_error "runtime-bootstrap: pkaction is missing, cannot install policykit rules"
      exit 1
    fi
    log_warn "runtime-bootstrap: pkaction is missing, skip policykit rules install"
    return 0
  fi

  if USER="${PI_USER}" "${script_path}" -r -z >/dev/null 2>&1; then
    log_info "runtime-bootstrap: moonraker policykit rules installed for user ${PI_USER}"
  else
    if [ "${MOONRAKER_POLKIT_REQUIRED}" = "1" ]; then
      log_error "runtime-bootstrap: failed to install moonraker policykit rules (script=${script_path})"
      exit 1
    fi
    log_warn "runtime-bootstrap: failed to install moonraker policykit rules, continuing"
  fi
}

update_crowsnest_repo() {
  if [ "${CROWSNEST_UPDATE}" != "1" ] || [ -n "${CROWSNEST_REF}" ]; then
    return 0
  fi

  run_as_pi "set -euo pipefail; cd '${CROWSNEST_DIR}'; branch=\"\$(git rev-parse --abbrev-ref HEAD)\"; if [ \"\${branch}\" != HEAD ]; then git pull --ff-only origin \"\${branch}\"; fi"
}

crowsnest_runtime_ready() {
  local unit_dump=""

  unit_dump="$(systemctl cat crowsnest.service 2>/dev/null || true)"
  if [ -z "${unit_dump}" ]; then
    return 1
  fi
  if ! printf '%s\n' "${unit_dump}" | grep -F "EnvironmentFile=${CROWSNEST_ENV_FILE}" >/dev/null; then
    return 1
  fi
  if ! printf '%s\n' "${unit_dump}" | grep -F "ExecStart=${CROWSNEST_VENV_DIR}/bin/python3" >/dev/null; then
    return 1
  fi
  if [ ! -f "${CROWSNEST_ENV_FILE}" ]; then
    return 1
  fi
  if [ ! -x "${CROWSNEST_VENV_DIR}/bin/python3" ]; then
    return 1
  fi
}

ensure_crowsnest_runtime() {
  local installer_log="${PRINTER_LOG_DIR}/crowsnest-install.log"

  case "${CROWSNEST_INSTALL}" in
    0)
      log_info "runtime-bootstrap: crowsnest install skipped (TREED_CROWSNEST_INSTALL=0)"
      return 0
      ;;
    1) ;;
    *)
      log_error "runtime-bootstrap: TREED_CROWSNEST_INSTALL must be 0 or 1, got: ${CROWSNEST_INSTALL}"
      exit 1
      ;;
  esac

  ensure_repo_present "${CROWSNEST_DIR}" "${CROWSNEST_REPO}" "${CROWSNEST_REF}"
  update_crowsnest_repo

  if [ ! -f "${CROWSNEST_DIR}/Makefile" ] || [ ! -f "${CROWSNEST_DIR}/tools/install.sh" ]; then
    log_error "runtime-bootstrap: Crowsnest installer not found in ${CROWSNEST_DIR}"
    exit 1
  fi

  if crowsnest_runtime_ready && [ "${CROWSNEST_UPDATE}" != "1" ]; then
    log_info "runtime-bootstrap: crowsnest systemd runtime present"
    return 0
  fi

  log_info "runtime-bootstrap: installing/updating Crowsnest (log=${installer_log})"
  (
    cd "${CROWSNEST_DIR}"
    env \
      SUDO_USER="${PI_USER}" \
      BASE_USER="${PI_USER}" \
      CROWSNEST_UNATTENDED=1 \
      CROWSNEST_ADD_CROWSNEST_MOONRAKER=0 \
      CROWSNEST_SKIP_REBOOT_PROMPT=1 \
      CROWSNEST_CONFIG_PATH="${PRINTER_CFG_DIR}" \
      CROWSNEST_LOG_PATH="${PRINTER_LOG_DIR}" \
      CROWSNEST_ENV_PATH="${PRINTER_DATA_DIR}/systemd" \
      DEBIAN_FRONTEND="${DEBIAN_FRONTEND:-noninteractive}" \
      make install
  ) > "${installer_log}" 2>&1 || {
    log_error "runtime-bootstrap: Crowsnest install failed (see ${installer_log})"
    exit 1
  }

  systemctl daemon-reload

  if ! systemctl cat crowsnest.service >/dev/null 2>&1; then
    log_error "runtime-bootstrap: crowsnest.service is missing after install"
    exit 1
  fi
  if ! crowsnest_runtime_ready; then
    log_error "runtime-bootstrap: crowsnest systemd runtime is incomplete after install (expected service/env/venv)"
    exit 1
  fi

  log_info "runtime-bootstrap: crowsnest runtime ready"
}

# Блок 5: Минимальные runtime-каталоги и права.
ensure_dir "${PRINTER_DATA_DIR}"
ensure_dir "${PRINTER_CFG_DIR}"
ensure_dir "${PRINTER_LOG_DIR}"
ensure_dir "${PRINTER_GCODE_DIR}"
ensure_dir "${PRINTER_COMMS_DIR}"
chown -R "${PI_USER}:${PI_GROUP}" "${PRINTER_DATA_DIR}"

if getent group dialout >/dev/null 2>&1; then
  usermod -a -G dialout "${PI_USER}" || true
fi

# Блок 6: Klipper env/service (репозиторий ожидается локально, clone fallback включен).
ensure_repo_present "${KLIPPER_DIR}" "${KLIPPER_REPO}" "${KLIPPER_REF}"

KLIPPER_REQ_FILE="${KLIPPER_DIR}/scripts/klippy-requirements.txt"
if [ ! -f "${KLIPPER_REQ_FILE}" ]; then
  log_error "runtime-bootstrap: Klipper requirements not found: ${KLIPPER_REQ_FILE}"
  exit 1
fi

ensure_python_venv "${KLIPPY_ENV_DIR}" "${KLIPPER_REQ_FILE}"
install_klipper_preflight

cat > /etc/systemd/system/klipper.service <<EOF
[Unit]
Description=Klipper 3D Printer Firmware Host
After=network.target treed-can-setup.service
Requires=treed-can-setup.service

[Install]
WantedBy=multi-user.target

[Service]
Type=simple
User=${PI_USER}
Group=${PI_GROUP}
SupplementaryGroups=dialout tty video render
WorkingDirectory=${KLIPPER_DIR}
ExecStartPre=${KLIPPER_PREFLIGHT_SCRIPT}
ExecStart=${KLIPPY_ENV_DIR}/bin/python ${KLIPPER_DIR}/klippy/klippy.py ${PRINTER_CFG_DIR}/printer.cfg -l ${PRINTER_LOG_DIR}/klippy.log -I ${PRINTER_COMMS_DIR}/klippy.serial -a ${KLIPPY_API_SOCK}
Restart=always
RestartSec=5
EOF

# Блок 7: Moonraker repo/env/service (clone при отсутствии).
ensure_repo_present "${MOONRAKER_DIR}" "${MOONRAKER_REPO}" "${MOONRAKER_REF}"

if [ "${MOONRAKER_RECREATE}" = "1" ] && [ -d "${MOONRAKER_ENV_DIR}" ]; then
  log_warn "runtime-bootstrap: forcing moonraker venv recreate (${MOONRAKER_ENV_DIR})"
  rm -rf "${MOONRAKER_ENV_DIR}"
fi

MOONRAKER_REQ_FILE=""
for candidate in \
  "${MOONRAKER_DIR}/scripts/moonraker-requirements.txt" \
  "${MOONRAKER_DIR}/scripts/requirements.txt"; do
  if [ -f "${candidate}" ]; then
    MOONRAKER_REQ_FILE="${candidate}"
    break
  fi
done

if [ -z "${MOONRAKER_REQ_FILE}" ]; then
  log_error "runtime-bootstrap: Moonraker requirements file not found in ${MOONRAKER_DIR}/scripts"
  exit 1
fi

MOONRAKER_ENTRYPOINT=""
for candidate in \
  "${MOONRAKER_DIR}/moonraker/moonraker.py" \
  "${MOONRAKER_DIR}/moonraker.py"; do
  if [ -f "${candidate}" ]; then
    MOONRAKER_ENTRYPOINT="${candidate}"
    break
  fi
done

if [ -z "${MOONRAKER_ENTRYPOINT}" ]; then
  log_error "runtime-bootstrap: Moonraker entrypoint not found in ${MOONRAKER_DIR}"
  exit 1
fi

ensure_python_venv "${MOONRAKER_ENV_DIR}" "${MOONRAKER_REQ_FILE}"

cat > /etc/systemd/system/moonraker.service <<EOF
[Unit]
Description=Moonraker API Server
After=network.target klipper.service
Wants=klipper.service

[Install]
WantedBy=multi-user.target

[Service]
Type=simple
User=${PI_USER}
Group=${PI_GROUP}
SupplementaryGroups=dialout tty video render
WorkingDirectory=${MOONRAKER_DIR}
ExecStart=${MOONRAKER_ENV_DIR}/bin/python ${MOONRAKER_ENTRYPOINT} -d ${PRINTER_DATA_DIR}
Restart=always
RestartSec=5
EOF

install_moonraker_policykit_rules

# Блок 8: Crowsnest repo/service (required для webcam-контура, управляется loader).
ensure_crowsnest_runtime

# Блок 9: Активация systemd unit-файлов.
systemctl daemon-reload
systemctl enable klipper.service >/dev/null
systemctl enable moonraker.service >/dev/null
systemctl enable crowsnest.service >/dev/null 2>&1 || true

chown -R "${PI_USER}:${PI_GROUP}" "${KLIPPER_DIR}" "${KLIPPY_ENV_DIR}" "${MOONRAKER_DIR}" "${MOONRAKER_ENV_DIR}" "${PRINTER_DATA_DIR}"
if [ -d "${CROWSNEST_DIR}" ]; then
  chown -R "${PI_USER}:${PI_GROUP}" "${CROWSNEST_DIR}"
fi

log_info "runtime-bootstrap: OK"
