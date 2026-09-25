#!/bin/bash
set -uo pipefail

# ==========================================
# ДИАГНОСТИКА: ПАССИВНЫЙ CAN/MCU ПАКЕТ И ОПЦИОНАЛЬНЫЙ EDDY SCAN
# ==========================================
# Назначение:
# - По умолчанию пассивно собирает ограниченный пакет host/CAN/Klipper/Moonraker.
# - Eddy scan доступен только отдельным mode и явным разрешением движения.
# Контур:
# - read-only в mode=passive; один mesh в mode=eddy-scan; явные серии в acceptance.

# Блок 1: Неподменяемые идентификаторы и пути пакета.
RUN_ID="${TREED_EDDY_RUN_ID:-}"
MODE="${TREED_DIAGNOSTIC_MODE:-passive}"
CAN_IFACE="can0"
PI_HOME="${HOME}"
REPO_DIR="${PI_HOME}/treed/printer-core"
COMMIT="${TREED_EDDY_REFERENCE_COMMIT:-$(git -C "${REPO_DIR}" rev-parse HEAD 2>/dev/null)}"
KLIPPY_LOG="${PI_HOME}/printer_data/logs/klippy.log"
MOONRAKER_LOG="${PI_HOME}/printer_data/logs/moonraker.log"
RUNTIME_CFG="${PI_HOME}/printer_data/config/profiles/treed_v2_corexy_v1/probe_eddy_duo.cfg"
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
case "${MODE}" in
  passive|eddy-scan|acceptance) ;;
  *)
    echo "TREED_EDDY_DIAG_ERROR: TREED_DIAGNOSTIC_MODE must be passive, eddy-scan or acceptance" >&2
    exit 2
    ;;
esac
if [ "${MODE}" = "eddy-scan" ] && [ "${TREED_EDDY_ALLOW_MOTION:-0}" != "1" ]; then
  echo "TREED_EDDY_DIAG_ERROR: set TREED_EDDY_ALLOW_MOTION=1 for the single approved scan" >&2
  exit 2
fi
if [ "${MODE}" = "acceptance" ] && [ "${TREED_ACCEPTANCE_ALLOW_MOTION:-0}" != "1" ]; then
  echo "TREED_EDDY_DIAG_ERROR: acceptance requires TREED_ACCEPTANCE_ALLOW_MOTION=1" >&2
  exit 2
fi

RUN_DIR="${OUT_ROOT}/eddy-${RUN_ID}"
if [ -e "${RUN_DIR}" ]; then
  echo "TREED_EDDY_DIAG_ERROR: run package already exists: ${RUN_DIR}" >&2
  exit 2
fi

umask 077
mkdir -p "${OUT_ROOT}"
mkdir "${RUN_DIR}" || exit 2
mkdir -p "${RUN_DIR}"/{can,config,kernel,klipper,moonraker,system,versions}

SCAN_RESULT="not_started"
STARTED_AT=""
ENDED_AT=""
LOG_START_LINE=1
CANDUMP_PID=""
LOG_START_ID=""
STATE_QUERY='configfile&webhooks&toolhead&print_stats&pause_resume&treed_z_recovery&mcu&mcu%20EBBCan&mcu%20eddy'

capture() {
  local relative_path="$1"
  shift
  local output="${RUN_DIR}/${relative_path}"
  local rc

  mkdir -p "$(dirname "${output}")"
  # JSON остаётся машинно читаемым; метаданные и stderr хранятся рядом.
  if [[ "${relative_path}" == *.json ]]; then
    "$@" >"${output}" 2>"${output}.stderr"
    rc=$?
    printf 'captured_at=%s\nexit_code=%s\n' "$(date --iso-8601=seconds)" "${rc}" >"${output}.meta"
    return "${rc}"
  fi
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

copy_log_bounded() {
  local source="$1"
  local target="$2"
  python3 - "${source}" "${target}" <<'PY'
import pathlib
import sys

source = pathlib.Path(sys.argv[1])
target = pathlib.Path(sys.argv[2])
limit = 8 * 1024 * 1024
if not source.is_file():
    target.write_text(f"source unavailable: {source}\n", encoding="utf-8")
    raise SystemExit(0)
with source.open("rb") as handle:
    handle.seek(0, 2)
    size = handle.tell()
    handle.seek(max(0, size - limit))
    data = handle.read()
text = data.decode("utf-8", errors="replace").replace("\x00", "\\0")
target.write_text(text, encoding="utf-8")
PY
}

write_session_boundaries() {
  local source="$1"
  local target="$2"
  python3 - "${source}" "${target}" <<'PY'
import pathlib
import re
import sys

source = pathlib.Path(sys.argv[1])
target = pathlib.Path(sys.argv[2])
patterns = re.compile(r"Start printer at|Git version:|Moonraker Version:|System Time Received|server:klippy_(?:ready|shutdown|disconnect)", re.I)
if not source.is_file():
    target.write_text("source unavailable\n", encoding="utf-8")
    raise SystemExit(0)
lines = source.read_text(encoding="utf-8", errors="replace").replace("\x00", "\\0").splitlines()
found = [f"{number}:{line}" for number, line in enumerate(lines, 1) if patterns.search(line)]
target.write_text("\n".join(found) + ("\n" if found else "no session boundary markers found\n"), encoding="utf-8")
PY
}

record_failure() {
  printf '%s\n' "$1" >>"${RUN_DIR}/status.txt"
}

last_mcu_stats() {
  local target="$1"
  if [ -f "${KLIPPY_LOG}" ]; then
    python3 - "${KLIPPY_LOG}" "${target}" <<'PY'
import pathlib
import sys

source = pathlib.Path(sys.argv[1])
target = pathlib.Path(sys.argv[2])
session_target = target.with_suffix(".session.txt")
with source.open("rb") as handle:
    handle.seek(0, 2)
    size = handle.tell()
    handle.seek(max(0, size - 4 * 1024 * 1024))
    text = handle.read().decode("utf-8", errors="replace").replace("\x00", "\\0")
lines = [line for line in text.splitlines() if line.startswith("Stats ")]
sessions = [line for line in text.splitlines() if line.startswith("Start printer at")]
target.write_text((lines[-1] if lines else "Stats line unavailable") + "\n", encoding="utf-8")
session_target.write_text((sessions[-1] if sessions else "session boundary unavailable") + "\n", encoding="utf-8")
PY
  else
    printf 'klippy.log missing: %s\n' "${KLIPPY_LOG}" >"${target}"
    printf 'session boundary unavailable\n' >"${target%.txt}.session.txt"
  fi
}

write_mcu_delta() {
  python3 - \
    "${RUN_DIR}/klipper/mcu-stats.before.txt" \
    "${RUN_DIR}/klipper/mcu-stats.after.txt" \
    "${RUN_DIR}/klipper/mcu-stats.before.session.txt" \
    "${RUN_DIR}/klipper/mcu-stats.after.session.txt" \
    <<'PY' >"${RUN_DIR}/klipper/mcu-stats.delta.txt"
import re
import sys

metrics = ("bytes_write", "bytes_read", "bytes_retransmit", "bytes_invalid", "tx_retries")

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
        found = {key: int(value) for key, value in re.findall(r"\b(bytes_(?:write|read|retransmit|invalid)|tx_retries)=(\d+)", values)}
        if found:
            result[name] = found
    return result, ""

before, before_error = parse(sys.argv[1])
after, after_error = parse(sys.argv[2])
if before_error or after_error:
    print("parse_error", before_error or after_error)
    raise SystemExit(1)
before_session = open(sys.argv[3], encoding="utf-8", errors="replace").read().strip()
after_session = open(sys.argv[4], encoding="utf-8", errors="replace").read().strip()
session_known = "unavailable" not in before_session and "unavailable" not in after_session
same_session = session_known and before_session == after_session
print(f"session_boundary={'same' if same_session else 'changed' if session_known else 'unavailable'}")

for name in sorted(set(before) | set(after)):
    print(name + ":")
    for key in metrics:
        old = before.get(name, {}).get(key)
        new = after.get(name, {}).get(key)
        if old is None or new is None:
            print(f"  {key}=unavailable before={old} after={new}")
        elif not same_session:
            print(f"  {key}=unavailable before={old} after={new} reason=session_boundary")
        elif new < old:
            print(f"  {key}=unavailable before={old} after={new} reason=counter_reset")
        else:
            print(f"  {key}: before={old} after={new} delta={new - old}")
PY
}

write_can_delta() {
  python3 - "${RUN_DIR}/can/ip-details.before.txt" "${RUN_DIR}/can/ip-details.after.txt" <<'PY' >"${RUN_DIR}/can/ip-details.delta.txt"
import re
import sys

def parse(path):
    text = open(path, encoding="utf-8", errors="replace").read().replace("\x00", "\\0")
    result = {}
    for direction in ("RX", "TX"):
        match = re.search(rf"{direction}:\s+bytes\s+packets\s+errors[^\n]*\n\s*(\d+)\s+(\d+)\s+(\d+)", text)
        if match:
            result[f"{direction.lower()}_errors_total"] = int(match.group(3))
    berr = re.search(r"berr-counter\s+tx\s+(\d+)\s+rx\s+(\d+)", text)
    if berr:
        result["berr_tx_current"] = int(berr.group(1))
        result["berr_rx_current"] = int(berr.group(2))
    return result

before = parse(sys.argv[1])
after = parse(sys.argv[2])
for key in ("rx_errors_total", "tx_errors_total"):
    old = before.get(key)
    new = after.get(key)
    if old is None or new is None:
        print(f"{key}=unavailable before={old} after={new}")
    elif new < old:
        print(f"{key}=unavailable before={old} after={new} reason=counter_reset")
    else:
        print(f"{key}: before={old} after={new} delta={new - old}")
for key in ("berr_rx_current", "berr_tx_current"):
    print(f"{key}: before={before.get(key, 'unavailable')} after={after.get(key, 'unavailable')} (instantaneous, no delta interpretation)")
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

  if [ "${MODE}" != "passive" ] && [ -f "${KLIPPY_LOG}" ]; then
    sed -n "${LOG_START_LINE},\$p" "${KLIPPY_LOG}" | tail -n 20000 >"${RUN_DIR}/klipper/klippy.interval.log"
    if [ "${LOG_START_ID}" != "$(stat -c '%d:%i' "${KLIPPY_LOG}")" ] || [ "$(wc -l < "${KLIPPY_LOG}")" -lt "$((LOG_START_LINE - 1))" ]; then
      record_failure "klippy_interval_rotated_or_truncated"
    fi
    if [ "$(( $(wc -l < "${KLIPPY_LOG}") - LOG_START_LINE + 1 ))" -gt 20000 ]; then
      record_failure "klippy_interval_exceeds_capture_limit"
    fi
  elif [ "${MODE}" = "passive" ]; then
    printf 'not applicable in passive mode\n' >"${RUN_DIR}/klipper/klippy.interval.log"
  fi
  capture "can/ip-details.after.txt" ip -details -statistics link show "${CAN_IFACE}"
  capture "can/counters.after.json" ip -json -details -statistics link show "${CAN_IFACE}"
  write_can_delta || record_failure "can_stats_delta_unavailable"
  capture "kernel/messages.interval.txt" journalctl -k --since "${STARTED_AT:-now}" --until "${ENDED_AT}" --no-pager
  capture "moonraker/gcode-store.after.json" curl -fsS --max-time 15 "http://127.0.0.1:7125/server/gcode_store?count=200"
  capture "moonraker/acceptance-after.json" curl -fsS --max-time 15 "http://127.0.0.1:7125/printer/objects/query?${STATE_QUERY}"
  capture "moonraker/printer-info-after.json" curl -fsS --max-time 15 "http://127.0.0.1:7125/printer/info"
  capture "moonraker/bed-mesh.after.json" curl -fsS --max-time 15 "http://127.0.0.1:7125/printer/objects/query?bed_mesh"

  {
    printf 'run_id=%s\n' "${RUN_ID}"
    printf 'reference_commit=%s\n' "${COMMIT}"
    printf 'started_at=%s\n' "${STARTED_AT:-not_started}"
    printf 'ended_at=%s\n' "${ENDED_AT}"
    printf 'mode=%s\n' "${MODE}"
    printf 'scan_result=%s\n' "${SCAN_RESULT}"
    printf 'exit_code=%s\n' "${rc}"
    if [ "${MODE}" = "eddy-scan" ]; then
      printf 'scan_command=TREED_EDDY_ACCEPTANCE_MESH CONFIRM=1\n'
    elif [ "${MODE}" = "acceptance" ]; then
      printf 'command_sequence=result.json:commands\n'
    else
      printf 'scan_command=not_sent\n'
    fi
    printf 'save_config_sent=0\nrestart_sent=0\nfirmware_restart_sent=0\ncan_reconfigured=0\n'
  } >"${RUN_DIR}/manifest.env"

  if [ "${MODE}" = "acceptance" ] || [ "${MODE}" = "eddy-scan" ]; then
    python3 "${REPO_DIR}/tools/z_acceptance.py" finalize --package "${RUN_DIR}" || rc=24
  fi
  sed -i "s/^exit_code=.*/exit_code=${rc}/" "${RUN_DIR}/manifest.env"

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
capture "versions/boot.txt" bash -c 'printf "boot_id="; cat /proc/sys/kernel/random/boot_id; printf "uptime="; cat /proc/uptime'
capture "versions/klipper.txt" git -C "${PI_HOME}/klipper" log -1 --format='commit=%H%nsubject=%s'
capture "versions/printer-core.txt" git -C "${REPO_DIR}" log -1 --format='commit=%H%nsubject=%s'
capture "versions/printer-core.diff" git -C "${REPO_DIR}" diff -- klipper-host klipper tools/collect_eddy_diagnostic.sh tools/z_acceptance.py
capture "versions/extra-checksums.txt" sha256sum "${REPO_DIR}/klipper-host/treed_z_recovery.py" "${PI_HOME}/klipper/klippy/extras/treed_z_recovery.py"
capture "versions/driver-gs_usb.txt" modinfo gs_usb
capture "versions/usb-can.txt" lsusb -d 1d50:606f
capture_sh "versions/mcu-from-klippy.txt" "tail -c 8388608 '${KLIPPY_LOG}' 2>/dev/null | grep -E '^Loaded MCU' || true"
capture "versions/mcu-live.json" curl -fsS --max-time 15 "http://127.0.0.1:7125/printer/objects/query?mcu&mcu%20EBBCan&mcu%20eddy&webhooks"
capture_sh "versions/runtime-manifest.txt" "sed -n '1,160p' '${REPO_DIR}/runtime-versions.env'"
capture_sh "versions/firmware-manifest.txt" "sed -n '1,160p' '${PI_HOME}/treed/firmware-artifacts/treed-v2/latest/manifest.tsv'"
capture "can/ip-details.before.txt" ip -details -statistics link show "${CAN_IFACE}"
capture "can/counters.before.json" ip -json -details -statistics link show "${CAN_IFACE}"
capture "can/ethtool-driver.txt" ethtool -i "${CAN_IFACE}"
capture "system/can-unit.txt" systemctl cat treed-can-setup.service
capture "system/can-unit-properties.txt" systemctl show treed-can-setup.service -p Before -p After -p Wants -p Requires -p Conflicts -p ActiveState
capture "system/can-unit-dependencies.txt" systemctl list-dependencies --all treed-can-setup.service
capture "system/klipper-unit-properties.txt" systemctl show klipper.service -p Before -p After -p Wants -p Requires -p Conflicts -p ActiveState
capture "system/klipper-unit-dependencies.txt" systemctl list-dependencies --all klipper.service
capture "system/unit-states.txt" systemctl show klipper.service moonraker.service treed-can-setup.service -p Id -p ActiveState -p SubState -p Result -p ExecMainStatus
capture "kernel/current-boot.txt" journalctl -k -b -n 4000 --no-pager
capture_sh "config/runtime-printer-include.txt" "grep -nF 'probe_eddy_duo.cfg' '${PI_HOME}/printer_data/config/printer.cfg' || true"
capture "moonraker/configfile-before.json" curl -fsS --max-time 15 "http://127.0.0.1:7125/printer/objects/query?configfile"
capture "moonraker/printer-info-before.json" curl -fsS --max-time 15 "http://127.0.0.1:7125/printer/info"
capture "moonraker/acceptance-before.json" curl -fsS --max-time 15 "http://127.0.0.1:7125/printer/objects/query?${STATE_QUERY}"
capture "moonraker/gcode-store.before.json" curl -fsS --max-time 15 "http://127.0.0.1:7125/server/gcode_store?count=200"

STARTED_AT="$(date --iso-8601=seconds)"
last_mcu_stats "${RUN_DIR}/klipper/mcu-stats.before.txt"
copy_log_bounded "${KLIPPY_LOG}" "${RUN_DIR}/klipper/klippy.current.log"
copy_log_bounded "${MOONRAKER_LOG}" "${RUN_DIR}/moonraker/moonraker.current.log"
write_session_boundaries "${RUN_DIR}/klipper/klippy.current.log" "${RUN_DIR}/klipper/session-boundaries.txt"
write_session_boundaries "${RUN_DIR}/moonraker/moonraker.current.log" "${RUN_DIR}/moonraker/session-boundaries.txt"

if [ "${MODE}" = "passive" ]; then
  SCAN_RESULT="not_requested"
  if command -v candump >/dev/null 2>&1 && command -v timeout >/dev/null 2>&1; then
    capture "can/candump.log" timeout 10 candump -L "${CAN_IFACE}"
  else
    printf 'candump unavailable; passive capture skipped\n' >"${RUN_DIR}/can/candump.unavailable.txt"
  fi
  exit 0
fi

if [ "${MODE}" = "acceptance" ]; then
  # Блок 3: Явная серия; collector пишет evidence даже после первого отказа.
  LOG_START_LINE=$(( $(wc -l < "${KLIPPY_LOG}") + 1 ))
  LOG_START_ID="$(stat -c '%d:%i' "${KLIPPY_LOG}")"
  if [ "${TREED_ACCEPTANCE_CANDUMP:-0}" = "1" ]; then
    candump -L "${CAN_IFACE}" >"${RUN_DIR}/can/candump.log" 2>"${RUN_DIR}/can/candump.error.log" &
    CANDUMP_PID=$!
    sleep 1
    if ! kill -0 "${CANDUMP_PID}" 2>/dev/null; then
      record_failure "candump_failed"
      exit 6
    fi
  fi
  acceptance_args=(run --mode "${TREED_ACCEPTANCE_MODE:-bottom}" --package "${RUN_DIR}" --runs "${TREED_ACCEPTANCE_RUNS:-10}" --allow-motion)
  if [ "${TREED_ACCEPTANCE_LOSE_Z:-0}" = "1" ]; then acceptance_args+=(--lose-z); fi
  if [ -n "${TREED_ACCEPTANCE_STARTS:-}" ]; then
    read -r -a acceptance_starts <<< "${TREED_ACCEPTANCE_STARTS}"
    acceptance_args+=(--starts "${acceptance_starts[@]}")
  fi
  python3 "${REPO_DIR}/tools/z_acceptance.py" "${acceptance_args[@]}"
  acceptance_rc=$?
  SCAN_RESULT="acceptance_exit_${acceptance_rc}"
  exit "${acceptance_rc}"
fi

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
LOG_START_ID="$(stat -c '%d:%i' "${KLIPPY_LOG}")"
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

GCODE=$'RESPOND PREFIX=eddy_diag MSG="BEGIN run='"${RUN_ID}"$'"\n'
GCODE+="TREED_EDDY_ACCEPTANCE_MESH CONFIRM=1"
GCODE+=$'\nM400\n'
GCODE+=$'RESPOND PREFIX=eddy_diag MSG="END run='"${RUN_ID}"$'"'
printf '%s\n' "${GCODE}" >"${RUN_DIR}/klipper/mesh-command.gcode"
python3 -c 'import json, sys; print(json.dumps({"script": sys.stdin.read()}))' <<<"${GCODE}" \
  | curl -fsS --max-time 1200 -H 'Content-Type: application/json' -X POST --data-binary @- \
      "http://127.0.0.1:7125/printer/gcode/script" >"${RUN_DIR}/moonraker/gcode-submit.json" || {
  SCAN_RESULT="gcode_submit_failed"
  record_failure "gcode_submit_failed"
  exit 20
}

SCAN_RESULT="waiting"
for ((waited=0; waited<1200; waited++)); do
  curl -fsS --max-time 15 "http://127.0.0.1:7125/server/gcode_store?count=200" >"${RUN_DIR}/moonraker/gcode-store.current.json" 2>/dev/null || true
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
