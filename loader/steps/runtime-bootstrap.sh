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
KLIPPY_ENV_DIR="${TREED_KLIPPY_ENV_DIR:-${PI_HOME}/klippy-env}"
MOONRAKER_DIR="${TREED_MOONRAKER_SRC_DIR:-${PI_HOME}/moonraker}"
MOONRAKER_ENV_DIR="${TREED_MOONRAKER_ENV_DIR:-${PI_HOME}/moonraker-env}"
MOONRAKER_REPO="${TREED_MOONRAKER_REPO:-https://github.com/Arksine/moonraker.git}"
MOONRAKER_REF="${TREED_MOONRAKER_REF:-}"
MOONRAKER_POLKIT_SETUP="${TREED_MOONRAKER_POLKIT_SETUP:-1}"
MOONRAKER_POLKIT_REQUIRED="${TREED_MOONRAKER_POLKIT_REQUIRED:-0}"
MOONRAKER_RECREATE="${TREED_MOONRAKER_RECREATE:-0}"

PRINTER_DATA_DIR="${PI_HOME}/printer_data"
PRINTER_CFG_DIR="${PRINTER_DATA_DIR}/config"
PRINTER_LOG_DIR="${PRINTER_DATA_DIR}/logs"
PRINTER_GCODE_DIR="${PRINTER_DATA_DIR}/gcodes"
PRINTER_COMMS_DIR="${PRINTER_DATA_DIR}/comms"
KLIPPY_API_SOCK="${PRINTER_COMMS_DIR}/klippy.sock"

# Блок 4: Вспомогательные функции (run-as-user, clone/update, venv, requirements).
run_as_pi() {
  local cmd="$1"
  sudo -u "${PI_USER}" -H bash -lc "${cmd}"
}

ensure_repo_present() {
  local repo_dir="$1"
  local repo_url="$2"
  local repo_ref="$3"

  if [ "${repo_dir}" = "${MOONRAKER_DIR}" ] && [ "${MOONRAKER_RECREATE}" = "1" ]; then
    log_warn "runtime-bootstrap: forcing moonraker repo recreate (${repo_dir})"
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
      # Подтягиваем теги даже без repo_ref, чтобы Moonraker не оставался в inferred-версии.
      run_as_pi "set -euo pipefail; cd '${repo_dir}'; git fetch --tags --prune origin >/dev/null 2>&1 || true"
    fi
  fi

  if [ -d "${repo_dir}/.git" ]; then
    if [ -n "${repo_ref}" ]; then
      run_as_pi "set -euo pipefail; cd '${repo_dir}'; git fetch --tags --prune; git checkout '${repo_ref}'"
    fi
    return 0
  fi

  ensure_dir "$(dirname "${repo_dir}")"
  run_as_pi "set -euo pipefail; git clone --depth 1 '${repo_url}' '${repo_dir}'"
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
if [ ! -d "${KLIPPER_DIR}/.git" ]; then
  log_warn "runtime-bootstrap: Klipper repo not found in ${KLIPPER_DIR}, cloning fallback"
  ensure_repo_present "${KLIPPER_DIR}" "https://github.com/Klipper3d/klipper.git" "${TREED_KLIPPER_REF:-}"
fi

KLIPPER_REQ_FILE="${KLIPPER_DIR}/scripts/klippy-requirements.txt"
if [ ! -f "${KLIPPER_REQ_FILE}" ]; then
  log_error "runtime-bootstrap: Klipper requirements not found: ${KLIPPER_REQ_FILE}"
  exit 1
fi

ensure_python_venv "${KLIPPY_ENV_DIR}" "${KLIPPER_REQ_FILE}"

cat > /etc/systemd/system/klipper.service <<EOF
[Unit]
Description=Klipper 3D Printer Firmware Host
After=network.target treed-can-setup.service
Wants=treed-can-setup.service

[Install]
WantedBy=multi-user.target

[Service]
Type=simple
User=${PI_USER}
Group=${PI_GROUP}
SupplementaryGroups=dialout tty video render
WorkingDirectory=${KLIPPER_DIR}
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

# Блок 8: Активация systemd unit-файлов.
systemctl daemon-reload
systemctl enable klipper.service >/dev/null
systemctl enable moonraker.service >/dev/null

chown -R "${PI_USER}:${PI_GROUP}" "${KLIPPER_DIR}" "${KLIPPY_ENV_DIR}" "${MOONRAKER_DIR}" "${MOONRAKER_ENV_DIR}" "${PRINTER_DATA_DIR}"

log_info "runtime-bootstrap: OK"
