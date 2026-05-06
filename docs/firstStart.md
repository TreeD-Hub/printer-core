# Первый старт Rock Pi для TreeD V2

Документ описывает базовый путь подготовки Rock Pi под V2-контур.

## 1. Базовая ОС

- Рекомендуемая база: **Armbian Debian 12**.
- Пользователь для runtime-путей проекта: `pi`.
- SSH должен быть включен.

## 2. Подготовка репозитория

```bash
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/TreeD-Hub/treed-mainshellOS.git}"
INSTALL_REF="${INSTALL_REF:-treed-v2}"
BASE="${BASE:-/home/pi/treed}"
REPO_DIR="${REPO_DIR:-${BASE}/treed-mainshellOS}"

sudo mkdir -p "${BASE}"
sudo chown "$(id -u):$(id -g)" "${BASE}"

sudo rm -rf "${REPO_DIR}"
git clone --branch "${INSTALL_REF}" --depth 1 "${REPO_URL}" "${REPO_DIR}"
cd "${REPO_DIR}"
```

## 3. Минимальные переменные для V2

Для текущей платы значения зафиксированы в `loader/bootstrap.sh`:
- Octopus Pro: `/dev/serial/by-id/usb-Klipper_stm32f446xx_3B0027000D50535556323420-if00`;
- EBB42: `efaf957ab20f`;
- Eddy: `95485b93332a`;
- Eddy включен по умолчанию (`TREED_EDDY_ENABLED=1`).

Опционально:

```bash
export TREED_CAN_IFACE="can0"
export TREED_CAN_BITRATE="1000000"
export TREED_CAN_TXQUEUE="1024"
export TREED_KLIPPER_PREFLIGHT="1"
export TREED_KLIPPER_PREFLIGHT_WAIT_SEC="12"
```

## 4. Запуск loader

```bash
sudo TREED_DEPLOY_MODE=clean TREED_NONINTERACTIVE=1 bash install.sh
sudo reboot
```

## 5. Что проверить после запуска

- `systemctl is-active klipper moonraker treed-can-setup`
- `ip -details link show can0`
- `ls -l /dev/serial/by-id/`
