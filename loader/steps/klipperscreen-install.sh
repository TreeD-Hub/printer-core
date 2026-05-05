#!/bin/bash
set -euo pipefail

# ==========================================
# ШАГ LOADER: KLIPPERSCREEN INSTALL
# ==========================================
# Назначение:
# - Обеспечивает managed-установку и базовую работоспособность KlipperScreen.
# - Не переустанавливает checkout той же версии или новее.
# - Выполняет проверки health состояния systemd-сервиса.
# Контур:
# - required: экранный UI является частью штатного V2 runtime.

# Блок 1: Библиотеки и root-права.
. "${REPO_DIR}/loader/lib/common.sh"

ensure_root

# Блок 2: Старт шага.
log_info "Step klipperscreen-install: ensuring KlipperScreen is installed"

# Блок 3: Вспомогательные функции проверки/установки KlipperScreen.
wait_service_active() {
  local unit="$1"
  local timeout="${2:-30}"
  local i

  for i in $(seq 1 "${timeout}"); do
    if systemctl is-active --quiet "${unit}"; then
      return 0
    fi
    sleep 1
  done

  return 1
}

assert_klipperscreen_healthy() {
  local unit="KlipperScreen.service"
  local timeout="${TREED_KLIPPERSCREEN_START_TIMEOUT:-45}"
  local err=""
  local rc=0

  if ! systemctl cat "${unit}" >/dev/null 2>&1; then
    log_error "klipperscreen-install: ${unit} not found"
    exit 1
  fi

  if systemctl is-active --quiet "${unit}"; then
    log_info "klipperscreen-install: ${unit} already active"
    return 0
  fi

  log_warn "klipperscreen-install: ${unit} is not active, restarting"
  if err="$(systemctl restart "${unit}" 2>&1)"; then
    :
  else
    rc=$?
    log_error "klipperscreen-install: ${unit} restart failed rc=${rc}: ${err}"
    print_klipperscreen_service_diagnostics "${unit}"
    exit 1
  fi

  if wait_service_active "${unit}" "${timeout}"; then
    log_info "klipperscreen-install: ${unit} is active after restart"
    return 0
  fi

  log_error "klipperscreen-install: ${unit} failed to become active within ${timeout}s"
  print_klipperscreen_service_diagnostics "${unit}"
  exit 1
}

print_klipperscreen_service_diagnostics() {
  local unit="$1"

  systemctl --no-pager -l status "${unit}" || true
  journalctl -u "${unit}" -n 80 --no-pager || true
}

checkout_klipperscreen_ref() {
  local repo_url="$1"
  local dst_dir="$2"
  local ref="$3"

  sudo -u "${PI_USER}" -H mkdir -p "$(dirname "${dst_dir}")"
  sudo -u "${PI_USER}" -H git init "${dst_dir}" >/dev/null
  sudo -u "${PI_USER}" -H git -C "${dst_dir}" remote add origin "${repo_url}"

  if ! sudo -u "${PI_USER}" -H git -C "${dst_dir}" fetch --depth 1 origin "${ref}" >/dev/null 2>&1; then
    log_error "klipperscreen-install: failed to fetch ref '${ref}' from ${repo_url}"
    exit 1
  fi

  sudo -u "${PI_USER}" -H git -C "${dst_dir}" checkout --detach FETCH_HEAD >/dev/null
}

klipperscreen_package_complete() {
  local package_dir="$1"

  [ -d "${package_dir}/.git" ] \
    && [ -f "${package_dir}/scripts/KlipperScreen-install.sh" ] \
    && [ -d "${package_dir}/styles" ]
}

klipperscreen_is_same_or_newer() {
  local installed_dir="$1"
  local target_commit="$2"
  local installed_head=""
  local installed_ts=""
  local target_ts=""

  installed_head="$(sudo -u "${PI_USER}" -H git -C "${installed_dir}" rev-parse HEAD 2>/dev/null || true)"
  if [ -z "${installed_head}" ]; then
    return 1
  fi

  if [ "${installed_head}" = "${target_commit}" ]; then
    return 0
  fi

  sudo -u "${PI_USER}" -H git -C "${installed_dir}" fetch --tags --prune origin >/dev/null 2>&1 || true
  sudo -u "${PI_USER}" -H git -C "${installed_dir}" fetch --depth 1 origin "${target_commit}" >/dev/null 2>&1 || true

  if sudo -u "${PI_USER}" -H git -C "${installed_dir}" merge-base --is-ancestor "${target_commit}" "${installed_head}" >/dev/null 2>&1; then
    return 0
  fi

  # Shallow checkout fallback: official KlipperScreen history is linear enough for timestamp gating.
  installed_ts="$(sudo -u "${PI_USER}" -H git -C "${installed_dir}" show -s --format=%ct "${installed_head}" 2>/dev/null || true)"
  target_ts="$(sudo -u "${PI_USER}" -H git -C "${KS_STAGING_DIR}" show -s --format=%ct "${target_commit}" 2>/dev/null || true)"
  if [ -n "${installed_ts}" ] && [ -n "${target_ts}" ] && [ "${installed_ts}" -ge "${target_ts}" ]; then
    return 0
  fi

  return 1
}

klipperscreen_service_points_to_home() {
  local package_dir="$1"
  local workdir=""

  if ! systemctl cat KlipperScreen.service >/dev/null 2>&1; then
    return 1
  fi

  workdir="$(systemctl show -p WorkingDirectory --value KlipperScreen.service 2>/dev/null | tr -d '\r\n')"
  [ "${workdir}" = "${package_dir}" ]
}

klipperscreen_needs_install() {
  local package_dir="$1"
  local target_commit="$2"
  KLIPPERSCREEN_INSTALL_REASON=""

  if [ "${TREED_FORCE_KLIPPERSCREEN_INSTALL:-0}" = "1" ]; then
    log_info "klipperscreen-install: forced reinstall requested"
    KLIPPERSCREEN_INSTALL_REASON="forced"
    return 0
  fi

  if ! klipperscreen_package_complete "${package_dir}"; then
    log_warn "klipperscreen-install: package incomplete or missing at ${package_dir}"
    KLIPPERSCREEN_INSTALL_REASON="package-incomplete"
    return 0
  fi

  if [ ! -x "${KS_ENV}/bin/python" ]; then
    log_warn "klipperscreen-install: runtime venv incomplete or missing at ${KS_ENV}"
    KLIPPERSCREEN_INSTALL_REASON="runtime-incomplete"
    return 0
  fi

  if ! klipperscreen_is_same_or_newer "${package_dir}" "${target_commit}"; then
    log_info "klipperscreen-install: installed KlipperScreen is older than target ${target_commit}"
    KLIPPERSCREEN_INSTALL_REASON="package-older"
    return 0
  fi

  if ! klipperscreen_service_points_to_home "${package_dir}"; then
    log_warn "klipperscreen-install: service missing or points outside ${package_dir}"
    KLIPPERSCREEN_INSTALL_REASON="service-missing"
    return 0
  fi

  log_info "klipperscreen-install: installed package is same-or-newer and service is wired to ${package_dir}"
  return 1
}

patch_klipperscreen_installer_noninteractive() {
  local installer="$1"

  sed -i \
    -e '/^if \[ "\$EUID" == 0 \]$/,+3d' \
    -e 's|sudo apt update|sudo env DEBIAN_FRONTEND=noninteractive APT_LISTCHANGES_FRONTEND=none NEEDRESTART_MODE=a apt-get update|g' \
    -e 's|sudo apt install -y|sudo env DEBIAN_FRONTEND=noninteractive APT_LISTCHANGES_FRONTEND=none NEEDRESTART_MODE=a apt-get -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold install|g' \
    -e 's|sudo apt install |sudo env DEBIAN_FRONTEND=noninteractive APT_LISTCHANGES_FRONTEND=none NEEDRESTART_MODE=a apt-get -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold install |g' \
    -e 's|sudo apt -f install|sudo env DEBIAN_FRONTEND=noninteractive APT_LISTCHANGES_FRONTEND=none NEEDRESTART_MODE=a apt-get -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold -f install|g' \
    -e 's|sudo ||g' \
    "${installer}"
}

# Блок 4: Основной сценарий установки и health-check.
PI_USER="${PI_USER:-${SUDO_USER:-pi}}"
PI_HOME="${PI_HOME:-$(getent passwd "${PI_USER}" | cut -d: -f6 || true)}"

if [ -z "${PI_HOME}" ] || [ ! -d "${PI_HOME}" ]; then
  log_error "klipperscreen-install: cannot determine home for user ${PI_USER}"
  exit 1
fi
if ! PI_GROUP="$(pi_primary_group "${PI_USER}")"; then
  exit 1
fi

if ! command -v git >/dev/null 2>&1; then
  log_info "klipperscreen-install: installing git"
  apt_update_noninteractive
  apt_get_noninteractive install git
fi

KS_REPO_URL="${TREED_KLIPPERSCREEN_REPO:-https://github.com/jordanruthe/KlipperScreen.git}"
KS_PINNED_REF_DEFAULT="35c26ba4d452043695d73fa8ec2acd25bbc8911d"
KS_REPO_REF="${TREED_KLIPPERSCREEN_REF:-${KS_PINNED_REF_DEFAULT}}"
KS_STAGING_DIR="${PI_HOME}/treed/.staging/KlipperScreen"
KS_STAGING_PARENT="$(dirname "${KS_STAGING_DIR}")"
KS_HOME_DEFAULT="${PI_HOME}/KlipperScreen"
if [ -n "${TREED_KLIPPERSCREEN_HOME:-}" ]; then
  KS_HOME="${TREED_KLIPPERSCREEN_HOME}"
else
  KS_HOME="${KS_HOME_DEFAULT}"
fi
KS_ENV="${TREED_KLIPPERSCREEN_ENV:-${PI_HOME}/.KlipperScreen-env}"
KS_INSTALL_SERVICE="${TREED_KLIPPERSCREEN_INSTALL_SERVICE:-1}"
KS_BACKEND="${TREED_KLIPPERSCREEN_BACKEND:-X}"
KS_NETWORK="${TREED_KLIPPERSCREEN_NETWORK_MANAGER:-N}"
KS_START="${TREED_KLIPPERSCREEN_START_AFTER_INSTALL:-0}"
KLIPPERSCREEN_INSTALL_REASON=""

case "${KS_INSTALL_SERVICE}" in
  0|n|N) KS_INSTALL_SERVICE="N" ;;
  *) KS_INSTALL_SERVICE="Y" ;;
esac
case "${KS_BACKEND}" in
  w|W) KS_BACKEND="W" ;;
  *) KS_BACKEND="X" ;;
esac
case "${KS_NETWORK}" in
  1|y|Y) KS_NETWORK="Y" ;;
  *) KS_NETWORK="N" ;;
esac

# На пустой системе эти каталоги могут отсутствовать, а после старых запусков
# могут принадлежать root. Checkout выполняется от deploy-пользователя.
ensure_dir "${PI_HOME}/treed"
chown "${PI_USER}:${PI_GROUP}" "${PI_HOME}/treed"
ensure_dir "${KS_STAGING_PARENT}"
chown "${PI_USER}:${PI_GROUP}" "${KS_STAGING_PARENT}"

# Всегда пересобираем staging-клон, чтобы не наследовать старое состояние checkout.
rm -rf "${KS_STAGING_DIR}"
checkout_klipperscreen_ref "${KS_REPO_URL}" "${KS_STAGING_DIR}" "${KS_REPO_REF}"

if [ ! -f "${KS_STAGING_DIR}/scripts/KlipperScreen-install.sh" ]; then
  log_error "klipperscreen-install: installer script not found for ref ${KS_REPO_REF}"
  exit 1
fi

KS_COMMIT="$(sudo -u "${PI_USER}" -H git -C "${KS_STAGING_DIR}" rev-parse --short=12 HEAD)"
KS_COMMIT_FULL="$(sudo -u "${PI_USER}" -H git -C "${KS_STAGING_DIR}" rev-parse HEAD)"
log_info "klipperscreen-install: target ref ${KS_REPO_REF} (commit ${KS_COMMIT})"

if klipperscreen_needs_install "${KS_HOME}" "${KS_COMMIT_FULL}"; then
  case "${KLIPPERSCREEN_INSTALL_REASON}" in
    forced|package-incomplete|package-older)
      log_info "klipperscreen-install: installing managed package into ${KS_HOME}"
      rm -rf "${KS_HOME}"
      checkout_klipperscreen_ref "${KS_REPO_URL}" "${KS_HOME}" "${KS_REPO_REF}"
      ;;
    runtime-incomplete|service-missing)
      log_info "klipperscreen-install: package is same-or-newer; running installer to restore runtime wiring"
      ;;
    *)
      log_error "klipperscreen-install: unexpected install reason '${KLIPPERSCREEN_INSTALL_REASON}'"
      exit 1
      ;;
  esac
  patch_klipperscreen_installer_noninteractive "${KS_HOME}/scripts/KlipperScreen-install.sh"

  env \
    USER="${PI_USER}" \
    LOGNAME="${PI_USER}" \
    HOME="${PI_HOME}" \
    SERVICE="${KS_INSTALL_SERVICE}" \
    BACKEND="${KS_BACKEND}" \
    NETWORK="${KS_NETWORK}" \
    START="${KS_START}" \
    KLIPPERSCREEN_VENV="${KS_ENV}" \
    DEBIAN_FRONTEND="${DEBIAN_FRONTEND:-noninteractive}" \
    APT_LISTCHANGES_FRONTEND="${APT_LISTCHANGES_FRONTEND:-none}" \
    NEEDRESTART_MODE="${NEEDRESTART_MODE:-a}" \
    bash "${KS_HOME}/scripts/KlipperScreen-install.sh"

  chown -R "${PI_USER}:${PI_GROUP}" "${KS_HOME}"
  if [ -d "${KS_ENV}" ]; then
    chown -R "${PI_USER}:${PI_GROUP}" "${KS_ENV}"
  fi
  if [ -d "${PI_HOME}/.local" ]; then
    chown -R "${PI_USER}:${PI_GROUP}" "${PI_HOME}/.local"
  fi
else
  log_info "klipperscreen-install: reinstall skipped"
fi

if ! klipperscreen_package_complete "${KS_HOME}"; then
  log_error "klipperscreen-install: installed package is incomplete after install (${KS_HOME})"
  exit 1
fi

systemctl enable KlipperScreen.service >/dev/null 2>&1 || true
assert_klipperscreen_healthy

log_info "klipperscreen-install: OK"
