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
7. `plymouth-theme-install`
8. `plymouth-initramfs`
9. `plymouth-initramfs-config`
10. `plymouth-cmdline`
11. `plymouth-systemd`
12. `klipper-sync`
13. `klipper-profiles`
14. `klipper-core`
15. `klipper-anti-shutdown`
16. `moonraker-config`
17. `crowsnest-webcam`
18. `treed-cam`
19. `klipper-mainsail-theme`
20. `klipperscreen-install`
21. `klipperscreen-integr`
22. `maintenance-start`
23. `verify`

## Контракт окружения

`loader.sh` экспортирует переменные, которыми пользуются шаги:

- `REPO_DIR` - путь к репозиторию.
- `PI_USER`, `PI_HOME` - целевой пользователь и его home.
- `BOOT_DIR`, `CMDLINE_FILE`, `CONFIG_FILE` - обнаруженные boot-пути.

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
