#!/bin/bash
set -euo pipefail

# ==========================================
# ШАГ LOADER: TREED SHELL INSTALL
# ==========================================
# Назначение:
# - Устанавливает TreeD Shell как альтернативный экранный UI.
# - Фиксирует checkout на ветке on-print и собирает printer-профиль Tauri.
# - Ставит systemd unit и команду переключения `treed-ui`.
# Контур:
# - required для штатного TS/KS-переключателя;
# - выбранный UI управляется `TREED_UI_MODE=ts|ks`.

# Блок 1: Библиотеки, root-права и старт шага.
. "${REPO_DIR}/loader/lib/common.sh"
ensure_root

log_info "Step treed-shell-install: installing TreeD Shell UI"

# Блок 2: Параметры source/runtime и выбранного UI.
PI_USER="${PI_USER:-${SUDO_USER:-pi}}"
PI_HOME="${PI_HOME:-$(getent passwd "${PI_USER}" | cut -d: -f6 || true)}"

if [ -z "${PI_HOME}" ] || [ ! -d "${PI_HOME}" ]; then
  log_error "treed-shell-install: cannot determine home for user ${PI_USER}"
  exit 1
fi
if ! PI_GROUP="$(pi_primary_group "${PI_USER}")"; then
  exit 1
fi

TREED_SHELL_INSTALL="${TREED_SHELL_INSTALL:-1}"
case "${TREED_SHELL_INSTALL}" in
  0|false|FALSE|no|NO|off|OFF)
    log_info "treed-shell-install: skipped (TREED_SHELL_INSTALL=${TREED_SHELL_INSTALL})"
    exit 0
    ;;
esac

SHELL_REPO_URL="${TREED_SHELL_REPO:-https://github.com/Yawllen/treed-shell.git}"
SHELL_PRIMARY_BRANCH="${TREED_SHELL_PRIMARY_BRANCH:-on-print}"
SHELL_REPO_REF="${TREED_SHELL_REF:-on-print}"
SHELL_HOME="${TREED_SHELL_HOME:-${PI_HOME}/treed/treed-shell}"
SHELL_RUNTIME_DIR="${TREED_SHELL_RUNTIME_DIR:-${PI_HOME}/treed/treed-shell-runtime}"
SHELL_RUNTIME_BIN="${SHELL_RUNTIME_DIR}/treed-shell"
SHELL_BUILD_MARKER="${SHELL_RUNTIME_DIR}/build.env"
TREED_UI_ENV_FILE="${TREED_UI_ENV_FILE:-/etc/default/treed-ui}"
TREED_NODE_VERSION="${TREED_SHELL_NODE_VERSION:-20.19.0}"
TREED_SHELL_START_TIMEOUT="${TREED_SHELL_START_TIMEOUT:-45}"
UI_MODE="$(resolve_treed_ui_mode ts)"
BUILD_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
SHELL_TARGET_COMMIT=""

# Блок 3: Helper-функции system package/toolchain.
package_installed() {
  local package="$1"

  dpkg-query -W -f='${Status}' "${package}" 2>/dev/null | grep -q '^install ok installed$'
}

install_missing_packages() {
  local missing=()
  local package=""

  for package in "$@"; do
    if ! package_installed "${package}"; then
      missing+=("${package}")
    fi
  done

  if [ "${#missing[@]}" -eq 0 ]; then
    log_info "treed-shell-install: system package set already current"
    return 0
  fi

  apt_update_noninteractive
  apt_get_noninteractive install "${missing[@]}"
}

node_version_ok() {
  local node_bin="$1"

  [ -x "${node_bin}" ] || return 1
  "${node_bin}" -e '
const [major, minor] = process.versions.node.split(".").map(Number);
process.exit(((major === 20 && minor >= 19) || (major === 22 && minor >= 12) || major > 22) ? 0 : 1);
' >/dev/null 2>&1
}

detect_node_arch() {
  case "$(uname -m)" in
    aarch64|arm64) printf '%s\n' "arm64" ;;
    armv7l|armv7*) printf '%s\n' "armv7l" ;;
    x86_64|amd64) printf '%s\n' "x64" ;;
    *)
      log_error "treed-shell-install: unsupported Node.js architecture $(uname -m)"
      exit 1
      ;;
  esac
}

ensure_node_runtime() {
  local system_node=""
  local node_arch=""
  local node_name=""
  local node_prefix=""
  local node_tar=""
  local node_url=""

  system_node="$(command -v node || true)"
  if [ -n "${system_node}" ] && node_version_ok "${system_node}"; then
    BUILD_PATH="$(dirname "${system_node}"):${BUILD_PATH}"
    log_info "treed-shell-install: using system Node.js $(${system_node} -p 'process.version')"
    return 0
  fi

  node_arch="$(detect_node_arch)"
  node_name="node-v${TREED_NODE_VERSION}-linux-${node_arch}"
  node_prefix="/opt/${node_name}"
  node_url="https://nodejs.org/dist/v${TREED_NODE_VERSION}/${node_name}.tar.xz"

  if [ ! -x "${node_prefix}/bin/node" ]; then
    node_tar="$(mktemp "/tmp/treed_node_${TREED_NODE_VERSION}_XXXXXX.tar.xz")"
    log_info "treed-shell-install: downloading Node.js ${TREED_NODE_VERSION} (${node_arch})"
    curl -fL --connect-timeout 20 --retry 3 --retry-delay 2 -o "${node_tar}" "${node_url}"
    tar -xJf "${node_tar}" -C /opt
    rm -f "${node_tar}"
  fi

  if ! node_version_ok "${node_prefix}/bin/node"; then
    log_error "treed-shell-install: installed Node.js is not compatible (${node_prefix})"
    exit 1
  fi

  ln -sfn "${node_prefix}" /opt/treed-node
  BUILD_PATH="/opt/treed-node/bin:${BUILD_PATH}"
  log_info "treed-shell-install: using bundled Node.js $(/opt/treed-node/bin/node -p 'process.version')"
}

rust_version_ok() {
  local rustc_bin="$1"

  [ -x "${rustc_bin}" ] || return 1
  "${rustc_bin}" --version 2>/dev/null | awk '
    {
      split($2, parts, ".")
      major = parts[1] + 0
      minor = parts[2] + 0
      if (major > 1 || (major == 1 && minor >= 77)) {
        exit 0
      }
      exit 1
    }
  '
}

user_rust_version_ok() {
  sudo -u "${PI_USER}" -H env \
    HOME="${PI_HOME}" \
    CARGO_HOME="${PI_HOME}/.cargo" \
    RUSTUP_HOME="${PI_HOME}/.rustup" \
    PATH="${PI_HOME}/.cargo/bin:${BUILD_PATH}" \
    sh -c '
      . "$HOME/.cargo/env" 2>/dev/null || true
      command -v rustc >/dev/null 2>&1 || exit 1
      rustc --version | awk "
        {
          split(\$2, parts, \".\")
          major = parts[1] + 0
          minor = parts[2] + 0
          if (major > 1 || (major == 1 && minor >= 77)) {
            exit 0
          }
          exit 1
        }
      "
    '
}

ensure_rust_runtime() {
  local cargo_bin="${PI_HOME}/.cargo/bin/cargo"
  local rustc_bin="${PI_HOME}/.cargo/bin/rustc"
  local system_rustc=""
  local rust_version=""

  system_rustc="$(command -v rustc || true)"
  if [ -n "${system_rustc}" ] && rust_version_ok "${system_rustc}"; then
    BUILD_PATH="$(dirname "${system_rustc}"):${BUILD_PATH}"
    log_info "treed-shell-install: using system rustc $(${system_rustc} --version)"
    return 0
  fi

  if ! user_rust_version_ok; then
    log_info "treed-shell-install: installing Rust toolchain via rustup for ${PI_USER}"
    sudo -u "${PI_USER}" -H env \
      HOME="${PI_HOME}" \
      CARGO_HOME="${PI_HOME}/.cargo" \
      RUSTUP_HOME="${PI_HOME}/.rustup" \
      PATH="${BUILD_PATH}" \
      sh -c \
      'curl --proto "=https" --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain stable'
  fi

  if ! user_rust_version_ok; then
    log_error "treed-shell-install: Rust toolchain is missing or too old after install"
    ls -la "${PI_HOME}/.cargo/bin" 2>/dev/null || true
    exit 1
  fi

  BUILD_PATH="${PI_HOME}/.cargo/bin:${BUILD_PATH}"
  rust_version="$(
    sudo -u "${PI_USER}" -H env \
      HOME="${PI_HOME}" \
      CARGO_HOME="${PI_HOME}/.cargo" \
      RUSTUP_HOME="${PI_HOME}/.rustup" \
      PATH="${PI_HOME}/.cargo/bin:${BUILD_PATH}" \
      sh -c '. "$HOME/.cargo/env" 2>/dev/null || true; rustc --version'
  )"
  log_info "treed-shell-install: using ${rust_version}"
}

ensure_build_dependencies() {
  install_missing_packages \
    git curl ca-certificates xz-utils xinit dbus-x11 build-essential wget file \
    libwebkit2gtk-4.1-dev libgtk-3-dev libxdo-dev libssl-dev \
    libayatana-appindicator3-dev librsvg2-dev patchelf

  ensure_node_runtime
  ensure_rust_runtime
}

# Блок 4: Checkout ветки on-print в управляемый каталог.
assert_managed_home_path() {
  case "${SHELL_HOME}" in
    "${PI_HOME}/treed/"*)
      ;;
    *)
      log_error "treed-shell-install: TREED_SHELL_HOME must be inside ${PI_HOME}/treed, got ${SHELL_HOME}"
      exit 1
      ;;
  esac
}

checkout_treed_shell_ref() {
  local target_commit=""

  assert_managed_home_path
  ensure_dir "$(dirname "${SHELL_HOME}")"
  chown "${PI_USER}:${PI_GROUP}" "$(dirname "${SHELL_HOME}")"

  if [ -e "${SHELL_HOME}" ] && [ ! -d "${SHELL_HOME}/.git" ]; then
    log_warn "treed-shell-install: removing non-git managed path ${SHELL_HOME}"
    rm -rf "${SHELL_HOME}"
  fi

  if [ ! -d "${SHELL_HOME}/.git" ]; then
    sudo -u "${PI_USER}" -H git clone "${SHELL_REPO_URL}" "${SHELL_HOME}"
  else
    sudo -u "${PI_USER}" -H git -C "${SHELL_HOME}" remote set-url origin "${SHELL_REPO_URL}" >/dev/null
  fi

  sudo -u "${PI_USER}" -H git -C "${SHELL_HOME}" fetch --tags --prune origin

  target_commit="$(sudo -u "${PI_USER}" -H git -C "${SHELL_HOME}" rev-parse --verify "origin/${SHELL_REPO_REF}^{commit}" 2>/dev/null || true)"
  if [ -z "${target_commit}" ]; then
    target_commit="$(sudo -u "${PI_USER}" -H git -C "${SHELL_HOME}" rev-parse --verify "${SHELL_REPO_REF}^{commit}" 2>/dev/null || true)"
  fi
  if [ -z "${target_commit}" ]; then
    log_error "treed-shell-install: failed to resolve TreeD Shell ref ${SHELL_REPO_REF}"
    exit 1
  fi

  sudo -u "${PI_USER}" -H git -C "${SHELL_HOME}" checkout -B "${SHELL_PRIMARY_BRANCH}" "${target_commit}"
  sudo -u "${PI_USER}" -H git -C "${SHELL_HOME}" reset --hard "${target_commit}" >/dev/null
  if sudo -u "${PI_USER}" -H git -C "${SHELL_HOME}" rev-parse --verify "origin/${SHELL_PRIMARY_BRANCH}^{commit}" >/dev/null 2>&1; then
    sudo -u "${PI_USER}" -H git -C "${SHELL_HOME}" branch --set-upstream-to="origin/${SHELL_PRIMARY_BRANCH}" "${SHELL_PRIMARY_BRANCH}" >/dev/null 2>&1 || true
  fi

  SHELL_TARGET_COMMIT="${target_commit}"
  chown -R "${PI_USER}:${PI_GROUP}" "${SHELL_HOME}"
  log_info "treed-shell-install: checkout ${SHELL_REPO_REF} (${SHELL_TARGET_COMMIT})"
}

# Блок 5: Сборка printer-профиля Tauri и публикация runtime binary.
treed_shell_needs_build() {
  if [ "${TREED_FORCE_SHELL_BUILD:-0}" = "1" ]; then
    return 0
  fi
  if [ ! -x "${SHELL_RUNTIME_BIN}" ]; then
    return 0
  fi
  if [ ! -f "${SHELL_BUILD_MARKER}" ]; then
    return 0
  fi
  if grep -q "^commit=${SHELL_TARGET_COMMIT}$" "${SHELL_BUILD_MARKER}"; then
    return 1
  fi
  return 0
}

find_treed_shell_release_binary() {
  local release_dir="${SHELL_HOME}/src-tauri/target/release"
  local candidate=""

  for candidate in \
    "${release_dir}/app" \
    "${release_dir}/treed-shell"
  do
    if [ -f "${candidate}" ] && [ -x "${candidate}" ]; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done

  find "${release_dir}" \
    -maxdepth 1 \
    -type f \
    -perm -111 \
    ! -name "*.so" \
    ! -name "*.d" \
    -print \
    2>/dev/null | head -n 1
}

build_treed_shell() {
  local release_bin=""

  if ! treed_shell_needs_build; then
    log_info "treed-shell-install: runtime binary already matches checkout"
    return 0
  fi

  ensure_build_dependencies

  log_info "treed-shell-install: installing npm dependencies"
  sudo -u "${PI_USER}" -H env PATH="${BUILD_PATH}" npm ci --no-audit --no-fund --prefix "${SHELL_HOME}"

  log_info "treed-shell-install: building printer Tauri profile"
  sudo -u "${PI_USER}" -H env PATH="${BUILD_PATH}" sh -c \
    "cd '${SHELL_HOME}' && npm run tauri:build:printer"

  release_bin="$(find_treed_shell_release_binary | tr -d '\r\n')"
  if [ -z "${release_bin}" ] || [ ! -x "${release_bin}" ]; then
    log_error "treed-shell-install: release binary not found after build"
    exit 1
  fi

  ensure_dir "${SHELL_RUNTIME_DIR}"
  install -m 0755 "${release_bin}" "${SHELL_RUNTIME_BIN}"
  cat > "${SHELL_BUILD_MARKER}" <<EOF
commit=${SHELL_TARGET_COMMIT}
ref=${SHELL_REPO_REF}
branch=${SHELL_PRIMARY_BRANCH}
repo=${SHELL_REPO_URL}
EOF
  chown -R "${PI_USER}:${PI_GROUP}" "${SHELL_RUNTIME_DIR}"
  log_info "treed-shell-install: published runtime binary ${SHELL_RUNTIME_BIN}"
}

# Блок 6: Systemd unit и операторская команда переключения.
deploy_treed_ui_command() {
  local src="${REPO_DIR}/runtime-scripts/treed-ui/treed-ui"

  if [ ! -f "${src}" ]; then
    log_error "treed-shell-install: missing runtime command source ${src}"
    exit 1
  fi

  install -m 0755 "${src}" /usr/local/sbin/treed-ui
  ln -sfn /usr/local/sbin/treed-ui /usr/local/bin/treed-ui
  log_info "treed-shell-install: deployed /usr/local/sbin/treed-ui"
}

write_treed_shell_unit() {
  cat > /etc/systemd/system/treed-shell.service <<EOF
[Unit]
Description=TreeD Shell UI
After=systemd-user-sessions.service plymouth-quit.service network-online.target moonraker.service
Wants=plymouth-quit.service network-online.target
Conflicts=KlipperScreen.service

[Service]
Type=simple
User=${PI_USER}
WorkingDirectory=${SHELL_HOME}
Environment=HOME=${PI_HOME}
Environment=WEBKIT_DISABLE_COMPOSITING_MODE=1
ExecStartPre=/bin/sh -lc 'plymouth quit --retain-splash || true'
ExecStart=/usr/bin/dbus-run-session -- /usr/bin/xinit ${SHELL_RUNTIME_BIN} -- :0 -nolisten tcp
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload
  log_info "treed-shell-install: wrote treed-shell.service"
}

write_ui_mode_env() {
  install -d -m 0755 "$(dirname "${TREED_UI_ENV_FILE}")"
  cat > "${TREED_UI_ENV_FILE}" <<EOF
TREED_UI_MODE=${UI_MODE}
EOF
  chmod 0644 "${TREED_UI_ENV_FILE}"
}

wait_service_active() {
  local unit="$1"
  local timeout="$2"
  local i

  for i in $(seq 1 "${timeout}"); do
    if systemctl is-active --quiet "${unit}"; then
      return 0
    fi
    sleep 1
  done

  return 1
}

print_service_diagnostics() {
  local unit="$1"

  systemctl --no-pager -l status "${unit}" || true
  journalctl -u "${unit}" -n 80 --no-pager || true
}

apply_selected_ui_mode() {
  write_ui_mode_env

  case "${UI_MODE}" in
    ts)
      /usr/local/sbin/treed-ui ts
      if ! wait_service_active "treed-shell.service" "${TREED_SHELL_START_TIMEOUT}"; then
        log_error "treed-shell-install: treed-shell.service failed to become active within ${TREED_SHELL_START_TIMEOUT}s"
        print_service_diagnostics "treed-shell.service"
        exit 1
      fi
      ;;
    ks)
      /usr/local/sbin/treed-ui ks
      ;;
    *)
      log_error "treed-shell-install: invalid resolved UI mode ${UI_MODE}"
      exit 1
      ;;
  esac
}

# Блок 7: Основной сценарий установки.
install_missing_packages git curl ca-certificates xinit dbus-x11
deploy_treed_ui_command
checkout_treed_shell_ref
build_treed_shell
write_treed_shell_unit
apply_selected_ui_mode

log_info "treed-shell-install: OK (ui=${UI_MODE}, ref=${SHELL_REPO_REF})"
