#!/bin/bash
set -euo pipefail

# ==========================================
# ШАГ LOADER: KLIPPERSCREEN INSTALL
# ==========================================
# Назначение:
# - Обеспечивает установку и базовую работоспособность KlipperScreen.
# - Выполняет проверки health состояния systemd-сервиса.
# Контур:
# - required, если сервис уже установлен или включена принудительная установка.

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

  if ! systemctl cat "${unit}" >/dev/null 2>&1; then
    log_error "klipperscreen-install: ${unit} not found"
    exit 1
  fi

  if systemctl is-active --quiet "${unit}"; then
    log_info "klipperscreen-install: ${unit} already active"
    return 0
  fi

  log_warn "klipperscreen-install: ${unit} is not active, restarting"
  systemctl restart "${unit}"

  if wait_service_active "${unit}" "${timeout}"; then
    log_info "klipperscreen-install: ${unit} is active after restart"
    return 0
  fi

  log_error "klipperscreen-install: ${unit} failed to become active within ${timeout}s"
  systemctl --no-pager -l status "${unit}" || true
  exit 1
}

checkout_klipperscreen_ref() {
  local repo_url="$1"
  local dst_dir="$2"
  local ref="$3"

  sudo -u "${PI_USER}" -H git init "${dst_dir}" >/dev/null
  sudo -u "${PI_USER}" -H git -C "${dst_dir}" remote add origin "${repo_url}"

  if ! sudo -u "${PI_USER}" -H git -C "${dst_dir}" fetch --depth 1 origin "${ref}" >/dev/null 2>&1; then
    log_error "klipperscreen-install: failed to fetch ref '${ref}' from ${repo_url}"
    exit 1
  fi

  sudo -u "${PI_USER}" -H git -C "${dst_dir}" checkout --detach FETCH_HEAD >/dev/null
}

patch_klipperscreen_installer_noninteractive() {
  local installer="$1"

  sed -i \
    -e 's|sudo apt update|sudo env DEBIAN_FRONTEND=noninteractive APT_LISTCHANGES_FRONTEND=none NEEDRESTART_MODE=a apt-get update|g' \
    -e 's|sudo apt install -y|sudo env DEBIAN_FRONTEND=noninteractive APT_LISTCHANGES_FRONTEND=none NEEDRESTART_MODE=a apt-get -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold install|g' \
    -e 's|sudo apt install |sudo env DEBIAN_FRONTEND=noninteractive APT_LISTCHANGES_FRONTEND=none NEEDRESTART_MODE=a apt-get -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold install |g' \
    -e 's|sudo apt -f install|sudo env DEBIAN_FRONTEND=noninteractive APT_LISTCHANGES_FRONTEND=none NEEDRESTART_MODE=a apt-get -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold -f install|g' \
    "${installer}"
}

# Блок 4: Основной сценарий установки и health-check.
PI_USER="${PI_USER:-${SUDO_USER:-pi}}"
PI_HOME="${PI_HOME:-$(getent passwd "${PI_USER}" | cut -d: -f6 || true)}"

if [ -z "${PI_HOME}" ] || [ ! -d "${PI_HOME}" ]; then
  log_error "klipperscreen-install: cannot determine home for user ${PI_USER}"
  exit 1
fi

# Поведение по умолчанию: ставим только если KlipperScreen.service отсутствует.
if systemctl cat KlipperScreen.service >/dev/null 2>&1 && [ "${TREED_FORCE_KLIPPERSCREEN_INSTALL:-0}" != "1" ]; then
  log_info "klipperscreen-install: KlipperScreen.service already exists, skipping install and validating service health"
  assert_klipperscreen_healthy
  exit 0
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
KS_INSTALL_SERVICE="${TREED_KLIPPERSCREEN_INSTALL_SERVICE:-1}"
KS_BACKEND="${TREED_KLIPPERSCREEN_BACKEND:-X}"
KS_NETWORK="${TREED_KLIPPERSCREEN_NETWORK_MANAGER:-N}"
KS_START="${TREED_KLIPPERSCREEN_START_AFTER_INSTALL:-0}"

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

# Всегда пересобираем staging-клон, чтобы не наследовать старое состояние checkout.
rm -rf "${KS_STAGING_DIR}"
checkout_klipperscreen_ref "${KS_REPO_URL}" "${KS_STAGING_DIR}" "${KS_REPO_REF}"

if [ ! -f "${KS_STAGING_DIR}/scripts/KlipperScreen-install.sh" ]; then
  log_error "klipperscreen-install: installer script not found for ref ${KS_REPO_REF}"
  exit 1
fi
patch_klipperscreen_installer_noninteractive "${KS_STAGING_DIR}/scripts/KlipperScreen-install.sh"

KS_COMMIT="$(sudo -u "${PI_USER}" -H git -C "${KS_STAGING_DIR}" rev-parse --short=12 HEAD)"
log_info "klipperscreen-install: using ref ${KS_REPO_REF} (commit ${KS_COMMIT})"

sudo -u "${PI_USER}" -H env \
  SERVICE="${KS_INSTALL_SERVICE}" \
  BACKEND="${KS_BACKEND}" \
  NETWORK="${KS_NETWORK}" \
  START="${KS_START}" \
  DEBIAN_FRONTEND="${DEBIAN_FRONTEND:-noninteractive}" \
  APT_LISTCHANGES_FRONTEND="${APT_LISTCHANGES_FRONTEND:-none}" \
  NEEDRESTART_MODE="${NEEDRESTART_MODE:-a}" \
  bash -lc "'${KS_STAGING_DIR}/scripts/KlipperScreen-install.sh'"

systemctl enable KlipperScreen.service >/dev/null 2>&1 || true
assert_klipperscreen_healthy

log_info "klipperscreen-install: OK"
