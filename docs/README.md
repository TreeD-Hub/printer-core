> Root project map: `README.md`
> Canonical config ownership model: `docs/config-ownership.md`

## Быстрый install path (V2)

```bash
set -euo pipefail
REPO_URL="https://github.com/TreeD-Hub/treed-mainshellOS.git"
INSTALL_REF="${INSTALL_REF:-treed-v2}"
BASE="/home/pi/treed"
REPO_DIR="${BASE}/treed-mainshellOS"

TREED_MAIN_MCU_SERIAL_BY_ID="${TREED_MAIN_MCU_SERIAL_BY_ID:-}"
TREED_MAIN_MCU_SERIAL_MASK="${TREED_MAIN_MCU_SERIAL_MASK:-/dev/serial/by-id/*stm32*}"
TREED_CAN_IFACE="${TREED_CAN_IFACE:-can0}"
TREED_CAN_BITRATE="${TREED_CAN_BITRATE:-500000}"
TREED_CAN_TXQUEUE="${TREED_CAN_TXQUEUE:-1024}"
TREED_CAN_AUTOBITRATE="${TREED_CAN_AUTOBITRATE:-1}"
TREED_CAN_AUTOBITRATE_LIST="${TREED_CAN_AUTOBITRATE_LIST:-500000 1000000 250000 125000}"
TREED_EBB_CANBUS_UUID="${TREED_EBB_CANBUS_UUID:-}"
TREED_EDDY_ENABLED="${TREED_EDDY_ENABLED:-0}"
TREED_EDDY_CANBUS_UUID="${TREED_EDDY_CANBUS_UUID:-}"

sudo systemctl stop klipper moonraker KlipperScreen crowsnest 2>/dev/null || true

mkdir -p "${BASE}"
sudo rm -rf "${REPO_DIR}"
git clone --branch "${INSTALL_REF}" --depth 1 "${REPO_URL}" "${REPO_DIR}"

cd "${REPO_DIR}"
find loader -type f -name '*.sh' -print0 | xargs -0 sed -i 's/\r$//'
chmod +x loader/loader.sh
find loader/steps -type f -name '*.sh' -exec chmod +x {} +

sudo TREED_MAIN_MCU_SERIAL_BY_ID="${TREED_MAIN_MCU_SERIAL_BY_ID}" \
     TREED_MAIN_MCU_SERIAL_MASK="${TREED_MAIN_MCU_SERIAL_MASK}" \
     TREED_CAN_IFACE="${TREED_CAN_IFACE}" \
     TREED_CAN_BITRATE="${TREED_CAN_BITRATE}" \
     TREED_CAN_TXQUEUE="${TREED_CAN_TXQUEUE}" \
     TREED_CAN_AUTOBITRATE="${TREED_CAN_AUTOBITRATE}" \
     TREED_CAN_AUTOBITRATE_LIST="${TREED_CAN_AUTOBITRATE_LIST}" \
     TREED_EBB_CANBUS_UUID="${TREED_EBB_CANBUS_UUID}" \
     TREED_EDDY_ENABLED="${TREED_EDDY_ENABLED}" \
     TREED_EDDY_CANBUS_UUID="${TREED_EDDY_CANBUS_UUID}" \
     bash loader/loader.sh
```

## Контракт переменных

- `TREED_MAIN_MCU_SERIAL_BY_ID` — optional override `/dev/serial/by-id/*`.
- `TREED_MAIN_MCU_SERIAL_MASK` — маска для auto-resolve main MCU, default `/dev/serial/by-id/*stm32*`.
- `TREED_CAN_IFACE` — default `can0`.
- `TREED_CAN_BITRATE` — default `500000`.
- `TREED_CAN_TXQUEUE` — default `1024`.
- `TREED_CAN_AUTOBITRATE` — `0|1`, default `1`; при пустом UUID позволяет подобрать рабочий bitrate из `TREED_CAN_AUTOBITRATE_LIST`.
- `TREED_CAN_AUTOBITRATE_LIST` — default `500000 1000000 250000 125000`.
- `TREED_EBB_CANBUS_UUID` — рекомендуется задавать явно; если пусто, `klipper-profiles.sh` пробует auto-detect через `canbus_query` (успех только при единственном UUID на шине).
- `TREED_EDDY_ENABLED` — `0|1`, default `0`.
- `TREED_EDDY_CANBUS_UUID` — required только при `TREED_EDDY_ENABLED=1`.

## Контракт железа

- Host SBC: Rock Pi / Rock Pi 4 Plus.
- Main MCU: Octopus Pro по USB serial.
- CAN adapter: U2C V2.1 (USB -> CAN).
- Toolhead MCU: EBB42 по CAN (required).
- Probe: Eddy / Eddy Duo по CAN (optional).

Ветка `treed-v2` не поддерживает RN12/RPi/UART-миграции.
