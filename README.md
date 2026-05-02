# TreeD MainshellOS

Единая точка входа для ветки `treed-v2`.

## V2 runtime-модель

```text
Rock Pi (Armbian Debian 12)
 ├─ USB -> Octopus Pro (main MCU, Klipper serial)
 └─ USB -> U2C V2.1
          ├─ CAN -> EBB42 (required)
          └─ CAN -> Eddy Duo (optional)
```

Ветка `treed-v2` не поддерживает RN12/RPi/UART legacy-контур.

## Быстрый запуск (копируй в SSH)

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
TREED_EBB_CANBUS_UUID="${TREED_EBB_CANBUS_UUID:?set TREED_EBB_CANBUS_UUID}"
TREED_EDDY_ENABLED="${TREED_EDDY_ENABLED:-0}"
TREED_EDDY_CANBUS_UUID="${TREED_EDDY_CANBUS_UUID:-}"
TREED_FIRMWARE_BUILD_ENABLED="${TREED_FIRMWARE_BUILD_ENABLED:-1}"
TREED_KLIPPER_SRC_DIR="${TREED_KLIPPER_SRC_DIR:-/home/pi/klipper}"
TREED_FIRMWARE_ARTIFACTS_DIR="${TREED_FIRMWARE_ARTIFACTS_DIR:-/home/pi/treed/firmware-artifacts/treed-v2}"
TREED_FW_MAIN_CONFIG="${TREED_FW_MAIN_CONFIG:-${REPO_DIR}/firmware/configs/treed_v2/main_octopus_pro_f446_usb.config}"
TREED_FW_EBB_CONFIG="${TREED_FW_EBB_CONFIG:-${REPO_DIR}/firmware/configs/treed_v2/ebb42_can_stm32g0b1.config}"
TREED_FW_EDDY_CONFIG="${TREED_FW_EDDY_CONFIG:-${REPO_DIR}/firmware/configs/treed_v2/eddy_can_stm32g0b1.config}"

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
     TREED_EBB_CANBUS_UUID="${TREED_EBB_CANBUS_UUID}" \
     TREED_EDDY_ENABLED="${TREED_EDDY_ENABLED}" \
     TREED_EDDY_CANBUS_UUID="${TREED_EDDY_CANBUS_UUID}" \
     TREED_FIRMWARE_BUILD_ENABLED="${TREED_FIRMWARE_BUILD_ENABLED}" \
     TREED_KLIPPER_SRC_DIR="${TREED_KLIPPER_SRC_DIR}" \
     TREED_FIRMWARE_ARTIFACTS_DIR="${TREED_FIRMWARE_ARTIFACTS_DIR}" \
     TREED_FW_MAIN_CONFIG="${TREED_FW_MAIN_CONFIG}" \
     TREED_FW_EBB_CONFIG="${TREED_FW_EBB_CONFIG}" \
     TREED_FW_EDDY_CONFIG="${TREED_FW_EDDY_CONFIG}" \
     bash loader/loader.sh
```

## Карта слоев

1. Репозиторий (source of truth)
- `loader/` — pipeline provisioning и проверки.
- `klipper/` — канонические конфиги Klipper.
- `moonraker/` — базовый конфиг Moonraker и компоненты.
- `runtime-scripts/` — runtime-скрипты (например, камера).
- `mainsail/` — тема и UI-ресурсы Mainsail.
- `firmware/` — репозиторные firmware-артефакты.

2. Loader
- entrypoint: `loader/loader.sh`
- шаги: `loader/steps/*.sh`

3. Staging на устройстве
- `/home/pi/treed/klipper`

4. Runtime на устройстве
- `/home/pi/printer_data/config`
- `/home/pi/treed/cam/bin`

5. Сервисы и UI
- `klipper`, `moonraker`, `crowsnest`, `KlipperScreen`, `mainsail`

## Документация

- Быстрый install path: `docs/README.md`
- Модель владения конфигами: `docs/config-ownership.md`
- Профиль Klipper V2: `klipper/profiles/treed_v2_corexy_v1/README.md`
