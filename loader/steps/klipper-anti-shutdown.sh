#!/usr/bin/env bash
set -Eeuo pipefail

# shellcheck disable=SC1091
. "$(dirname "$0")/../lib/common.sh"

ensure_root

STEP="klipper-anti-shutdown"
log_info "Step ${STEP}: clearing MCU shutdown if present"

PI_USER="${PI_USER:-${SUDO_USER:-$(id -un)}}"
PI_HOME="${PI_HOME:-$(getent passwd "${PI_USER}" | cut -d: -f6 || true)}"
if [ -z "${PI_HOME}" ] || [ ! -d "${PI_HOME}" ]; then
  log_error "${STEP}: cannot determine home for user ${PI_USER}"
  exit 1
fi

KLIPPER_SERVICE="${KLIPPER_SERVICE:-klipper}"

SOCK="${PI_HOME}/printer_data/comms/klippy.sock"
LOG="${PI_HOME}/printer_data/logs/klippy.log"

query_klippy_state() {
  local sock_path="$1"
  local timeout="${2:-2}"

  # Читаем state через Unix-сокет Klippy API (метод info).
  python3 - "${sock_path}" "${timeout}" <<'PY'
import json
import socket
import sys

if len(sys.argv) < 3:
    raise SystemExit(2)

sock_path = sys.argv[1]
timeout = float(sys.argv[2])

request = {"id": 1, "method": "info", "params": {}}
payload = (json.dumps(request) + "\x03").encode("utf-8")

sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.settimeout(timeout)

try:
    sock.connect(sock_path)
    sock.sendall(payload)

    data = b""
    while b"\x03" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
finally:
    sock.close()

if b"\x03" not in data:
    raise SystemExit(3)

raw = data.split(b"\x03", 1)[0]
msg = json.loads(raw.decode("utf-8", errors="replace"))

state = ""
if isinstance(msg, dict):
    result = msg.get("result")
    if isinstance(result, dict):
        state = str(result.get("state", "")).strip().lower()

if not state:
    raise SystemExit(4)

print(state)
PY
}

send_klippy_gcode() {
  local sock_path="$1"
  local timeout="${2:-2}"
  local gcode="$3"

  # Отправляем gcode/script в Klippy через тот же сокет API.
  python3 - "${sock_path}" "${timeout}" "${gcode}" <<'PY'
import json
import socket
import sys

if len(sys.argv) < 4:
    raise SystemExit(2)

sock_path = sys.argv[1]
timeout = float(sys.argv[2])
gcode = sys.argv[3]

request = {
    "id": 2,
    "method": "gcode/script",
    "params": {"script": gcode},
}
payload = (json.dumps(request) + "\x03").encode("utf-8")

sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.settimeout(timeout)

try:
    sock.connect(sock_path)
    sock.sendall(payload)

    data = b""
    while b"\x03" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
finally:
    sock.close()

if b"\x03" not in data:
    raise SystemExit(3)

raw = data.split(b"\x03", 1)[0]
msg = json.loads(raw.decode("utf-8", errors="replace"))

if not isinstance(msg, dict):
    raise SystemExit(4)
if "error" in msg:
    raise SystemExit(5)
PY
}

# Гарантируем, что Klipper запущен; рестарт нефатален, но обязательно логируется.
if ! systemctl is-active --quiet "${KLIPPER_SERVICE}"; then
  if err="$(systemctl restart "${KLIPPER_SERVICE}" 2>&1)"; then
    log_info "${STEP}: restarted ${KLIPPER_SERVICE}"
  else
    rc=$?
    log_warn "${STEP}: systemctl restart ${KLIPPER_SERVICE} failed rc=${rc}: ${err}"
  fi
fi
# Ждем появление klippy.sock до 30 секунд.
for _ in $(seq 1 30); do
  [ -S "$SOCK" ] && break
  sleep 1
done

if [ ! -S "$SOCK" ]; then
  log_warn "${STEP}: klippy.sock not found at ${SOCK}; retrying ${KLIPPER_SERVICE} restart"
  if err="$(systemctl restart "${KLIPPER_SERVICE}" 2>&1)"; then
    log_info "${STEP}: restarted ${KLIPPER_SERVICE}"
  else
    rc=$?
    log_warn "${STEP}: systemctl restart ${KLIPPER_SERVICE} failed rc=${rc}: ${err}"
  fi
  sleep 2
fi


if [ ! -S "$SOCK" ]; then
  log_warn "${STEP}: socket missing at ${SOCK}; skipping anti-shutdown"
  exit 0
fi

klippy_state=""
if command -v python3 >/dev/null 2>&1; then
  klippy_state="$(query_klippy_state "${SOCK}" "${TREED_ANTI_SHUTDOWN_INFO_TIMEOUT:-2}" 2>/dev/null || true)"
else
  log_warn "${STEP}: python3 not found; cannot query klippy state"
fi

if [ "${klippy_state}" = "shutdown" ]; then
  log_info "${STEP}: MCU state=shutdown; sending FIRMWARE_RESTART"
  if command -v python3 >/dev/null 2>&1; then
    if send_klippy_gcode "${SOCK}" "${TREED_ANTI_SHUTDOWN_INFO_TIMEOUT:-2}" "FIRMWARE_RESTART" >/dev/null 2>&1
    then
      :
    else
      rc=$?
      log_warn "${STEP}: failed to send FIRMWARE_RESTART via klippy API rc=${rc}"
    fi
    sleep 2
  else
    log_warn "${STEP}: cannot send FIRMWARE_RESTART (python3 required); skipping"
  fi
elif [ -n "${klippy_state}" ]; then
  log_info "${STEP}: klippy state=${klippy_state}; FIRMWARE_RESTART not required"
else
  log_warn "${STEP}: unable to query klippy state via ${SOCK}; anti-shutdown auto-restart skipped"
fi

if [ ! -f "$LOG" ]; then
  log_warn "${STEP}: klippy.log missing at ${LOG}; stats check skipped"
else
  if tail -n 200 "$LOG" | grep -q "Stats "; then
    log_info "${STEP}: Klipper is active; config accepted"
  else
    log_warn "${STEP}: no recent Stats lines in ${LOG}; please check logs"
  fi
fi

log_info "${STEP}: OK"
