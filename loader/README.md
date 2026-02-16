# Loader

Каталог `loader/` содержит оркестратор provisioning TreeD и шаги, которые раскладывают конфиги в runtime.

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
22. `klipperscreen-theme`
23. `klipperscreen-integr`
24. `maintenance-start`
25. `verify`

## Контракт окружения

`loader.sh` экспортирует переменные, которыми пользуются шаги:

- `REPO_DIR` — путь к репозиторию.
- `PI_USER`, `PI_HOME` — целевой пользователь и его home.
- `BOOT_DIR`, `CMDLINE_FILE`, `CONFIG_FILE` — обнаруженные boot-пути.
- `TREED_MCU_TRANSPORT` — режим связи с MCU (`usb` или `uart`).
- `TREED_MCU_UART_DEV` — путь UART-устройства (по умолчанию `/dev/serial0`).
- `TREED_UART_DISABLE_BT` — отключение BT UART (`1`/`0`).
- `TREED_KS_THEME` — тема KlipperScreen (`treed-oled`, `material-dark`, `keep`).
- `TREED_KS_LANGUAGE` — язык интерфейса KlipperScreen (по умолчанию `ru`, `keep` — не менять текущий язык в `KlipperScreen.conf`).
- `TREED_KLIPPERSCREEN_HOME` — путь до каталога установки KlipperScreen.
  Если переменная не задана, путь определяется из `WorkingDirectory` сервиса `KlipperScreen.service`, fallback — `${PI_HOME}/KlipperScreen`.
- `TREED_DEPLOY_MODE` — режим runtime-деплоя (`auto|clean|preserve`, по умолчанию `auto`).
- `TREED_DEPLOY_MODE_EFFECTIVE` — вычисленный режим для шагов.
- `TREED_DEPLOY_BRANCH` — определенная ветка репозитория (пусто в detached `HEAD`).

Auto-резолв для `TREED_DEPLOY_MODE=auto`:

- `dev` -> `clean`
- любая определенная не-`dev` ветка -> `preserve`
- неопределенная ветка (`HEAD`) -> `clean`

## Границы ответственности

- `loader.sh` оркестрирует порядок шагов и общие переменные.
- Бизнес-логика каждого этапа находится в `loader/steps/*.sh`.
- Общие функции вынесены в `loader/lib/*.sh`.

## Где смотреть детали

- `loader/lib/README.md` — общие библиотеки.
- `loader/steps/README.md` — описание шагов и управляющих переменных.
- `docs/config-ownership.md` — карта слоев и ownership runtime-артефактов.
- `docs/rn_v12_to_klipper.md` — переход RN12 `usb -> uart`.
