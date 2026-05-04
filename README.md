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

REPO_URL="${REPO_URL:-https://github.com/TreeD-Hub/treed-mainshellOS.git}"
INSTALL_REF="${INSTALL_REF:-treed-v2_main}"
BASE="${BASE:-/home/pi/treed}"
REPO_DIR="${REPO_DIR:-${BASE}/treed-mainshellOS}"

sudo mkdir -p "${BASE}"
sudo chown "$(id -u):$(id -g)" "${BASE}"

sudo rm -rf "${REPO_DIR}"
git clone --branch "${INSTALL_REF}" --depth 1 "${REPO_URL}" "${REPO_DIR}"

cd "${REPO_DIR}"
sudo TREED_DEPLOY_MODE=clean TREED_NONINTERACTIVE=1 bash install.sh
sudo reboot
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
