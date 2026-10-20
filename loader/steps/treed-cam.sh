#!/bin/bash
set -euo pipefail

# ==========================================
# ШАГ LOADER: TREED CAM
# ==========================================
# Назначение:
# - Разворачивает runtime-скрипты камеры TreeD в домашний каталог пользователя.
# - Гарантирует права, структуру каталогов и исполняемость скриптов.
# Контур:
# - обязательный шаг для скриптов камеры и локального CPU IPC; без офлайн-моделей анализ недоступен.

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
LIB_DIR="${REPO_DIR}/loader/lib"
# Блок 1: Библиотеки и базовая инициализация.
source "${LIB_DIR}/common.sh"

# Блок 2: Старт шага и чтение пользовательского контекста.
log_info "Step treed-cam"


PI_USER="${PI_USER:-pi}"
PI_HOME="${PI_HOME:-/home/${PI_USER}}"
if ! grp="$(pi_primary_group "${PI_USER}")"; then
  exit 1
fi

# Блок 3: Расчет путей source/destination.
SRC_DIR="${REPO_DIR}/runtime-scripts/treed-cam"
VISION_SRC="${REPO_DIR}/runtime-scripts/treed-vision"
DST_ROOT="${PI_HOME}/treed/cam"
DST_BIN="${DST_ROOT}/bin"
DST_DATA="${DST_ROOT}/prints"
VISION_ROOT="${PI_HOME}/treed/vision"
VISION_ENV="${VISION_ROOT}/venv"
VISION_ENV_FILE="/etc/default/treed-vision"
for path in "${DST_ROOT}" "${DST_BIN}" "${VISION_ROOT}" "${VISION_ROOT}/bin" "${VISION_ROOT}/models"; do
  if [[ -L "${path}" ]]; then
    log_error "Путь runtime является symlink: ${path}"
    exit 1
  fi
done

# Блок 4: Запрещаем замену файлов/моделей во время печати и останавливаем IPC-контур.
if systemctl is-active --quiet moonraker.service; then
  print_state="$(curl --noproxy '*' -fsS --max-time 4 'http://127.0.0.1:7125/printer/objects/query?print_stats=state' |
    python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["status"]["print_stats"]["state"])' 2>/dev/null)" || {
      log_error "Не удалось проверить состояние печати перед обновлением vision runtime"
      exit 1
    }
  case "${print_state,,}" in
    standby|complete|cancelled|error) ;;
    *) log_error "Обновление vision runtime/модели запрещено при состоянии принтера ${print_state}"; exit 1 ;;
  esac
elif systemctl is-active --quiet klipper.service; then
  log_error "Нельзя проверить состояние автономной печати перед обновлением vision runtime"
  exit 1
fi
VISION_WAS_ACTIVE=0
DETECTION_WAS_ACTIVE=0
systemctl is-active --quiet treed-vision.service && VISION_WAS_ACTIVE=1 || true
systemctl is-active --quiet treed-detection.service && DETECTION_WAS_ACTIVE=1 || true
restart_vision_after_failure() {
  local result=$?
  if [[ "$result" -ne 0 ]]; then
    if [[ "$VISION_WAS_ACTIVE" = "1" ]]; then
      systemctl start treed-vision.service || log_warn "Не удалось повторно запустить прежний treed-vision.service"
    fi
    if [[ "$DETECTION_WAS_ACTIVE" = "1" ]]; then
      systemctl start treed-detection.service || log_warn "Не удалось повторно запустить прежний treed-detection.service"
    fi
  fi
  return "$result"
}
trap restart_vision_after_failure EXIT
for unit in treed-detection.service treed-vision.service; do
  if systemctl cat "${unit}" >/dev/null 2>&1; then
    systemctl stop "${unit}"
    for _ in $(seq 1 10); do
      systemctl is-active --quiet "${unit}" || break
      sleep 1
    done
    if systemctl is-active --quiet "${unit}"; then
      log_error "${unit} не остановился перед обновлением runtime/модели"
      exit 1
    fi
  fi
done

# Блок 5: Подготовка директорий назначения.
ensure_dir "${DST_BIN}"
ensure_dir "${DST_DATA}"
ensure_dir "${DST_ROOT}/config"
ensure_dir "${VISION_ROOT}"

if [[ ! -d "${SRC_DIR}" ]]; then
  log_error "Missing runtime scripts directory: ${SRC_DIR}"
  exit 1
fi

# Блок 6: Атомарная синхронизация runtime-скриптов после quiescence сервисов.
if [[ "${TREED_UPDATE_MANAGED_LOADER:-0}" != "1" ]]; then
log_info "Синхронизация runtime-скриптов: ${SRC_DIR} -> ${DST_BIN}"
camera_stage="$(mktemp -d "${DST_ROOT}/.bin.next.XXXXXX")"
vision_stage="$(mktemp -d "${VISION_ROOT}/.bin.next.XXXXXX")"
camera_old="${DST_ROOT}/bin.previous.$$"
vision_old="${VISION_ROOT}/bin.previous.$$"
if [[ -e "${camera_old}" || -L "${camera_old}" || -e "${vision_old}" || -L "${vision_old}" ]]; then
  log_error "Предыдущая замена runtime прервалась; требуется ручное восстановление"
  exit 1
fi
cp -a "${SRC_DIR}/." "${camera_stage}/"
cp -a "${VISION_SRC}/." "${vision_stage}/"
find "${camera_stage}" -type f -name '*.sh' -exec chmod +x {} \;
if [[ -e "${DST_BIN}" || -L "${DST_BIN}" ]]; then mv "${DST_BIN}" "${camera_old}"; fi
if ! mv "${camera_stage}" "${DST_BIN}"; then
  [[ ! -e "${camera_old}" && ! -L "${camera_old}" ]] || mv "${camera_old}" "${DST_BIN}"
  exit 1
fi
if [[ -e "${VISION_ROOT}/bin" || -L "${VISION_ROOT}/bin" ]]; then mv "${VISION_ROOT}/bin" "${vision_old}"; fi
if ! mv "${vision_stage}" "${VISION_ROOT}/bin"; then
  [[ ! -e "${vision_old}" && ! -L "${vision_old}" ]] || mv "${vision_old}" "${VISION_ROOT}/bin"
  [[ ! -e "${camera_old}" && ! -L "${camera_old}" ]] || { rm -rf "${DST_BIN}"; mv "${camera_old}" "${DST_BIN}"; }
  exit 1
fi
rm -rf "${camera_old}" "${vision_old}"
fi

# Блок 7: Исполняемость shell-скриптов и права владельца.
if [[ "${TREED_UPDATE_MANAGED_LOADER:-0}" = "1" ]]; then
  for runtime_dir in "${DST_ROOT}" "${DST_BIN}" "${DST_DATA}" "${DST_ROOT}/config"; do
    [[ ! -d "${runtime_dir}" ]] || chown "${PI_USER}:${grp}" "${runtime_dir}" || true
  done
else
  chown -R "${PI_USER}:${grp}" "${DST_ROOT}" || true
fi

# Блок 8: Офлайн-модели принимаются только из явно заданных локальных артефактов.
install_model() {
  local source="$1" destination="$2" expected_size="$3" expected_sha="$4"
  [[ -n "$source" ]] || return 0
  [[ "$source" = /* && -f "$source" && ! -L "$source" ]] || { log_warn "Некорректный локальный model artifact: $source"; return 0; }
  install -d -m 0750 "${VISION_ROOT}/models"
  local temporary actual_size actual_sha
  temporary="$(mktemp "${destination}.tmp.XXXXXX")"
  if ! cat -- "$source" > "$temporary"; then rm -f -- "$temporary"; return 0; fi
  actual_size="$(stat -c '%s' "$temporary")"
  actual_sha="$(sha256sum "$temporary" | awk '{print $1}')"
  if [[ "$actual_size" != "$expected_size" || "$actual_sha" != "$expected_sha" ]]; then
    rm -f -- "$temporary"
    log_warn "Локальный model artifact не прошел проверку: $source"
    return 0
  fi
  chmod 0640 "$temporary"
  python3 - "$temporary" <<'PY'
import os, sys
descriptor = os.open(sys.argv[1], os.O_RDONLY)
try: os.fsync(descriptor)
finally: os.close(descriptor)
PY
  mv -f -- "$temporary" "$destination"
  if [[ "${TREED_UPDATE_MANAGED_LOADER:-0}" = "1" ]]; then
    chown "${PI_USER}:${grp}" "$destination" || true
  fi
  python3 - "${VISION_ROOT}/models" <<'PY'
import os, sys
descriptor = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY)
try: os.fsync(descriptor)
finally: os.close(descriptor)
PY
}
install_model "${TREED_VISION_SPAGHETTI_ARTIFACT:-}" "${VISION_ROOT}/models/spaghetti_presence_v12.pth" \
  44790539 95c6e676301c77f294250961f77e5773707a0a0d57a5b7fe7e8b6fbe54eb81e9
install_model "${TREED_VISION_BED_ARTIFACT:-}" "${VISION_ROOT}/models/fdm-detail-e0-20261016-8de7e8aa22e2.pth" \
  127479855 8de7e8aa22e20893a563c0c04d28f0019dcad9ef8d805818df6fd2ec8b10866e

vision_python_valid() {
  local candidate="$1"
  [[ "${candidate}" = /* && -x "${candidate}" && "${candidate}" != *[[:space:]]* ]] &&
    "${candidate}" -c 'import importlib.metadata as m; assert m.version("torch").split("+")[0] == "2.11.0" and m.version("torchvision").split("+")[0] == "0.26.0" and m.version("rfdetr") == "1.11.0" and m.version("Pillow") == "12.3.0"' >/dev/null 2>&1
}

# Блок 9: Используется проверенный локальный runtime или ставятся закрепленные CPU-зависимости.
if [[ -n "${TREED_VISION_WHEELHOUSE:-}" && -d "${TREED_VISION_WHEELHOUSE}" ]] &&
  ! vision_python_valid "${VISION_ENV}/bin/python" &&
  { [[ "${TREED_UPDATE_MANAGED_LOADER:-0}" != "1" ]] || ! vision_python_valid "${PI_HOME}/treed/autoprinter-bed-env/bin/python"; }; then
  if { [[ -x "${VISION_ENV}/bin/python" ]] || python3 -m venv "${VISION_ENV}"; } && \
    "${VISION_ENV}/bin/pip" install --no-index --find-links "${TREED_VISION_WHEELHOUSE}" \
      'torch==2.11.0' 'torchvision==0.26.0' 'rfdetr==1.11.0' 'Pillow==12.3.0'; then
    log_info "Installed pinned vision runtime from local wheelhouse"
  else
    log_warn "Локальный wheelhouse неполон; AI-анализ останется недоступен"
  fi
fi
VISION_PYTHON=""
if [[ -n "${TREED_VISION_PYTHON:-}" ]]; then
  VISION_PYTHON="${TREED_VISION_PYTHON}"
elif [[ -f "${VISION_ENV_FILE}" ]]; then
  VISION_PYTHON="$(sed -n 's/^VISION_PYTHON=//p' "${VISION_ENV_FILE}" | head -n 1)"
elif [[ -f /etc/systemd/system/treed-vision.service ]]; then
  VISION_PYTHON="$(sed -n 's/^ExecStart=\([^ ]*\) -u .*$/\1/p' /etc/systemd/system/treed-vision.service | head -n 1)"
fi
if ! vision_python_valid "${VISION_PYTHON}"; then
  if [[ "${TREED_UPDATE_MANAGED_LOADER:-0}" = "1" ]] && vision_python_valid "${PI_HOME}/treed/autoprinter-bed-env/bin/python"; then
    VISION_PYTHON="${PI_HOME}/treed/autoprinter-bed-env/bin/python"
  fi
fi
if ! vision_python_valid "${VISION_PYTHON}"; then
  if vision_python_valid "${VISION_ENV}/bin/python"; then
    VISION_PYTHON="${VISION_ENV}/bin/python"
  elif [[ "${TREED_UPDATE_MANAGED_LOADER:-0}" = "1" ]]; then
    python3 -m venv "${VISION_ENV}"
    if [[ -n "${TREED_VISION_WHEELHOUSE:-}" && -d "${TREED_VISION_WHEELHOUSE}" ]]; then
      "${VISION_ENV}/bin/pip" install --no-index --find-links "${TREED_VISION_WHEELHOUSE}" \
        'torch==2.11.0' 'torchvision==0.26.0' 'rfdetr==1.11.0' 'Pillow==12.3.0'
    else
      "${VISION_ENV}/bin/pip" install --index-url https://download.pytorch.org/whl/cpu \
        'torch==2.11.0' 'torchvision==0.26.0'
      "${VISION_ENV}/bin/pip" install 'rfdetr==1.11.0' 'Pillow==12.3.0'
    fi
    VISION_PYTHON="${VISION_ENV}/bin/python"
    vision_python_valid "${VISION_PYTHON}" || { log_error "Pinned CPU vision runtime installation failed validation"; exit 1; }
  else
    VISION_PYTHON="/usr/bin/python3"
    log_warn "Локальный Python vision с нужными версиями недоступен; AI ответит unavailable"
  fi
fi
printf 'VISION_PYTHON=%s\n' "${VISION_PYTHON}" > "${VISION_ENV_FILE}.tmp"
chmod 0644 "${VISION_ENV_FILE}.tmp"
mv -f "${VISION_ENV_FILE}.tmp" "${VISION_ENV_FILE}"
install -d -m 0700 "${VISION_ROOT}/run"
if [[ "${TREED_UPDATE_MANAGED_LOADER:-0}" = "1" ]]; then
  for runtime_dir in "${VISION_ROOT}" "${VISION_ROOT}/bin" "${VISION_ROOT}/models" "${VISION_ROOT}/run"; do
    [[ ! -d "${runtime_dir}" ]] || chown "${PI_USER}:${grp}" "${runtime_dir}" || true
  done
else
  chown -R "${PI_USER}:${grp}" "${VISION_ROOT}"
fi

# Блок 10: Сервис ограничен локальным Unix socket и ресурсами systemd.
unit_tmp="$(mktemp /etc/systemd/system/treed-vision.service.XXXXXX)"
cat > "${unit_tmp}" <<EOF
[Unit]
Description=TreeD local CPU vision service
After=local-fs.target

[Service]
User=${PI_USER}
ExecStart=${VISION_PYTHON} -u ${VISION_ROOT}/bin/service.py
WorkingDirectory=${VISION_ROOT}
RuntimeDirectory=treed-vision
RuntimeDirectoryMode=0700
Environment=OMP_NUM_THREADS=2
Environment=MKL_NUM_THREADS=2
Environment=OPENBLAS_NUM_THREADS=2
Environment=NUMEXPR_NUM_THREADS=2
MemoryMax=1536M
CPUQuota=150%
TasksMax=32
Nice=10
MemorySwapMax=0
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=${VISION_ROOT}/run
RestrictAddressFamilies=AF_UNIX

[Install]
WantedBy=multi-user.target
EOF
chmod 0644 "${unit_tmp}"
mv -f "${unit_tmp}" /etc/systemd/system/treed-vision.service
systemctl daemon-reload
systemctl enable treed-vision.service
systemctl restart treed-vision.service || log_warn "treed-vision: локальный анализ недоступен"

# Блок 11: Камера и Moonraker соединяются только локально с IPC/HTTP endpoints.
unit_tmp="$(mktemp /etc/systemd/system/treed-detection.service.XXXXXX)"
cat > "${unit_tmp}" <<EOF
[Unit]
Description=TreeD local camera detection adapter
After=treed-vision.service moonraker.service crowsnest.service
Wants=treed-vision.service moonraker.service crowsnest.service

[Service]
User=${PI_USER}
EnvironmentFile=-${DST_ROOT}/config/runtime.env
ExecStart=${VISION_PYTHON} -u ${DST_BIN}/stream_detect.py --react --fps 0.2 --vision-socket ${VISION_ROOT}/run/vision.sock
UnsetEnvironment=DETECT_API_KEY TREED_DETECT_SERVER_URL
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
chmod 0644 "${unit_tmp}"
mv -f "${unit_tmp}" /etc/systemd/system/treed-detection.service
systemctl enable treed-detection.service
systemctl restart treed-detection.service || log_warn "treed-detection: локальный анализ недоступен"

log_info "treed-cam: DONE (data at ${DST_DATA})"
