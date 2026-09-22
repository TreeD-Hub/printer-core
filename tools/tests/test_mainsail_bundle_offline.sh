#!/bin/bash
set -euo pipefail

# ==========================================
# CONTRACT TEST: OFFLINE BUNDLED MAINSAIL
# ==========================================
# Назначение:
# - Запускает реальный mainsail-web step с системными командами, заменёнными локальными mocks.
# - Падает при любой попытке обратиться к GitHub через wget.

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
. "${REPO_DIR}/runtime-versions.env"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

mkdir -p "${tmp}/home" "${tmp}/nginx/available" "${tmp}/nginx/enabled"
network_marker="${tmp}/network-accessed"
mock_env="${tmp}/mocks.sh"

cat > "${mock_env}" <<'EOF'
id() {
  case "${1:-}" in
    -u) printf '0\n' ;;
    -gn) printf 'test\n' ;;
    *) command id "$@" ;;
  esac
}
apt-get() { return 0; }
chown() { return 0; }
setfacl() { return 0; }
nginx() { return 0; }
systemctl() { return 0; }
curl() { printf '200'; }
wget() {
  printf 'unexpected wget call\n' > "${NETWORK_MARKER}"
  return 99
}
sudo() {
  while [ "$#" -gt 0 ]; do
    case "$1" in
      -u) shift 2 ;;
      -H) shift ;;
      *) break ;;
    esac
  done
  "$@"
}
rsync() {
  local args=("$@")
  local count="${#args[@]}"
  local src="${args[$((count-2))]}"
  local dst="${args[$((count-1))]}"
  mkdir -p "${dst}"
  cp -a "${src}/." "${dst}/"
}
EOF

NETWORK_MARKER="${network_marker}" \
BASH_ENV="${mock_env}" \
REPO_DIR="${REPO_DIR}" \
PI_USER="test" \
PI_HOME="${tmp}/home" \
TREED_MAINSAIL_WEB_PATH="${tmp}/www/mainsail" \
TREED_MAINSAIL_NGINX_SITE_AVAILABLE="${tmp}/nginx/available/mainsail" \
TREED_MAINSAIL_NGINX_SITE_ENABLED="${tmp}/nginx/enabled/mainsail" \
TREED_MAINSAIL_NGINX_DEFAULT_SITE_ENABLED="${tmp}/nginx/enabled/default" \
bash "${REPO_DIR}/loader/steps/mainsail-web.sh" >/dev/null

if [ -e "${network_marker}" ]; then
  printf 'bundled Mainsail attempted a network download\n' >&2
  exit 1
fi
[ "$(sed -nE 's|.*"version"[[:space:]]*:[[:space:]]*"([^"]+)".*|\1|p' "${tmp}/www/mainsail/release_info.json")" = "${TREED_MAINSAIL_VERSION}" ]
test -f "${tmp}/www/mainsail/index.html"

# Старый установленный web-root не должен маскировать отсутствие manifest artifact.
mkdir -p "${tmp}/www-old"
printf '<html></html>\n' > "${tmp}/www-old/index.html"
printf '{"project_name":"mainsail","version":"v0.0.0-old"}\n' > "${tmp}/www-old/release_info.json"
if NETWORK_MARKER="${network_marker}" \
  BASH_ENV="${mock_env}" \
  REPO_DIR="${REPO_DIR}" \
  PI_USER="test" \
  PI_HOME="${tmp}/home" \
  TREED_MAINSAIL_LOCAL_ZIP="${tmp}/missing.zip" \
  TREED_MAINSAIL_WEB_PATH="${tmp}/www-old" \
  TREED_MAINSAIL_NGINX_SITE_AVAILABLE="${tmp}/nginx/available/mainsail-old" \
  TREED_MAINSAIL_NGINX_SITE_ENABLED="${tmp}/nginx/enabled/mainsail-old" \
  TREED_MAINSAIL_NGINX_DEFAULT_SITE_ENABLED="${tmp}/nginx/enabled/default-old" \
  bash "${REPO_DIR}/loader/steps/mainsail-web.sh" >/dev/null 2>&1
then
  printf 'old installed Mainsail unexpectedly passed manifest validation\n' >&2
  exit 1
fi

printf 'PASS: offline bundled Mainsail deployment\n'
