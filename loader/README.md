# Loader

Каталог `loader/` содержит оркестратор провижининга TreeD и шаги, которые раскладывают конфиги в runtime.

## Точка входа

- `loader/loader.sh`

Базовый запуск:

```bash
cd /home/pi/treed/treed-mainshellOS
sudo bash loader/loader.sh
```

## Порядок шагов

`loader.sh` выполняет шаги строго по списку:

1. `check-env`
2. `detect-rpi`
3. `timezone-sync`
4. `maintenance-stop`
5. `packages-core`
6. `boot-hdmi-config`
7. `rpi-uart-config`
8. `plymouth-theme-install`
9. `plymouth-initramfs`
10. `plymouth-initramfs-config`
11. `plymouth-cmdline`
12. `plymouth-systemd`
13. `klipper-sync`
14. `klipper-profiles`
15. `klipper-core`
16. `klipper-anti-shutdown`
17. `moonraker-config`
18. `crowsnest-webcam`
19. `treed-cam`
20. `klipper-mainsail-theme`
21. `klipperscreen-install`
22. `klipperscreen-integr`
23. `maintenance-start`
24. `verify`

## Контракт окружения

`loader.sh` экспортирует переменные, которыми пользуются шаги:

- `REPO_DIR` - путь к репозиторию.
- `PI_USER`, `PI_HOME` - целевой пользователь и его home.
- `BOOT_DIR`, `CMDLINE_FILE`, `CONFIG_FILE` - обнаруженные boot-пути.
- `TREED_MCU_TRANSPORT` - режим связи с MCU (`usb` или `uart`).
- `TREED_MCU_UART_DEV` - путь UART-устройства (по умолчанию `/dev/serial0`).
- `TREED_UART_DISABLE_BT` - отключение BT UART (`1` -> `dtoverlay=disable-bt`, default для `uart`; `0` - оставить BT включенным).
  Для standalone `verify` без явного значения используется auto-режим проверки BT overlay.

Дополнительно:

- скрипт нормализует CRLF в `loader/*.sh`;
- включает `set -euo pipefail`;
- ставит `trap` для fail-fast логирования шага и команды.

## Границы ответственности

- `loader.sh` только оркестрирует порядок и общие переменные.
- Бизнес-логика каждого этапа находится в `loader/steps/*.sh`.
- Общие функции вынесены в `loader/lib/*.sh`.

## Где смотреть детали

- `loader/lib/README.md` - общие библиотеки.
- `loader/steps/README.md` - описание шагов и управляющих переменных.
- `docs/config-ownership.md` - карта слоев и ownership runtime-артефактов.
- `docs/rn_v12_to_klipper.md` - двухфазный переход RN12 `usb -> uart` (подготовка + reboot, затем переключение transport).
