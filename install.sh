#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOOTSTRAP="${REPO_DIR}/loader/bootstrap.sh"

if [ "${EUID}" -ne 0 ]; then
  echo "[install] ERROR: run as root: sudo bash install.sh" >&2
  exit 1
fi

if [ ! -f "${BOOTSTRAP}" ]; then
  echo "[install] ERROR: missing ${BOOTSTRAP}" >&2
  exit 1
fi

bash "${BOOTSTRAP}"