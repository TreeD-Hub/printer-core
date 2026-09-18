#!/bin/bash
set -uo pipefail

# ==========================================
# ДИАГНОСТИКА: ОДИН ПРОГОН EDDY MESH
# ==========================================
# Назначение:
# - Собирает воспроизводимый пакет одного обычного Eddy scan.
# - Не меняет прошивки, ядро, параметры датчика, offset или геометрию.
# Контур:
# - required/read-only кроме одной явно отправленной mesh-команды.

# Блок 1: Неподменяемые идентификаторы и пути пакета.
RUN_ID="${TREED_EDDY_RUN_ID:-}"
COMMIT="${TREED_EDDY_REFERENCE_COMMIT:-6947713}"
CAN_IFACE="can0"
PI_HOME="${HOME}"
REPO_DIR="${PI_HOME}/treed/printer-core"
KLIPPY_LOG="${PI_HOME}/printer_data/logs/klippy.log"
RUNTIME_CFG="${PI_HOME}/printer_data/config/probe_eddy_duo.cfg"
PROFILE_PATH="klipper/profiles/treed_v2_corexy_v1/probe_eddy_duo.cfg"
OUT_ROOT="${PI_HOME}/treed/diagnostics"

if ! [[ "${RUN_ID}" =~ ^[A-Za-z0-9_]{8,80}$ ]]; then
  echo "TREED_EDDY_DIAG_ERROR: TREED_EDDY_RUN_ID must be 8-80 ASCII letters, digits, or underscores" >&2
  exit 2
fi
if ! [[ "${COMMIT}" =~ ^[0-9a-fA-F]{7,64}$ ]]; then
  echo "TREED_EDDY_DIAG_ERROR: TREED_EDDY_REFERENCE_COMMIT must be a Git commit prefix" >&2
  exit 2
fi
if [ "${TREED_EDDY_ALLOW_MOTION:-0}" != "1" ]; then
  echo "TREED_EDDY_DIAG_ERROR: set TREED_EDDY_ALLOW_MOTION=1 for the single approved scan" >&2
  exit 2
fi

RUN_DIR="${OUT_ROOT}/eddy-${RUN_ID}"
if [ -e "${RUN_DIR}" ]; then
  echo "TREED_EDDY_DIAG_ERROR: run package already exists: ${RUN_DIR}" >&2
  exit 2
fi

umask 077
mkdir -p "${RUN_DIR}"/{can,config,kernel,klipper,moonraker,system,versions}

SCAN_RESULT="not_started"
STARTED_AT=""
ENDED_AT=""
LOG_START_LINE=1
CANDUMP_PID=""

capture() {
  local relative_path="$1"
  shift
  local output="${RUN_DIR}/${relative_path}"
  local rc

  mkdir -p "$(dirname "${output}")"
  {
    printf '# captured_at='; date --iso-8601=seconds
    printf '# command='
    printf '%q ' "$@"
    printf '\n\n'
    "$@"
    rc=$?
    printf '\n# exit_code=%s\n' "${rc}"
  } >"${output}" 2>&1
}

capture_sh() {
  local relative_path="$1"
  local command="$2"
  capture "${relative_path}" bash -c "${command}"
}

record_failure() {
  printf '%s\n' "$1" >>"${RUN_DIR}/status.txt"
}

last_mcu_stats() {
  local target="$1"
  if [ -f "${KLIPPY_LOG}" ]; then
    grep '^Stats ' "${KLIPPY_LOG}" | tail -n 1 >"${target}" 2>&1 || true
  else
    printf 'klippy.log missing: %s\n' "${KLIPPY_LOG}" >"${target}"
  fi
}

write_mcu_delta() {
  python3 - "${RUN_DIR}/klipper/mcu-stats.before.txt" "${RUN_DIR}/klipper/mcu-stats.after.txt" <<'PY' >"${RUN_DIR}/klipper/mcu-stats.delta.txt"
import re
import sys

metrics = ("bytes_write", "bytes_read", "bytes_retransmit", "bytes_invalid")

def parse(path):
    try:
        line = open(path, encoding="utf-8", errors="replace").read().strip()
    except OSError as exc:
        return {}, str(exc)
    sections = re.split(r" (?=[^\s:]+: )", line)
    result = {}
    for section in sections:
        if ": " not in section:
            continue
        name, values = section.split(": ", 1)
        found = {key: int(value) for key, value in re.findall(r"\b(bytes_(?:write|read|retransmit|invalid))=(\d+)", values)}
        if found:
            result[name] = found
    return result, ""

before, before_error = parse(sys.argv[1])
after, after_error = parse(sys.argv[2])
if before_error or after_error:
    print("parse_error", before_error or after_error)
    raise SystemExit(1)

for name in sorted(set(before) | set(after)):
    print(name + ":")
    for key in metrics:
        old = before.get(name, {}).get(key)
        new = after.get(name, {}).get(key)
        if old is None or new is None:
            print(f"  {key}=unavailable before={old} after={new}")
        else:
            print(f"  {key}: before={old} after={new} delta={new - old}")
PY
}

stop_candump() {
  if [ -n "${CANDUMP_PID}" ] && kill -0 "${CANDUMP_PID}" 2>/dev/null; then
    kill "${CANDUMP_PID}" 2>/dev/null || true
    wait "${CANDUMP_PID}" 2>/dev/null || true
  fi
}

finalize() {
  local rc=$?
  trap - EXIT
  set +e

  stop_candump
  ENDED_AT="$(date --iso-8601=seconds)"
  last_mcu_stats "${RUN_DIR}/klipper/mcu-stats.after.txt"
  if ! write_mcu_delta; then
    record_failure "mcu_stats_delta_unavailable"
    if [ "${rc}" -eq 0 ]; then
      rc=22
    fi
  fi

  if [ -f "${KLIPPY_LOG}" ]; then
    sed -n "${LOG_START_LINE},\$p" "${KLIPPY_LOG}" >"${RUN_DIR}/klipper/klippy.interval.log"
  fi
  capture "can/ip-details.after.txt" ip -details -statistics link show "${CAN_IFACE}"
  capture "kernel/messages.interval.txt" journalctl -k --since "${STARTED_AT:-now}" --until "${ENDED_AT}" --no-pager
  capture "moonraker/gcode-store.after.json" curl -fsS "http://127.0.0.1:7125/server/gcode_store?count=200"

  {
    printf 'run_id=%s\n' "${RUN_ID}"
    printf 'reference_commit=%s\n' "${COMMIT}"
    printf 'started_at=%s\n' "${STARTED_AT:-not_started}"
    printf 'ended_at=%s\n' "${ENDED_AT}"
    printf 'scan_result=%s\n' "${SCAN_RESULT}"
    printf 'exit_code=%s\n' "${rc}"
    printf 'scan_command=TREED_BED_MESH_CALIBRATE_EDDY PROFILE=eddy_diag_%s METHOD=scan\n' "${RUN_ID}"
    printf 'save_config_sent=0\nrestart_sent=0\n'
  } >"${RUN_DIR}/manifest.env"

  if ! tar -C "${OUT_ROOT}" -czf "${OUT_ROOT}/eddy-${RUN_ID}.tar.gz" "eddy-${RUN_ID}"; then
    record_failure "archive_create_failed"
    if [ "${rc}" -eq 0 ]; then
      rc=23
    fi
  fi
  printf 'TREED_EDDY_DIAG_PACKAGE=%s\n' "${RUN_DIR}"
  printf 'TREED_EDDY_DIAG_ARCHIVE=%s\n' "${OUT_ROOT}/eddy-${RUN_ID}.tar.gz"
  exit "${rc}"
}
trap finalize EXIT

# Блок 2: Read-only снимок загруженной конфигурации, версий и владельца can0.
capture "versions/host.txt" bash -c 'hostnamectl; uname -a; cat /etc/os-release'
capture "versions/klipper.txt" git -C "${PI_HOME}/klipper" log -1 --format='commit=%H%nsubject=%s'
capture "versions/driver-gs_usb.txt" modinfo gs_usb
capture "versions/usb-can.txt" lsusb -d 1d50:606f
capture_sh "versions/mcu-from-klippy.txt" "grep -E '^Loaded MCU' '${KLIPPY_LOG}' || true"
capture "can/ip-details.before.txt" ip -details -statistics link show "${CAN_IFACE}"
capture "can/ethtool-driver.txt" ethtool -i "${CAN_IFACE}"
capture "system/can-unit.txt" systemctl cat treed-can-setup.service
capture "system/can-unit-properties.txt" systemctl show treed-can-setup.service -p Before -p After -p Wants -p Requires -p Conflicts -p ActiveState
capture "system/can-unit-dependencies.txt" systemctl list-dependencies --all treed-can-setup.service
capture "system/klipper-unit-properties.txt" systemctl show klipper.service -p Before -p After -p Wants -p Requires -p Conflicts -p ActiveState
capture "system/klipper-unit-dependencies.txt" systemctl list-dependencies --all klipper.service
capture_sh "config/runtime-printer-include.txt" "grep -nF 'probe_eddy_duo.cfg' '${PI_HOME}/printer_data/config/printer.cfg' || true"
capture "moonraker/configfile-before.json" curl -fsS "http://127.0.0.1:7125/printer/objects/query?configfile"
capture "moonraker/printer-info-before.json" curl -fsS "http://127.0.0.1:7125/printer/info"
capture "moonraker/gcode-store.before.json" curl -fsS "http://127.0.0.1:7125/server/gcode_store?count=200"

if ! git -C "${REPO_DIR}" cat-file -e "${COMMIT}^{commit}" 2>"${RUN_DIR}/config/reference-commit.error.txt"; then
  SCAN_RESULT="reference_commit_missing"
  record_failure "reference_commit_missing=${COMMIT}"
  exit 3
fi
if [ ! -f "${RUNTIME_CFG}" ]; then
  SCAN_RESULT="runtime_macro_missing"
  record_failure "runtime_macro_missing=${RUNTIME_CFG}"
  exit 3
fi
if [ ! -f "${KLIPPY_LOG}" ]; then
  SCAN_RESULT="klippy_log_missing"
  record_failure "klippy_log_missing=${KLIPPY_LOG}"
  exit 3
fi

git -C "${REPO_DIR}" show "${COMMIT}:${PROFILE_PATH}" >"${RUN_DIR}/config/reference.cfg"
awk '
  $0 == "[gcode_macro TREED_BED_MESH_CALIBRATE_EDDY]" { active=1 }
  active && /^\[/ && $0 != "[gcode_macro TREED_BED_MESH_CALIBRATE_EDDY]" { exit }
  active { print }
' "${RUNTIME_CFG}" >"${RUN_DIR}/config/runtime-macro.cfg"
awk '
  $0 == "[gcode_macro TREED_BED_MESH_CALIBRATE_EDDY]" { active=1 }
  active && /^\[/ && $0 != "[gcode_macro TREED_BED_MESH_CALIBRATE_EDDY]" { exit }
  active { print }
' "${RUN_DIR}/config/reference.cfg" >"${RUN_DIR}/config/reference-macro.cfg"
if [ ! -s "${RUN_DIR}/config/runtime-macro.cfg" ] || [ ! -s "${RUN_DIR}/config/reference-macro.cfg" ]; then
  SCAN_RESULT="mesh_macro_extract_missing"
  record_failure "mesh_macro_extract_missing"
  exit 3
fi
sha256sum "${RUNTIME_CFG}" "${RUN_DIR}/config/runtime-macro.cfg" "${RUN_DIR}/config/reference-macro.cfg" >"${RUN_DIR}/config/checksums.sha256"
if diff -u "${RUN_DIR}/config/reference-macro.cfg" "${RUN_DIR}/config/runtime-macro.cfg" >"${RUN_DIR}/config/macro.diff"; then
  printf 'macro_match=1\n' >"${RUN_DIR}/config/macro-match.env"
else
  SCAN_RESULT="runtime_macro_differs_from_reference"
  printf 'macro_match=0\n' >"${RUN_DIR}/config/macro-match.env"
  record_failure "runtime_macro_differs_from_reference=${COMMIT}"
  exit 4
fi

for required_command in awk candump curl diff ethtool git grep ip journalctl lsusb modinfo python3 sed sha256sum systemctl tar wc; do
  if ! command -v "${required_command}" >/dev/null 2>&1; then
    SCAN_RESULT="missing_${required_command}"
    record_failure "missing_required_command=${required_command}"
  fi
done
if [ "${SCAN_RESULT}" != "not_started" ]; then
  exit 5
fi
if ! ip link show "${CAN_IFACE}" >/dev/null 2>&1; then
  SCAN_RESULT="can_interface_missing"
  record_failure "can_interface_missing=${CAN_IFACE}"
  exit 5
fi
if ! python3 - "${RUN_DIR}/moonraker/printer-info-before.json" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
state = payload.get("result", {}).get("state", "")
if state != "ready":
    raise SystemExit(f"printer_state_not_ready={state or 'missing'}")
PY
then
  SCAN_RESULT="printer_not_ready"
  record_failure "printer_not_ready"
  exit 5
fi

# Блок 3: Один обычный scan и синхронный пассивный захват интервала.
LOG_START_LINE=$(( $(wc -l < "${KLIPPY_LOG}") + 1 ))
last_mcu_stats "${RUN_DIR}/klipper/mcu-stats.before.txt"
STARTED_AT="$(date --iso-8601=seconds)"
candump -L "${CAN_IFACE}" >"${RUN_DIR}/can/candump.log" 2>"${RUN_DIR}/can/candump.error.log" &
CANDUMP_PID=$!
sleep 1
if ! kill -0 "${CANDUMP_PID}" 2>/dev/null; then
  SCAN_RESULT="candump_failed"
  record_failure "candump_failed"
  exit 6
fi

PROFILE="eddy_diag_${RUN_ID}"
GCODE=$'RESPOND PREFIX=eddy_diag MSG="BEGIN run='"${RUN_ID}"$'"\n'
GCODE+="TREED_BED_MESH_CALIBRATE_EDDY PROFILE=${PROFILE} METHOD=scan"
GCODE+=$'\nM400\n'
GCODE+=$'RESPOND PREFIX=eddy_diag MSG="END run='"${RUN_ID}"$'"'
printf '%s\n' "${GCODE}" >"${RUN_DIR}/klipper/mesh-command.gcode"
python3 -c 'import json, sys; print(json.dumps({"script": sys.stdin.read()}))' <<<"${GCODE}" \
  | curl -fsS -H 'Content-Type: application/json' -X POST --data-binary @- \
      "http://127.0.0.1:7125/printer/gcode/script" >"${RUN_DIR}/moonraker/gcode-submit.json" || {
  SCAN_RESULT="gcode_submit_failed"
  record_failure "gcode_submit_failed"
  exit 20
}

SCAN_RESULT="waiting"
for ((waited=0; waited<1200; waited++)); do
  curl -fsS "http://127.0.0.1:7125/server/gcode_store?count=200" >"${RUN_DIR}/moonraker/gcode-store.current.json" 2>/dev/null || true
  if grep -Fq "END run=${RUN_ID}" "${RUN_DIR}/moonraker/gcode-store.current.json"; then
    SCAN_RESULT="completed"
    exit 0
  fi
  if tail -n "+${LOG_START_LINE}" "${KLIPPY_LOG}" 2>/dev/null \
    | grep -Eqi 'Unable to obtain probe_eddy_current sensor readings|probe_eddy_current sensor not in valid range|TREED_BED_MESH_CALIBRATE_EDDY:'; then
    SCAN_RESULT="eddy_error"
    record_failure "eddy_error_detected"
    exit 20
  fi
  sleep 1
done

SCAN_RESULT="result_timeout"
record_failure "result_timeout_no_repeat_sent"
exit 21
