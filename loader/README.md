# Loader

Каталог `loader/` содержит оркестратор provisioning и шаги, которые приводят систему к целевому runtime-состоянию V2.

## Структура

- `loader/loader.sh` — главный оркестратор.
- `loader/lib/*.sh` — общие библиотеки функций.
- `loader/steps/*.sh` — атомарные шаги provisioning.

## Как работает оркестратор

`loader.sh` выполняет:
1. Нормализацию `*.sh` в `loader/**` для `apply`-режима (убирает CRLF, выставляет executable-bit).
2. Определение `PI_USER` / `PI_HOME` и host-aware boot-контекста (`BOOT_DIR`, `TREED_BOOT_BACKEND`, `CMDLINE_FILE`, `CONFIG_FILE`, `ARMBIAN_ENV_FILE`).
3. Fail-fast проверку boot backend-контракта (`rpi`, `armbian` или `extlinux`).
4. Снятие state snapshot в `/run/treed-loader/state.env` для `apply`-режима.
5. Вычисление режима деплоя (`TREED_DEPLOY_MODE_EFFECTIVE`) по фактическому состоянию устройства.
6. Если `TREED_LOADER_MODE=check`, read-only проверку актуальности без запуска step-скриптов.
7. Если `TREED_LOADER_MODE=apply`, последовательный запуск реестра шагов.

Контуры выполнения:
- `required` шаг: ошибка завершает loader.
- `optional` шаг: ошибка логируется, provisioning продолжается.

## Реестр шагов

| # | Шаг | Тип | Назначение |
|---|---|---|---|
| 1 | `check-env` | required | Проверка контракта V2 переменных и окружения. |
| 2 | `timezone-sync` | required | Синхронизация timezone/NTP. |
| 3 | `maintenance-stop` | required | Контролируемая остановка runtime-сервисов. |
| 4 | `packages-core` | required | Установка базовых пакетов и numpy/matplotlib/BLAS-зависимостей. |
| 5 | `runtime-bootstrap` | required | Bootstrap Klipper/Moonraker unit-файлов, venv и runtime-каталогов; Crowsnest best-effort при `TREED_CAMERA_REQUIRED=0`. |
| 6 | `can-setup` | required | Подъем `can0` через systemd oneshot + `ip link`. |
| 7 | `firmware-build` | required | Сборка firmware main+EBB(+Eddy) и публикация build-отчета. |
| 8 | `boot-hdmi-config` | required | RPi: `config.txt`; Armbian: `armbianEnv.txt`; Extlinux: `append video=...`. |
| 9 | `plymouth-theme-install` | required | Установка темы Plymouth. |
| 10 | `plymouth-initramfs` | required | Пересборка initramfs. |
| 11 | `plymouth-initramfs-config` | required | RPi/Armbian/Extlinux backend-aware валидация initrd-контура. |
| 12 | `plymouth-cmdline` | required | RPi: `cmdline.txt`; Armbian: `extraargs`; Extlinux: `append` в `extlinux.conf`. |
| 13 | `plymouth-systemd` | required | Политика `getty@tty1` и unit Plymouth. |
| 14 | `klipper-sync` | required | Синхронизация `klipper/` в staging. |
| 15 | `klipper-core` | required | Раскладка staging в runtime-конфиг. |
| 16 | `klipper-anti-shutdown` | required | Сброс MCU shutdown при необходимости. |
| 17 | `mainsail-web` | required | Установка/обновление web-слоя Mainsail и nginx reverse proxy. |
| 18 | `moonraker-config` | required | Деплой Moonraker-конфигов и компонента. |
| 19 | `crowsnest-webcam` | optional | Настройка камеры/crowsnest/Moonraker webcam-фрагмента. |
| 20 | `treed-cam` | required | Runtime-скрипты камеры TreeD. |
| 21 | `klipper-mainsail-theme` | required | Синхронизация темы Mainsail. |
| 22 | `klipperscreen-install` | required | Managed-установка/проверка KlipperScreen. |
| 23 | `klipperscreen-theme` | required | Деплой темы/шрифта KlipperScreen. |
| 24 | `klipperscreen-integr` | required | Systemd override KlipperScreen. |
| 25 | `treed-shell-install` | required | Деплой TreeD Shell из `treed-shell-ui.zip` и команды переключения TS/KS. |
| 26 | `maintenance-start` | required | Запуск required/best-effort сервисов. |
| 27 | `verify` | required | Финальная валидация V2-контура с разделением fatal/diagnostic проверок. |

`loader/steps/detect-boot-env.sh` оставлен для ручной диагностики. Основной оркестратор не запускает его повторно, потому что boot-контекст уже определяется в parent-shell до реестра шагов.

## Ключевые переменные

- `TREED_MAIN_MCU_CANBUS_UUID` — Octopus Pro UUID, default `d372e54bf965`.
- `TREED_LOADER_MODE` — `apply|check`, default `apply`; `check` только сверяет текущее runtime-состояние и не запускает step-скрипты.
- `TREED_DEPLOY_MODE` — `auto|clean|preserve`, default `auto`; `auto` использует state snapshot, а не имя ветки.
- `TREED_DEVICE_STATE` — `fresh|update|recover`; пишется loader в `/run/treed-loader/state.env`.
- `TREED_STATE_FILE` — default `/run/treed-loader/state.env`.
- `TREED_CAN_IFACE` — default `can0`.
- `TREED_CAN_BITRATE` — default `1000000`.
- `TREED_CAN_TXQUEUE` — default `1024`.
- `TREED_CAN_RESTART_MS` — default `100` (применяется в `ip link ... restart-ms` при каждом старте `treed-can-setup.service`).
- `TREED_CAN_IFACE_WAIT_SEC` — default `20` (ожидание появления `can0` после boot/USB init).
- `TREED_CAN_REINIT_ATTEMPTS` — default `5` (количество циклов down/up для восстановления CAN после reboot).
- `TREED_CAN_REINIT_DELAY_SEC` — default `2` (пауза между reinit-циклами).
- `TREED_KLIPPER_PREFLIGHT` — `0|1`, default `1`; включает readiness-проверку перед стартом Klipper.
- `TREED_KLIPPER_PREFLIGHT_WAIT_SEC` — default `12`; общий таймаут ожидания CAN-интерфейса перед стартом Klipper.
- `TREED_KLIPPER_PREFLIGHT_INTERVAL_SEC` — default `1`; интервал повторной проверки preflight.
- `TREED_KLIPPER_PREFLIGHT_CAN_UUIDS_REQUIRED` — `0|1`, default `0`; при `1` strict UUID-gate через `canbus_query.py`, при `0` query не запускается.
- `TREED_EBB_CANBUS_UUID` — EBB42 UUID, default `efaf957ab20f`; auto-detect не используется, чтобы не принять Eddy за EBB.
- `TREED_EDDY_ENABLED` — legacy `0|1`, default `1`; для активного Klipper-профиля должно оставаться `1`.
- `TREED_EDDY_CANBUS_UUID` — Eddy UUID, default `95485b93332a`.
- `TREED_NONINTERACTIVE` — `0|1`, default `1`; убирает apt/dpkg/needrestart prompts.
- `TREED_FIRMWARE_BUILD_ENABLED` — `0|1`, default `1`.
- `TREED_KLIPPER_SRC_DIR` — default `${PI_HOME}/klipper`.
- `TREED_KLIPPER_REPO` — default `https://github.com/Klipper3d/klipper.git`.
- `TREED_KLIPPER_REF` — optional pin branch/tag/commit для Klipper.
- `TREED_FIRMWARE_ARTIFACTS_DIR` — default `${PI_HOME}/treed/firmware-artifacts/treed-v2`.
- `TREED_FW_MAIN_CONFIG` / `TREED_FW_EBB_CONFIG` / `TREED_FW_EDDY_CONFIG` — пути к Kconfig target-файлам сборки.
- `TREED_RUNTIME_BOOTSTRAP` — `0|1`, default `1` (создание/проверка unit-файлов и venv Klipper/Moonraker).
- `TREED_KLIPPY_ENV_DIR` — default `${PI_HOME}/klippy-env`; venv Klipper, куда `runtime-bootstrap` при первом запуске ставит `numpy` и `matplotlib`.
- `TREED_CROWSNEST_SRC_DIR` — default `${PI_HOME}/crowsnest`, upstream checkout Crowsnest.
- `TREED_CROWSNEST_REPO` — default `https://github.com/mainsail-crew/crowsnest.git`.
- `TREED_CROWSNEST_REF` — optional pin branch/tag/commit для Crowsnest.
- `TREED_CROWSNEST_INSTALL` — `0|1`, default `1`; установка/обновление Crowsnest в `runtime-bootstrap`.
- `TREED_CROWSNEST_RECREATE` — `0|1`, default `0`; пересоздание `${PI_HOME}/crowsnest`.
- `TREED_CROWSNEST_UPDATE` — `0|1`, default `1`; `git pull` и повторный unattended install Crowsnest.
- `TREED_CAMERA_REQUIRED` — `0|1`, default `0`; при `1` Crowsnest/webcam становятся fail-fast.
- `TREED_MAINSAIL_WEB_PATH` — default `/var/www/mainsail`, путь web-root Mainsail (используется в `mainsail-web` и `moonraker-config`).
- `TREED_MAINSAIL_ZIP_URL` — URL архива Mainsail для `mainsail-web`.
- `TREED_MAINSAIL_MOONRAKER_PROXY_URL` — upstream Moonraker для nginx reverse-proxy в `mainsail-web` (default `http://127.0.0.1:7125`).
- `TREED_MAINSAIL_LOCAL_ZIP` — local fallback archive, default `${REPO_DIR}/mainsail/web/mainsail.zip`.
- `TREED_MAINSAIL_PREFER_LOCAL_ZIP` — `0|1`, default `1`; использовать bundled archive вместо live-download.
- `TREED_MAINSAIL_WGET_TIMEOUT` / `TREED_MAINSAIL_WGET_CONNECT_TIMEOUT` / `TREED_MAINSAIL_WGET_READ_TIMEOUT` — таймауты загрузки `mainsail.zip`.
- `TREED_MAINSAIL_WGET_TRIES` — число попыток загрузки `mainsail.zip` (default `3`).
- `TREED_MAINSAIL_ALLOW_EXISTING_FALLBACK` — `0|1`, default `1`; при недоступном GitHub разрешает использовать существующий валидный web-root Mainsail.
- `TREED_KLIPPERSCREEN_REPO` — default `https://github.com/KlipperScreen/KlipperScreen.git`.
- `TREED_KLIPPERSCREEN_PRIMARY_BRANCH` — default `master`, ветка KlipperScreen для Moonraker update_manager.
- `TREED_KLIPPERSCREEN_REF` — pin branch/tag/commit для managed checkout KlipperScreen; если установленный checkout той же версии или новее, переустановка пропускается.
- `TREED_FORCE_KLIPPERSCREEN_INSTALL` — `1` принудительно пересоздает managed checkout KlipperScreen.
- `TREED_KLIPPERSCREEN_ENV` — путь venv KlipperScreen, default `${PI_HOME}/.KlipperScreen-env`.
- `TREED_KLIPPERSCREEN_REQUIRED` — `0|1`, default `1`; оставлен для совместимости, активный UI проверяется через `TREED_UI_MODE`.
- `TREED_UI_MODE` — `ts|ks`, default `ts`; выбранный экранный UI. Если `/etc/default/treed-ui` уже существует, bootstrap берет режим оттуда.
- `TREED_UI_ENV_FILE` — default `/etc/default/treed-ui`, persisted-состояние выбранного UI.
- `TREED_SHELL_RELEASE_API_URL` — default `https://api.github.com/repos/TreeD-Hub/treed-shell/releases`, GitHub Releases API.
- `TREED_SHELL_RELEASE_TAG_PREFIX` — default `ui-main-`, префикс production UI release tag.
- `TREED_SHELL_UI_ASSET_NAME` — default `treed-shell-ui.zip`, имя release asset.
- `TREED_SHELL_UI_ARCHIVE_URL` — optional direct archive URL; при наличии loader не обращается к GitHub Releases API.
- `TREED_SHELL_RUNTIME_DIR` — default `${PI_HOME}/treed/treed-shell-runtime`, runtime root.
- `TREED_SHELL_WEB_DIR` — default `${TREED_SHELL_RUNTIME_DIR}/ui`, распакованный UI bundle.
- `TREED_SHELL_HTTP_PORT` — default `8787`, порт локального static server.
- `TREED_SHELL_BROWSER_BIN` — optional browser binary override.
- `TREED_SHELL_CHROMIUM_RENDERING` — `hardware|software`, default `hardware`; `software` добавляет Chromium flags `--disable-gpu`, `--disable-gpu-compositing`, `--enable-unsafe-swiftshader`. Runtime override можно положить в `/etc/default/treed-shell`.
- `TREED_SHELL_START_TIMEOUT` — default `45`, ожидание активного `treed-shell.service`.
- `TREED_KLIPPER_START_REQUIRE_ACTIVE` — `0|1`, default `1`; при `1` `maintenance-start` блокирует loader, если `klipper.service` не стал active в таймаут.
- `TREED_REQUIRE_KLIPPER_READY` — `0|1`, default `0`; управляет тем, будет ли `Klippy state!=ready` блокировать `verify`.

## Запуск

`runtime-bootstrap` поддерживает полную Git metadata для Klipper/Moonraker/Crowsnest: новые checkout'ы не создаются shallow-клонами, а существующие shallow-репозитории разворачиваются через `git fetch --unshallow --tags`. Это нужно, чтобы Moonraker update_manager видел реальные semver-версии, а не `v0.0.0-...-inferred`.

`runtime-bootstrap` проверяет `numpy` и `matplotlib` в `${TREED_KLIPPY_ENV_DIR:-${PI_HOME}/klippy-env}`: если импорт уже работает, установка пропускается; если пакета нет, ставится текущий стабильный релиз через pip в существующий venv Klipper.

```bash
curl -fsSL https://raw.githubusercontent.com/TreeD-Hub/treed-mainshellOS/treed-v2/bootstrap-pi.sh | bash
```

Локальный запуск уже клонированного репозитория:

```bash
cd ~/treed/treed-mainshellOS
sudo bash loader/loader.sh
```

Read-only проверка актуальности без применения изменений:

```bash
sudo TREED_LOADER_MODE=check bash loader/loader.sh
```

## Смежная документация

- `loader/lib/README.md` — описание библиотек `loader/lib`.
- `loader/steps/README.md` — описание шагов и env-параметров.
- `docs/config-ownership.md` — ownership runtime-артефактов.
