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
TREED_CAN_BITRATE="${TREED_CAN_BITRATE:-1000000}"
TREED_CAN_TXQUEUE="${TREED_CAN_TXQUEUE:-1024}"
TREED_CAN_AUTOBITRATE="${TREED_CAN_AUTOBITRATE:-1}"
TREED_CAN_AUTOBITRATE_LIST="${TREED_CAN_AUTOBITRATE_LIST:-1000000 500000 250000 125000}"
TREED_EBB_CANBUS_UUID="${TREED_EBB_CANBUS_UUID:-}"
TREED_EBB_CANBUS_AUTODETECT="${TREED_EBB_CANBUS_AUTODETECT:-0}"
TREED_EDDY_ENABLED="${TREED_EDDY_ENABLED:-0}"
TREED_EDDY_CANBUS_UUID="${TREED_EDDY_CANBUS_UUID:-}"
TREED_Z_ENDSTOP_PIN="${TREED_Z_ENDSTOP_PIN:-PG10}"
TREED_Z_POSITION_ENDSTOP="${TREED_Z_POSITION_ENDSTOP:-0.5}"
TREED_NONINTERACTIVE="${TREED_NONINTERACTIVE:-1}"
TREED_KLIPPERSCREEN_INSTALL_SERVICE="${TREED_KLIPPERSCREEN_INSTALL_SERVICE:-1}"
TREED_KLIPPERSCREEN_BACKEND="${TREED_KLIPPERSCREEN_BACKEND:-X}"
TREED_KLIPPERSCREEN_NETWORK_MANAGER="${TREED_KLIPPERSCREEN_NETWORK_MANAGER:-N}"
TREED_KLIPPERSCREEN_START_AFTER_INSTALL="${TREED_KLIPPERSCREEN_START_AFTER_INSTALL:-0}"

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
     TREED_EBB_CANBUS_AUTODETECT="${TREED_EBB_CANBUS_AUTODETECT}" \
     TREED_EDDY_ENABLED="${TREED_EDDY_ENABLED}" \
     TREED_EDDY_CANBUS_UUID="${TREED_EDDY_CANBUS_UUID}" \
     TREED_Z_ENDSTOP_PIN="${TREED_Z_ENDSTOP_PIN}" \
     TREED_Z_POSITION_ENDSTOP="${TREED_Z_POSITION_ENDSTOP}" \
     TREED_NONINTERACTIVE="${TREED_NONINTERACTIVE}" \
     TREED_KLIPPERSCREEN_INSTALL_SERVICE="${TREED_KLIPPERSCREEN_INSTALL_SERVICE}" \
     TREED_KLIPPERSCREEN_BACKEND="${TREED_KLIPPERSCREEN_BACKEND}" \
     TREED_KLIPPERSCREEN_NETWORK_MANAGER="${TREED_KLIPPERSCREEN_NETWORK_MANAGER}" \
     TREED_KLIPPERSCREEN_START_AFTER_INSTALL="${TREED_KLIPPERSCREEN_START_AFTER_INSTALL}" \
     bash loader/loader.sh
```

## Контракт переменных

- `TREED_MAIN_MCU_SERIAL_BY_ID` — optional override `/dev/serial/by-id/*`.
- `TREED_MAIN_MCU_SERIAL_MASK` — маска для auto-resolve main MCU, default `/dev/serial/by-id/*stm32*`.
- `TREED_CAN_IFACE` — default `can0`.
- `TREED_CAN_BITRATE` — default `1000000`.
- `TREED_CAN_TXQUEUE` — default `1024`.
- `TREED_CAN_AUTOBITRATE` — `0|1`, default `1`; при auto-detect CAN UUID позволяет подобрать рабочий bitrate из `TREED_CAN_AUTOBITRATE_LIST`.
- `TREED_CAN_AUTOBITRATE_LIST` — default `1000000 500000 250000 125000`.
- `TREED_EBB_CANBUS_UUID` — optional hex UUID; если пусто, `klipper-profiles.sh` использует runtime hint, либо требует `TREED_EBB_CANBUS_AUTODETECT=1` для первичного provisioning.
- `TREED_EBB_CANBUS_AUTODETECT` — `0|1`, default `0`; при `1` разрешает выбрать единственный видимый CAN UUID как EBB, использовать только когда на шине оставлена одна EBB.
- `TREED_EDDY_ENABLED` — `0|1`, default `0`.
- `TREED_EDDY_CANBUS_UUID` — optional hex UUID при `TREED_EDDY_ENABLED=1`; если пусто, после резолва EBB используется единственный оставшийся неизвестный CAN UUID.
- `TREED_Z_ENDSTOP_PIN` — physical Z endstop when Eddy is disabled, default `PG10`.
- `TREED_Z_POSITION_ENDSTOP` — Z endstop coordinate when Eddy is disabled, default `0.5`.
- `TREED_NONINTERACTIVE` — `0|1`, default `1`; disables apt/dpkg/needrestart prompts.
- `TREED_KLIPPERSCREEN_INSTALL_SERVICE` — answer for KlipperScreen service install, default `1`.
- `TREED_KLIPPERSCREEN_BACKEND` — answer for KlipperScreen graphical backend, default `X`.
- `TREED_KLIPPERSCREEN_NETWORK_MANAGER` — answer for NetworkManager install, default `N`.
- `TREED_KLIPPERSCREEN_START_AFTER_INSTALL` — answer for external installer service start, default `0`.

## Контракт железа

- Host SBC: Rock Pi / Rock Pi 4 Plus.
- Main MCU: Octopus family по USB serial.
- CAN adapter: U2C V2.1 (USB -> CAN).
- Toolhead MCU: EBB42 по CAN (required).
- Probe: Eddy / Eddy Duo по CAN (optional).

Ветка `treed-v2` не поддерживает RN12/RPi/UART-миграции.
