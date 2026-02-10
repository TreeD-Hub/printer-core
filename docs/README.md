> Root project map: `README.md`
> Canonical config ownership model: `docs/config-ownership.md`

## Быстрый install path (актуальный)

```bash
set -euo pipefail
REPO_URL="https://github.com/TreeD-Hub/treed-mainshellOS.git"
# Каналы:
# - dev: рабочая ветка для установки и обновления
# - main: legacy snapshot
INSTALL_REF="${INSTALL_REF:-dev}"
BASE="/home/pi/treed"
REPO_DIR="${BASE}/treed-mainshellOS"
TREED_MCU_TRANSPORT="${TREED_MCU_TRANSPORT:-uart}"  # uart|usb
TREED_MCU_UART_DEV="${TREED_MCU_UART_DEV:-/dev/serial0}"
TREED_UART_DISABLE_BT="${TREED_UART_DISABLE_BT:-1}" # для UART обычно 1

sudo systemctl stop klipper moonraker KlipperScreen crowsnest 2>/dev/null || true

mkdir -p "${BASE}"
sudo rm -rf "${REPO_DIR}"
git clone --branch "${INSTALL_REF}" --depth 1 "${REPO_URL}" "${REPO_DIR}"

cd "${REPO_DIR}"
find loader -type f -name '*.sh' -print0 | xargs -0 sed -i 's/\r$//'
chmod +x loader/loader.sh
find loader/steps -type f -name '*.sh' -exec chmod +x {} +

sudo TREED_MCU_TRANSPORT="${TREED_MCU_TRANSPORT}" \
     TREED_MCU_UART_DEV="${TREED_MCU_UART_DEV}" \
     TREED_UART_DISABLE_BT="${TREED_UART_DISABLE_BT}" \
     bash loader/loader.sh
sudo reboot
```

## Примечания

- По умолчанию камера не является блокером установки.
- Для fail-fast режима камеры используйте: `TREED_CAMERA_REQUIRED=1`.
- UART (рекомендуется): `sudo TREED_MCU_TRANSPORT=uart TREED_MCU_UART_DEV=/dev/serial0 TREED_UART_DISABLE_BT=1 bash loader/loader.sh`.
- Legacy USB: `sudo TREED_MCU_TRANSPORT=usb TREED_UART_DISABLE_BT=0 bash loader/loader.sh`.
- Полный порядок шагов и ownership: `docs/config-ownership.md`.
