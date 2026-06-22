#!/bin/bash
set -euo pipefail

# ==========================================
# ШАГ LOADER: TREED SHELL INSTALL
# ==========================================
# Назначение:
# - Устанавливает TreeD Shell как экранный UI из готового release artifact.
# - Не клонирует и не собирает `treed-shell` на устройстве.
# - Ставит systemd unit и команду переключения `treed-ui`.
# Контур:
# - required для штатного TS/KS-переключателя;
# - выбранный UI управляется `TREED_UI_MODE=ts|ks`.

# Блок 1: Библиотеки, root-права и старт шага.
. "${REPO_DIR}/loader/lib/common.sh"
ensure_root

log_info "Step treed-shell-install: installing TreeD Shell UI"

# Блок 2: Параметры release artifact, runtime и выбранного UI.
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

SHELL_RELEASE_API_URL="${TREED_SHELL_RELEASE_API_URL:-https://api.github.com/repos/TreeD-Hub/treed-shell/releases}"
SHELL_RELEASE_TAG_PREFIX="${TREED_SHELL_RELEASE_TAG_PREFIX:-ui-main-}"
SHELL_UI_ASSET_NAME="${TREED_SHELL_UI_ASSET_NAME:-treed-shell-ui.zip}"
SHELL_UI_ARCHIVE_URL="${TREED_SHELL_UI_ARCHIVE_URL:-}"
SHELL_RUNTIME_DIR="${TREED_SHELL_RUNTIME_DIR:-${PI_HOME}/treed/treed-shell-runtime}"
SHELL_WEB_DIR="${TREED_SHELL_WEB_DIR:-${SHELL_RUNTIME_DIR}/ui}"
SHELL_ARCHIVE_PATH="${SHELL_RUNTIME_DIR}/${SHELL_UI_ASSET_NAME}"
SHELL_RUNTIME_SCRIPT="${SHELL_RUNTIME_DIR}/start-treed-shell-kiosk.sh"
SHELL_HTTP_PORT="${TREED_SHELL_HTTP_PORT:-8787}"
SHELL_BROWSER_BIN="${TREED_SHELL_BROWSER_BIN:-}"
TREED_UI_ENV_FILE="${TREED_UI_ENV_FILE:-/etc/default/treed-ui}"
TREED_SHELL_START_TIMEOUT="${TREED_SHELL_START_TIMEOUT:-45}"
UI_MODE="$(resolve_treed_ui_mode ts)"
BROWSER_BIN=""

# Блок 3: Helper-функции system package/runtime.
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

install_optional_unclutter() {
  if package_installed unclutter; then
    return 0
  fi

  if ! apt_update_noninteractive; then
    log_warn "treed-shell-install: optional package update failed, continuing"
    return 0
  fi

  if apt_get_noninteractive install unclutter; then
    log_info "treed-shell-install: installed optional package unclutter"
  else
    log_warn "treed-shell-install: optional package unclutter unavailable, continuing"
  fi
}

resolve_browser_bin() {
  local candidate=""

  if [ -n "${SHELL_BROWSER_BIN}" ] && [ -x "${SHELL_BROWSER_BIN}" ]; then
    printf '%s\n' "${SHELL_BROWSER_BIN}"
    return 0
  fi

  for candidate in chromium chromium-browser; do
    if command -v "${candidate}" >/dev/null 2>&1; then
      command -v "${candidate}"
      return 0
    fi
  done

  return 1
}

ensure_browser_runtime() {
  if BROWSER_BIN="$(resolve_browser_bin)"; then
    log_info "treed-shell-install: using browser ${BROWSER_BIN}"
    return 0
  fi

  apt_update_noninteractive
  if apt_get_noninteractive install chromium; then
    BROWSER_BIN="$(resolve_browser_bin)"
    log_info "treed-shell-install: installed browser ${BROWSER_BIN}"
    return 0
  fi

  log_warn "treed-shell-install: package chromium unavailable, trying chromium-browser"
  apt_get_noninteractive install chromium-browser
  BROWSER_BIN="$(resolve_browser_bin)"
  log_info "treed-shell-install: installed browser ${BROWSER_BIN}"
}

# Блок 4: Release artifact download/extract.
assert_runtime_path() {
  case "${SHELL_RUNTIME_DIR}" in
    "${PI_HOME}/treed/"*)
      ;;
    *)
      log_error "treed-shell-install: TREED_SHELL_RUNTIME_DIR must be inside ${PI_HOME}/treed, got ${SHELL_RUNTIME_DIR}"
      exit 1
      ;;
  esac

  case "${SHELL_WEB_DIR}" in
    "${SHELL_RUNTIME_DIR}/"*)
      ;;
    *)
      log_error "treed-shell-install: TREED_SHELL_WEB_DIR must be inside ${SHELL_RUNTIME_DIR}, got ${SHELL_WEB_DIR}"
      exit 1
      ;;
  esac
}

resolve_ui_archive_url() {
  if [ -n "${SHELL_UI_ARCHIVE_URL}" ]; then
    printf '%s\n' "${SHELL_UI_ARCHIVE_URL}"
    return 0
  fi

  python3 - "${SHELL_RELEASE_API_URL}" "${SHELL_RELEASE_TAG_PREFIX}" "${SHELL_UI_ASSET_NAME}" <<'PY'
import json
import sys
import urllib.request

api_url, tag_prefix, asset_name = sys.argv[1:4]
request = urllib.request.Request(
    api_url,
    headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "treed-mainshellOS-loader",
    },
)

with urllib.request.urlopen(request, timeout=30) as response:
    releases = json.load(response)

for release in releases:
    if release.get("draft") or release.get("prerelease"):
        continue

    tag_name = str(release.get("tag_name") or "")
    if tag_prefix and not tag_name.startswith(tag_prefix):
        continue

    for asset in release.get("assets") or []:
        if asset.get("name") == asset_name and asset.get("browser_download_url"):
            print(asset["browser_download_url"])
            raise SystemExit(0)

raise SystemExit(f"no release asset {asset_name!r} found for tag prefix {tag_prefix!r}")
PY
}

download_ui_archive() {
  local archive_url=""
  local tmp_archive=""

  assert_runtime_path
  ensure_dir "${SHELL_RUNTIME_DIR}"
  chown "${PI_USER}:${PI_GROUP}" "${SHELL_RUNTIME_DIR}"

  archive_url="$(resolve_ui_archive_url)"
  tmp_archive="$(mktemp "${SHELL_RUNTIME_DIR}/treed-shell-ui.XXXXXX.zip")"

  log_info "treed-shell-install: downloading ${SHELL_UI_ASSET_NAME}"
  curl -fL --connect-timeout 20 --retry 3 --retry-delay 2 -o "${tmp_archive}" "${archive_url}"

  if [ ! -s "${tmp_archive}" ]; then
    rm -f "${tmp_archive}"
    log_error "treed-shell-install: downloaded archive is empty"
    exit 1
  fi

  mv "${tmp_archive}" "${SHELL_ARCHIVE_PATH}"
  chown "${PI_USER}:${PI_GROUP}" "${SHELL_ARCHIVE_PATH}"
}

install_ui_archive() {
  local staged_dir="${SHELL_WEB_DIR}.new"

  rm -rf "${staged_dir}"
  mkdir -p "${staged_dir}"

  if ! python3 -m zipfile -t "${SHELL_ARCHIVE_PATH}" >/dev/null; then
    rm -rf "${staged_dir}"
    log_error "treed-shell-install: invalid UI archive ${SHELL_ARCHIVE_PATH}"
    exit 1
  fi

  python3 -m zipfile -e "${SHELL_ARCHIVE_PATH}" "${staged_dir}"

  if [ ! -f "${staged_dir}/index.html" ]; then
    rm -rf "${staged_dir}"
    log_error "treed-shell-install: index.html missing in UI archive"
    exit 1
  fi

  if [ ! -f "${staged_dir}/treed-shell-ui-manifest.json" ]; then
    rm -rf "${staged_dir}"
    log_error "treed-shell-install: treed-shell-ui-manifest.json missing in UI archive"
    exit 1
  fi

  rm -rf "${SHELL_WEB_DIR}"
  mv "${staged_dir}" "${SHELL_WEB_DIR}"
  chown -R "${PI_USER}:${PI_GROUP}" "${SHELL_WEB_DIR}"
  log_info "treed-shell-install: published UI bundle ${SHELL_WEB_DIR}"
}

# Блок 5: Kiosk launcher, systemd unit и операторская команда переключения.
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

write_kiosk_launcher() {
  local url="http://127.0.0.1:${SHELL_HTTP_PORT}/"

  cat > "${SHELL_RUNTIME_SCRIPT}" <<EOF
#!/bin/sh
set -eu

UI_DIR="${SHELL_WEB_DIR}"
PORT="${SHELL_HTTP_PORT}"
BROWSER="${BROWSER_BIN}"
PROFILE_DIR="${SHELL_RUNTIME_DIR}/chromium-profile"
URL="${url}"
CHROMIUM_RENDERING="\${TREED_SHELL_CHROMIUM_RENDERING:-hardware}"
CHROMIUM_FLAGS="--kiosk"
CHROMIUM_FLAGS="\${CHROMIUM_FLAGS} --no-first-run"
CHROMIUM_FLAGS="\${CHROMIUM_FLAGS} --disable-background-networking"
CHROMIUM_FLAGS="\${CHROMIUM_FLAGS} --disable-component-update"
CHROMIUM_FLAGS="\${CHROMIUM_FLAGS} --disable-default-apps"
CHROMIUM_FLAGS="\${CHROMIUM_FLAGS} --disable-features=Translate,MediaRouter"
CHROMIUM_FLAGS="\${CHROMIUM_FLAGS} --disable-infobars"
CHROMIUM_FLAGS="\${CHROMIUM_FLAGS} --disable-session-crashed-bubble"
CHROMIUM_FLAGS="\${CHROMIUM_FLAGS} --disable-dev-shm-usage"
CHROMIUM_FLAGS="\${CHROMIUM_FLAGS} --disable-sync"
CHROMIUM_FLAGS="\${CHROMIUM_FLAGS} --noerrdialogs"
unclutter_pid=""

mkdir -p "\${PROFILE_DIR}"
cd "\${UI_DIR}"

python3 -m http.server "\${PORT}" --bind 127.0.0.1 >/tmp/treed-shell-http.log 2>&1 &
server_pid=\$!

cleanup() {
  kill "\${server_pid}" 2>/dev/null || true
  if [ -n "\${unclutter_pid}" ]; then
    kill "\${unclutter_pid}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

if command -v unclutter >/dev/null 2>&1; then
  unclutter -idle 0.1 -root >/tmp/treed-shell-unclutter.log 2>&1 &
  unclutter_pid=\$!
fi

i=0
while [ "\${i}" -lt 30 ]; do
  if curl -fsS "\${URL}index.html" >/dev/null 2>&1; then
    break
  fi
  i=\$((i + 1))
  sleep 1
done

if [ "\${i}" -ge 30 ]; then
  echo "treed-shell kiosk: local UI server did not become ready" >&2
  exit 1
fi

case "\${CHROMIUM_RENDERING}" in
  software)
    CHROMIUM_FLAGS="\${CHROMIUM_FLAGS} --disable-gpu --disable-gpu-compositing --enable-unsafe-swiftshader"
    ;;
  hardware|"")
    ;;
  *)
    echo "treed-shell kiosk: unsupported TREED_SHELL_CHROMIUM_RENDERING=\${CHROMIUM_RENDERING}, using hardware" >&2
    ;;
esac

"\${BROWSER}" \${CHROMIUM_FLAGS} \\
  --user-data-dir="\${PROFILE_DIR}" \\
  "\${URL}"
EOF

  chmod 0755 "${SHELL_RUNTIME_SCRIPT}"
  chown "${PI_USER}:${PI_GROUP}" "${SHELL_RUNTIME_SCRIPT}"
  log_info "treed-shell-install: wrote ${SHELL_RUNTIME_SCRIPT}"
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
WorkingDirectory=${SHELL_WEB_DIR}
Environment=HOME=${PI_HOME}
Environment=TREED_SHELL_WEB_DIR=${SHELL_WEB_DIR}
Environment=TREED_SHELL_HTTP_PORT=${SHELL_HTTP_PORT}
Environment=TREED_SHELL_CHROMIUM_RENDERING=hardware
EnvironmentFile=-/etc/default/treed-shell
ExecStartPre=/bin/sh -lc 'plymouth quit --retain-splash || true'
ExecStart=/usr/bin/dbus-run-session -- /usr/bin/xinit ${SHELL_RUNTIME_SCRIPT} -- :0 -nolisten tcp
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

# Блок 6: Основной сценарий установки.
install_missing_packages curl ca-certificates python3 xinit dbus-x11
install_optional_unclutter
ensure_browser_runtime
deploy_treed_ui_command
download_ui_archive
install_ui_archive
write_kiosk_launcher
write_treed_shell_unit
apply_selected_ui_mode

log_info "treed-shell-install: OK (ui=${UI_MODE}, asset=${SHELL_UI_ASSET_NAME}, port=${SHELL_HTTP_PORT})"
