# TreeD MainshellOS

Единая точка входа по структуре репозитория, слоям разворачивания и ownership.

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

## Политика веток

- `dev` — рабочая ветка для актуальных установок и развития.
- `main` — консервативная/историческая ветка, не основной install-канал.
- `refactor/*` — временные ветки для изолированных изменений.

## Naming-конвенции

- README-файлы: `README.md`.
- Каталоги: lowercase + `kebab-case` для составных имен.
- Runtime-скрипты: только в `runtime-scripts/`.
- Firmware-артефакты: `firmware/<board>/<ARTIFACT>.bin`.
