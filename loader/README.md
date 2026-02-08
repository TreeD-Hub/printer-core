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
3. `packages-core`
4. `boot-hdmi-config`
5. `plymouth-theme-install`
6. `plymouth-initramfs`
7. `plymouth-initramfs-config`
8. `plymouth-cmdline`
9. `plymouth-systemd`
10. `klipper-sync`
11. `klipper-profiles`
12. `klipper-core`
13. `klipper-anti-shutdown`
14. `moonraker-config`
15. `crowsnest-webcam`
16. `treed-cam`
17. `klipper-mainsail-theme`
18. `klipperscreen-install`
19. `klipperscreen-integr`
20. `verify`

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
