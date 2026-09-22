#!/bin/bash
set -euo pipefail

# ==========================================
# CONTRACT TEST: CAN TX QUEUE REAPPLY
# ==========================================
# Назначение:
# - Запускает реальный generated runtime script с mock ip/systemctl.
# - Проверяет миграцию существующего qlen 1024 -> 128 и повторное применение 128.

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

runtime_script="${tmp}/treed-can-setup.sh"
awk '
  /^cat > "\$\{CAN_SCRIPT\}" <<'\''EOF'\''$/ { capture=1; next }
  capture && /^EOF$/ { exit }
  capture { print }
' "${REPO_DIR}/loader/steps/can-setup.sh" > "${runtime_script}"
chmod +x "${runtime_script}"

state_file="${tmp}/qlen"
calls_file="${tmp}/calls"
mock_env="${tmp}/mocks.sh"
printf '1024\n' > "${state_file}"

cat > "${mock_env}" <<'EOF'
systemctl() {
  if [ "${1:-}" = "show" ]; then
    printf 'inactive\n'
  fi
  return 0
}

ip() {
  printf '%s\n' "$*" >> "${CAN_TEST_CALLS}"
  case "$*" in
    "link show ${TREED_CAN_IFACE}")
      printf '2: %s: <NOARP,UP,LOWER_UP> mtu 16 qdisc pfifo_fast state UP qlen %s\n' \
        "${TREED_CAN_IFACE}" "$(cat "${CAN_TEST_STATE}")"
      ;;
    "link set ${TREED_CAN_IFACE} txqueuelen "*)
      printf '%s\n' "${5}" > "${CAN_TEST_STATE}"
      ;;
    "link set ${TREED_CAN_IFACE} down"|"link set ${TREED_CAN_IFACE} up type can bitrate ${TREED_CAN_BITRATE} restart-ms ${TREED_CAN_RESTART_MS}")
      ;;
    *)
      printf 'unexpected ip call: %s\n' "$*" >&2
      return 1
      ;;
  esac
}
EOF

run_runtime_setup() {
  CAN_TEST_STATE="${state_file}" \
  CAN_TEST_CALLS="${calls_file}" \
  BASH_ENV="${mock_env}" \
  TREED_CAN_IFACE="can0" \
  TREED_CAN_BITRATE="1000000" \
  TREED_CAN_TXQUEUE="128" \
  TREED_CAN_RESTART_MS="100" \
  TREED_CAN_IFACE_WAIT_SEC="1" \
  TREED_CAN_REINIT_ATTEMPTS="1" \
  TREED_CAN_REINIT_DELAY_SEC="0" \
  bash "${runtime_script}"
}

run_runtime_setup
[ "$(cat "${state_file}")" = "128" ]
run_runtime_setup
[ "$(cat "${state_file}")" = "128" ]

[ "$(grep -Fxc 'link set can0 txqueuelen 128' "${calls_file}")" -eq 2 ]
[ "$(grep -Fxc 'link set can0 up type can bitrate 1000000 restart-ms 100' "${calls_file}")" -eq 2 ]
if grep -Fq 'link set can0 txqueuelen 1024' "${calls_file}"; then
  printf 'runtime script reapplied legacy txqueuelen 1024\n' >&2
  exit 1
fi

printf 'PASS: CAN txqueuelen 128 reapplied idempotently\n'
