# TreeD MainshellOS

Единая точка входа по структуре репозитория, слоям разворачивания и ownership.

## RN12: Фаза 1 (перевод Pi на UART)

```bash
cd /home/pi/treed/treed-mainshellOS
sudo REPO_DIR="$(pwd)" TREED_MCU_TRANSPORT=uart TREED_UART_DISABLE_BT=1 bash loader/steps/rpi-uart-config.sh
sudo REPO_DIR="$(pwd)" TREED_MCU_TRANSPORT=uart bash loader/steps/plymouth-cmdline.sh
sudo reboot
```

## Быстрый запуск (копируй в SSH)

```bash
set -euo pipefail
REPO_URL="https://github.com/TreeD-Hub/treed-mainshellOS.git"
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

## Карта слоев

1. Репозиторий (source of truth)
- `loader/` — pipeline провижининга и проверки.
- `klipper/` — канонические конфиги Klipper.
- `moonraker/` — базовый конфиг Moonraker и компоненты.
- `runtime-scripts/` — runtime-скрипты (например, для камеры).
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

## Ownership (кратко)

- `klipper/*` -> `loader/steps/klipper-core.sh`
- `moonraker/base/*` -> `loader/steps/moonraker-config.sh`
- `moonraker/generated/50-webcam-treed.conf` -> `loader/steps/crowsnest-webcam.sh`
- `runtime-scripts/treed-cam/*` -> `loader/steps/treed-cam.sh`
- Локальные overrides (`local_overrides.cfg`, `mainsail.cfg` и др.) сохраняются при deploy шагом `klipper-core`.

Подробная карта владения: `docs/config-ownership.md`.

## Документация

- Быстрый install path: `docs/README.md`
- Первый старт платы: `docs/firstStart.md`
- Прошивка RN12 под Klipper: `docs/rn_v12_to_klipper.md`
- Модель владения конфигами: `docs/config-ownership.md`

Для перехода RN12 с USB на UART используйте только двухфазный сценарий из
`docs/rn_v12_to_klipper.md` (фаза 1: подготовка + reboot, фаза 2: переключение transport).
Минимальный post-install smoke-test для UART/MCU также находится в `docs/rn_v12_to_klipper.md`.

## Политика веток

- `dev` — рабочая ветка для актуальных установок и развития.
- `main` — консервативная/историческая ветка, не основной install-канал.
- `refactor/*` — временные ветки для изолированных изменений.

## Naming-конвенции

- README-файлы: `README.md`.
- Каталоги: lowercase + `kebab-case` для составных имен.
- Runtime-скрипты: только в `runtime-scripts/`.
- Firmware-артефакты: `firmware/<board>/<ARTIFACT>.bin`.
