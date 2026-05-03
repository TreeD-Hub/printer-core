#!/bin/bash
set -euo pipefail

# ==========================================
# ШАГ LOADER: MAINSAIL WEB
# ==========================================
# Назначение:
# - Разворачивает web-слой Mainsail (статический frontend + nginx reverse proxy).
# - Обеспечивает валидный локальный путь Mainsail для Moonraker update_manager.
# Контур:
# - required (без web-слоя UI недоступен по HTTP).

# Блок 1: Библиотеки, root-права и определение целевого пользователя.
. "${REPO_DIR}/loader/lib/common.sh"
ensure_root

PI_USER="${PI_USER:-${SUDO_USER:-pi}}"
PI_HOME="${PI_HOME:-/home/${PI_USER}}"
if [ -z "${PI_HOME}" ] || [ ! -d "${PI_HOME}" ]; then
  PI_HOME="$(getent passwd "${PI_USER}" | cut -d: -f6 || true)"
fi
if [ -z "${PI_HOME}" ] || [ ! -d "${PI_HOME}" ]; then
  log_error "mainsail-web: cannot determine home for user ${PI_USER}"
  exit 1
fi

if ! grp="$(pi_primary_group "${PI_USER}")"; then
  exit 1
fi

# Блок 2: Конфигурация путей/URL с безопасными дефолтами.
TREED_MAINSAIL_WEB_PATH="${TREED_MAINSAIL_WEB_PATH:-/var/www/mainsail}"
TREED_MAINSAIL_ZIP_URL="${TREED_MAINSAIL_ZIP_URL:-https://github.com/mainsail-crew/mainsail/releases/latest/download/mainsail.zip}"
TREED_MAINSAIL_NGINX_SITE_AVAILABLE="${TREED_MAINSAIL_NGINX_SITE_AVAILABLE:-/etc/nginx/sites-available/mainsail}"
TREED_MAINSAIL_NGINX_SITE_ENABLED="${TREED_MAINSAIL_NGINX_SITE_ENABLED:-/etc/nginx/sites-enabled/mainsail}"
TREED_MAINSAIL_NGINX_DEFAULT_SITE_ENABLED="${TREED_MAINSAIL_NGINX_DEFAULT_SITE_ENABLED:-/etc/nginx/sites-enabled/default}"
TREED_MAINSAIL_MOONRAKER_PROXY_URL="${TREED_MAINSAIL_MOONRAKER_PROXY_URL:-http://127.0.0.1:7125}"

log_info "Step mainsail-web: deploying Mainsail UI + nginx proxy"
log_info "mainsail-web: web_path=${TREED_MAINSAIL_WEB_PATH}, zip_url=${TREED_MAINSAIL_ZIP_URL}"

# Блок 3: Установка зависимостей web-слоя.
apt_update_noninteractive
apt_get_noninteractive install nginx wget unzip acl

# Блок 4: Загрузка и раскладка фронтенда Mainsail.
tmp_dir="$(mktemp -d "/tmp/treed_mainsail_web_XXXXXX")"
cleanup_tmp_dir() {
  if [ -n "${tmp_dir:-}" ] && [ -d "${tmp_dir}" ]; then
    rm -rf "${tmp_dir}"
  fi
}
trap cleanup_tmp_dir EXIT

wget -q -O "${tmp_dir}/mainsail.zip" "${TREED_MAINSAIL_ZIP_URL}"
mkdir -p "${tmp_dir}/unpack"
unzip -q "${tmp_dir}/mainsail.zip" -d "${tmp_dir}/unpack"

if [ ! -f "${tmp_dir}/unpack/release_info.json" ]; then
  log_error "mainsail-web: release_info.json not found in downloaded archive"
  exit 1
fi

ensure_dir "${TREED_MAINSAIL_WEB_PATH}"
rsync -a --delete "${tmp_dir}/unpack/" "${TREED_MAINSAIL_WEB_PATH}/"
if [ ! -f "${TREED_MAINSAIL_WEB_PATH}/index.html" ]; then
  log_error "mainsail-web: index.html not found after sync: ${TREED_MAINSAIL_WEB_PATH}/index.html"
  exit 1
fi

if [[ "${TREED_MAINSAIL_WEB_PATH}" == "/home/${PI_USER}/"* ]]; then
  chown -R "${PI_USER}:${grp}" "${TREED_MAINSAIL_WEB_PATH}"
  setfacl -m "u:www-data:x" "${PI_HOME}"
else
  chown -R root:root "${TREED_MAINSAIL_WEB_PATH}"
  find "${TREED_MAINSAIL_WEB_PATH}" -type d -exec chmod 755 {} \;
  find "${TREED_MAINSAIL_WEB_PATH}" -type f -exec chmod 644 {} \;
fi

  if ! sudo -u www-data test -r "${TREED_MAINSAIL_WEB_PATH}/index.html"; then
  log_error "mainsail-web: www-data cannot read ${TREED_MAINSAIL_WEB_PATH}/index.html"
  exit 1
fi

log_info "mainsail-web: synced web root to ${TREED_MAINSAIL_WEB_PATH}"

# Блок 5: Генерация nginx-конфига с проксированием Moonraker.
ensure_dir "$(dirname "${TREED_MAINSAIL_NGINX_SITE_AVAILABLE}")"
cat > "${TREED_MAINSAIL_NGINX_SITE_AVAILABLE}" <<EOF
server {
    listen 80 default_server;
    listen [::]:80 default_server;

    server_name _;
    root ${TREED_MAINSAIL_WEB_PATH};
    index index.html;

    client_max_body_size 1024m;

    location / {
        try_files \$uri \$uri/ /index.html;
    }

    location /websocket {
        proxy_pass ${TREED_MAINSAIL_MOONRAKER_PROXY_URL}/websocket;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$http_host;
        proxy_read_timeout 86400;
    }

    location /printer {
        proxy_pass ${TREED_MAINSAIL_MOONRAKER_PROXY_URL}/printer;
        proxy_set_header Host \$http_host;
    }

    location /api {
        proxy_pass ${TREED_MAINSAIL_MOONRAKER_PROXY_URL}/api;
        proxy_set_header Host \$http_host;
    }

    location /access {
        proxy_pass ${TREED_MAINSAIL_MOONRAKER_PROXY_URL}/access;
        proxy_set_header Host \$http_host;
    }

    location /machine {
        proxy_pass ${TREED_MAINSAIL_MOONRAKER_PROXY_URL}/machine;
        proxy_set_header Host \$http_host;
    }

    location /server {
        proxy_pass ${TREED_MAINSAIL_MOONRAKER_PROXY_URL}/server;
        proxy_set_header Host \$http_host;
    }

    location = /webcam {
        return 301 /webcam/;
    }

    location /webcam/ {
        proxy_pass http://127.0.0.1:8080/;
        proxy_set_header Host \$http_host;
    }
}
EOF

# Блок 6: Активация nginx-site и запуск nginx.
ensure_dir "$(dirname "${TREED_MAINSAIL_NGINX_SITE_ENABLED}")"
rm -f "${TREED_MAINSAIL_NGINX_DEFAULT_SITE_ENABLED}" || true
ln -sfn "${TREED_MAINSAIL_NGINX_SITE_AVAILABLE}" "${TREED_MAINSAIL_NGINX_SITE_ENABLED}"

nginx -t
systemctl enable --now nginx
systemctl restart nginx

if systemctl is-active --quiet nginx.service; then
  log_info "mainsail-web: nginx.service active"
else
  state="$(systemctl is-active nginx.service 2>/dev/null || true)"
  log_error "mainsail-web: nginx.service is not active (state=${state:-unknown})"
  exit 1
fi

status_code="$(curl -m 8 -sS -o /dev/null -w '%{http_code}' "http://127.0.0.1/" || true)"
if [ "${status_code}" = "200" ]; then
  log_info "mainsail-web: local HTTP root is reachable (http=${status_code})"
else
  log_error "mainsail-web: local HTTP root check failed (http=${status_code:-n/a})"
  exit 1
fi

log_info "mainsail-web: OK"
