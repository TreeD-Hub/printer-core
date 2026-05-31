# Loader Steps

Каталог `loader/steps/` содержит атомарные этапы provisioning. Порядок и тип шага (`required`/`optional`) задаются в `loader/loader.sh`.

## Порядок выполнения

| # | Шаг | Тип | Назначение |
|---|---|---|---|
| 1 | `check-env.sh` | required | Проверка V2-контракта переменных и базового окружения loader. |
| 2 | `timezone-sync.sh` | required | Синхронизация timezone/NTP. |
| 3 | `maintenance-stop.sh` | required | Остановка runtime-сервисов перед provisioning. |
| 4 | `packages-core.sh` | required | Базовые системные пакеты и numpy/matplotlib/BLAS-зависимости. |
| 5 | `runtime-bootstrap.sh` | required | Bootstrap Klipper/Moonraker unit-файлов, venv и runtime-каталогов. |
| 6 | `can-setup.sh` | required | Подъем CAN интерфейса (`can0`) через systemd oneshot + `ip link`. |
| 7 | `firmware-build.sh` | required | Сборка firmware main+EBB(+Eddy), публикация artifact/report/checksum. |
| 8 | `boot-hdmi-config.sh` | required | Backend-aware HDMI policy: `auto` (удаляет forced `video=`), `fixed` (добавляет `video=`), `off` (не трогает `video=`). |
| 9 | `plymouth-theme-install.sh` | required | Установка темы Plymouth. |
| 10 | `plymouth-initramfs.sh` | required | Пересборка initramfs. |
| 11 | `plymouth-initramfs-config.sh` | required | RPi/Armbian/Extlinux backend-aware валидация initrd. |
| 12 | `plymouth-cmdline.sh` | required | RPi: `cmdline.txt`; Armbian: `extraargs`; Extlinux: `append` в `extlinux.conf`. |
| 13 | `plymouth-systemd.sh` | required | Политика `getty@tty1` и `plymouth-quit*`. |
| 14 | `klipper-sync.sh` | required | Синхронизация дерева `klipper/` в staging. |
| 15 | `klipper-core.sh` | required | Раскладка staging в runtime (`printer_data/config`). |
| 16 | `klipper-anti-shutdown.sh` | required | Обработка состояния MCU `shutdown`. |
| 17 | `mainsail-web.sh` | required | Установка/обновление web-слоя Mainsail и nginx reverse proxy. |
| 18 | `moonraker-config.sh` | required | Деплой Moonraker-конфига и компонента. |
| 19 | `crowsnest-webcam.sh` | optional | Настройка камеры/crowsnest/webcam-фрагмента. |
| 20 | `treed-cam.sh` | required | Runtime-скрипты камеры TreeD. |
| 21 | `klipper-mainsail-theme.sh` | required | Деплой темы Mainsail. |
| 22 | `klipperscreen-install.sh` | required | Managed-установка/проверка KlipperScreen. |
| 23 | `klipperscreen-theme.sh` | required | Деплой темы/шрифта KlipperScreen. |
| 24 | `klipperscreen-integr.sh` | required | Systemd override KlipperScreen. |
| 25 | `treed-shell-install.sh` | required | Деплой TreeD Shell (`on-print`) и команды переключения TS/KS. |
| 26 | `maintenance-start.sh` | required | Запуск required/best-effort сервисов. |
| 27 | `verify.sh` | required | Финальная валидация V2-контура (boot/service/http/CAN/camera), без проверки runtime-конфигов. |

`detect-boot-env.sh` оставлен как ручной диагностический step. В основном реестре он не запускается, потому что parent-shell оркестратор уже определяет и экспортирует boot-контекст до выполнения шагов.

## Контракт для step-скриптов

Каждый шаг должен:
- быть идемпотентным;
- логировать ключевые действия через `log_info`/`log_warn`/`log_error`;
- завершаться с ошибкой только при реально блокирующей ситуации для своего контура.

Контур шага определяется оркестратором:
- `required` — ошибка прерывает provisioning;
- `optional` — ошибка логируется, loader продолжает работу.

## Ключевые переменные окружения

### Общие

- `REPO_DIR`, `PI_USER`, `PI_HOME`
- `BOOT_DIR`, `TREED_BOOT_BACKEND`
- `CMDLINE_FILE`, `CONFIG_FILE` (для `rpi` backend)
- `ARMBIAN_ENV_FILE` (для `armbian` backend)
- `EXTLINUX_FILE` (для `extlinux` backend)
- `TREED_LOADER_MODE` (`apply|check`, default `apply`; `check` не запускает step-скрипты и только сверяет runtime-состояние)
- `TREED_DEPLOY_MODE` (`auto|clean|preserve`, default `auto`)
- `TREED_DEPLOY_MODE_EFFECTIVE` (`clean|preserve`)
- `TREED_DEVICE_STATE` (`fresh|update|recover`)
- `TREED_STATE_FILE` (default `/run/treed-loader/state.env`)
- `TREED_MAINTENANCE_MODE` (`1|0`)
- `TREED_NONINTERACTIVE` (`0|1`, default `1`; apt/dpkg/needrestart без prompt)

### Main MCU / CAN / Eddy

- `TREED_MAIN_MCU_CANBUS_UUID` (default `d372e54bf965`)
- `TREED_CAN_IFACE` (default `can0`)
- `TREED_CAN_BITRATE` (default `1000000`)
- `TREED_CAN_TXQUEUE` (default `1024`)
- `TREED_CAN_RESTART_MS` (default `100`; значение `restart-ms` для auto-recovery CAN controller)
- `TREED_CAN_IFACE_WAIT_SEC` (default `20`; ожидание появления `can0` после boot/USB init)
- `TREED_CAN_REINIT_ATTEMPTS` (default `5`; число циклов down/up при инициализации CAN)
- `TREED_CAN_REINIT_DELAY_SEC` (default `2`; пауза между циклами reinit CAN)
- `TREED_CAN_SETUP_ENV_FILE` (default `/etc/default/treed-can-setup`)
- `TREED_CAN_SETUP_UNIT` (default `treed-can-setup.service`)
- `TREED_KLIPPER_PREFLIGHT` (`0|1`, default `1`; readiness-проверка перед стартом Klipper)
- `TREED_KLIPPER_PREFLIGHT_WAIT_SEC` (default `12`; общий таймаут ожидания CAN-интерфейса)
- `TREED_KLIPPER_PREFLIGHT_INTERVAL_SEC` (default `1`; интервал повторной проверки)
- `TREED_KLIPPER_PREFLIGHT_CAN_UUIDS_REQUIRED` (`0|1`, default `0`; при `1` strict UUID-gate через `canbus_query.py`, при `0` query не запускается)
- `TREED_EBB_CANBUS_UUID` (default `efaf957ab20f`; auto-detect не используется, чтобы не принять Eddy за EBB)
- `TREED_EDDY_ENABLED` (legacy `0|1`, default `1`; для активного Klipper-профиля должно оставаться `1`)
- `TREED_EDDY_CANBUS_UUID` (default `95485b93332a`)

### Firmware build

- `TREED_FIRMWARE_BUILD_ENABLED` (`0|1`, default `1`)
- `TREED_KLIPPER_SRC_DIR` (default `${PI_HOME}/klipper`)
- `TREED_FIRMWARE_ARTIFACTS_DIR` (default `${PI_HOME}/treed/firmware-artifacts/treed-v2`)
- `TREED_FW_MAIN_CONFIG` (default `firmware/configs/treed_v2/main_octopus_pro_f446_can.config`)
- `TREED_FW_EBB_CONFIG` (default `firmware/configs/treed_v2/ebb42_can_stm32g0b1.config`)
- `TREED_FW_EDDY_CONFIG` (default `firmware/configs/treed_v2/eddy_can_rp2040.config`)

### Runtime bootstrap

- `TREED_RUNTIME_BOOTSTRAP` (`0|1`, default `1`)
- `TREED_ALLOW_MISSING_REQUIRED_SERVICES_ON_STOP` (`0|1`, default `1`)
- `TREED_KLIPPER_REPO` (default `https://github.com/Klipper3d/klipper.git`)
- `TREED_KLIPPER_REF` (optional, empty by default)
- `TREED_KLIPPY_ENV_DIR` (default `${PI_HOME}/klippy-env`)
- `TREED_MOONRAKER_SRC_DIR` (default `${PI_HOME}/moonraker`)
- `TREED_MOONRAKER_ENV_DIR` (default `${PI_HOME}/moonraker-env`)
- `TREED_MOONRAKER_REPO` (default `https://github.com/Arksine/moonraker.git`)
- `TREED_MOONRAKER_REF` (optional, empty by default)
- `TREED_MOONRAKER_POLKIT_SETUP` (`0|1`, default `1`; авто-установка PolicyKit правил Moonraker через `set-policykit-rules.sh`)
- `TREED_MOONRAKER_POLKIT_REQUIRED` (`0|1`, default `0`; при `1` делает неуспех PolicyKit setup блокирующей ошибкой)
- `TREED_MOONRAKER_RECREATE` (`0|1`, default `0`; при `1` принудительно пересоздает `${PI_HOME}/moonraker` и `${PI_HOME}/moonraker-env` в `runtime-bootstrap`)
- `TREED_CROWSNEST_SRC_DIR` (default `${PI_HOME}/crowsnest`)
- `TREED_CROWSNEST_REPO` (default `https://github.com/mainsail-crew/crowsnest.git`)
- `TREED_CROWSNEST_REF` (optional, empty by default)
- `TREED_CROWSNEST_INSTALL` (`0|1`, default `1`; при `1` `runtime-bootstrap` устанавливает/обновляет Crowsnest и `crowsnest.service`)
- `TREED_CROWSNEST_RECREATE` (`0|1`, default `0`; при `1` принудительно пересоздает `${PI_HOME}/crowsnest`)
- `TREED_CROWSNEST_UPDATE` (`0|1`, default `1`; при `1` подтягивает Crowsnest repo и повторно запускает unattended installer)

### Moonraker / Camera

- `CAM_DEVICE`
- `CAM_ALLOW_VIDEO0_FALLBACK` (`1` разрешает fallback на `/dev/video0`)
- `TREED_CAMERA_REQUIRED` (`1` переводит шаг камеры в fail-fast; при `0` отсутствие `crowsnest.service` очищает webcam-fragment и не блокирует loader)
- `TREED_CAM_RESOLUTION` (default `1920x1080`)
- `TREED_CAM_FPS` (default `10`)
- `MOONRAKER_READY_RETRIES` (default `30`)
- `TREED_MAINSAIL_WEB_PATH` (default `/var/www/mainsail`; целевой web-root Mainsail и путь для `[update_manager mainsail]`)
- `TREED_MAINSAIL_ZIP_URL` (default `https://github.com/mainsail-crew/mainsail/releases/latest/download/mainsail.zip`)
- `TREED_MAINSAIL_MOONRAKER_PROXY_URL` (default `http://127.0.0.1:7125`; upstream Moonraker для nginx proxy)
- `TREED_MAINSAIL_NGINX_SITE_AVAILABLE` (default `/etc/nginx/sites-available/mainsail`)
- `TREED_MAINSAIL_NGINX_SITE_ENABLED` (default `/etc/nginx/sites-enabled/mainsail`)
- `TREED_MAINSAIL_NGINX_DEFAULT_SITE_ENABLED` (default `/etc/nginx/sites-enabled/default`; удаляется при активации сайта Mainsail)
- `TREED_MAINSAIL_LOCAL_ZIP` (default `${REPO_DIR}/mainsail/web/mainsail.zip`; bundled archive для offline install)
- `TREED_MAINSAIL_PREFER_LOCAL_ZIP` (`0|1`, default `1`; предпочитать bundled archive)
- `TREED_MAINSAIL_WGET_TIMEOUT` (default `30`; общий timeout `wget` для `mainsail.zip`)
- `TREED_MAINSAIL_WGET_DNS_TIMEOUT` (default `10`)
- `TREED_MAINSAIL_WGET_CONNECT_TIMEOUT` (default `10`)
- `TREED_MAINSAIL_WGET_READ_TIMEOUT` (default `30`)
- `TREED_MAINSAIL_WGET_TRIES` (default `3`; число попыток загрузки)
- `TREED_MAINSAIL_ALLOW_EXISTING_FALLBACK` (`0|1`, default `1`; использовать существующий валидный web-root при ошибке загрузки)

### KlipperScreen

- `TREED_FORCE_KLIPPERSCREEN_INSTALL` (`1` — принудительная переустановка managed checkout)
- `TREED_KLIPPERSCREEN_INSTALL_SERVICE` (default `1`, ответ installer-у на установку service)
- `TREED_KLIPPERSCREEN_BACKEND` (default `X`, ответ installer-у на выбор Xserver/Wayland)
- `TREED_KLIPPERSCREEN_NETWORK_MANAGER` (default `N`, ответ installer-у на установку NetworkManager)
- `TREED_KLIPPERSCREEN_START_AFTER_INSTALL` (default `0`, внешний installer не стартует сервис сам)
- `TREED_KLIPPERSCREEN_REPO` (default `https://github.com/KlipperScreen/KlipperScreen.git`)
- `TREED_KLIPPERSCREEN_PRIMARY_BRANCH` (default `master`, ветка для Moonraker update_manager)
- `TREED_KLIPPERSCREEN_REF` (pin branch/tag/commit; checkout той же версии или новее не переустанавливается)
- `TREED_KLIPPERSCREEN_START_TIMEOUT` (default `45`)
- `TREED_KLIPPERSCREEN_HOME`
- `TREED_KLIPPERSCREEN_ENV` (default `${PI_HOME}/.KlipperScreen-env`)
- `TREED_KS_THEME` (`treed-oled|...|keep`, default `treed-oled`)
- `TREED_KS_LANGUAGE` (`ru|...|keep`, default `ru`)
- `TREED_KLIPPERSCREEN_REQUIRED` (`0|1`, default `1`; оставлен для совместимости, активный UI проверяется через `TREED_UI_MODE`)

### TreeD Shell / UI switch

- `TREED_UI_MODE` (`ts|ks`, default `ts`; при наличии `/etc/default/treed-ui` bootstrap берет режим оттуда)
- `TREED_UI_ENV_FILE` (default `/etc/default/treed-ui`)
- `TREED_SHELL_INSTALL` (`0|1`, default `1`)
- `TREED_SHELL_REPO` (default `https://github.com/Yawllen/treed-shell.git`)
- `TREED_SHELL_PRIMARY_BRANCH` (default `on-print`)
- `TREED_SHELL_REF` (default `on-print`)
- `TREED_SHELL_HOME` (default `${PI_HOME}/treed/treed-shell`)
- `TREED_SHELL_RUNTIME_DIR` (default `${PI_HOME}/treed/treed-shell-runtime`)
- `TREED_SHELL_NODE_VERSION` (default `20.19.0`)
- `TREED_SHELL_START_TIMEOUT` (default `45`)
- `TREED_FORCE_SHELL_BUILD` (`1` — принудительная пересборка TreeD Shell)

`treed-shell-install.sh`:
- держит checkout TreeD Shell на ветке/ref `on-print`;
- собирает `npm run tauri:build:printer`;
- публикует runtime binary в `${TREED_SHELL_RUNTIME_DIR}/treed-shell`;
- создает `treed-shell.service`;
- ставит команду `/usr/local/sbin/treed-ui` и symlink `/usr/local/bin/treed-ui`.

Команды переключения на Rock Pi:

```bash
sudo treed-ui ts
sudo treed-ui ks
treed-ui status
```

### Time / systemd / verify

- `TREED_SET_TIMEZONE` (default `1`)
- `TREED_TIMEZONE` (default `Europe/Moscow`)
- `TREED_ENABLE_NTP` (default `1`)
- `TREED_MASK_TTY1` (default `1`)
- `TREED_VERIFY_CAMERA` (`auto|0|1`, default `auto`; в `auto` HTTP-проверки камеры запускаются только при наличии `crowsnest.service`)
- `TREED_CAM_HTTP_RETRIES` (default `3`)
- `TREED_CAM_HTTP_TIMEOUT` (default `8`)
- `TREED_MOONRAKER_HTTP_RETRIES` (default `30`)
- `TREED_KLIPPER_START_REQUIRE_ACTIVE` (`0|1`, default `1`; при `0` ожидание `klipper.service active` в `maintenance-start` диагностическое)
- `TREED_REQUIRE_KLIPPER_READY` (`0|1`, default `0`; при `1` `verify.sh` считает `Klippy state!=ready` блокирующей ошибкой)
- `TREED_ARMBIAN_VERBOSITY` (default `1`)
- `TREED_ARMBIAN_BOOTLOGO` (default `true`)
- `TREED_ARMBIAN_CONSOLE` (default `both`)
- `TREED_HDMI_MODE` (`auto|fixed|off`, default `auto`)
- `TREED_HDMI_VIDEO_MODE` (optional full mode override for `fixed`, e.g. `HDMI-A-1:960x544@60`)
- `TREED_ARMBIAN_VIDEO_MODE` (default `HDMI-A-1:960x544@60`)

## Поведение `TREED_DEPLOY_MODE_EFFECTIVE`

При `TREED_DEPLOY_MODE=auto` оркестратор сначала пишет snapshot `/run/treed-loader/state.env`:

- `fresh` — нет runtime-конфига и unit-файлов Klipper/Moonraker; effective mode `clean`;
- `update` — runtime полный и сервисы не stuck; effective mode `preserve`;
- `recover` — runtime частичный или сервисы `failed|activating|deactivating`; effective mode `preserve`.

`TREED_DEPLOY_MODE_EFFECTIVE` влияет на шаги:

- `klipper-core.sh`
  - `clean`: полная пересборка runtime без возврата локальных файлов.
  - `preserve`: сохраняются `local_overrides.cfg` и stock `SAVE_CONFIG`-сегмент из `printer.cfg`.
- `moonraker-config.sh`
  - `clean`: `moonraker.conf` без `.bak`.
  - `preserve`: `backup_file_once` перед перезаписью.
- `klipperscreen-install.sh`
  - managed checkout находится в `${TREED_KLIPPERSCREEN_HOME:-${PI_HOME}/KlipperScreen}`;
  - venv находится в `${TREED_KLIPPERSCREEN_ENV:-${PI_HOME}/.KlipperScreen-env}`;
  - checkout нормализуется в branch-based состояние `${TREED_KLIPPERSCREEN_PRIMARY_BRANCH:-master}` с `origin`, чтобы Moonraker update_manager не видел detached repo;
  - если checkout полный и его commit равен target или новее target, package-переустановка не выполняется;
  - если service отсутствует/указывает в другой каталог, installer запускается для восстановления systemd wiring без пересоздания same-or-newer checkout.
- `klipperscreen-theme.sh`
  - `clean`: `KlipperScreen.conf` без `.bak`.
  - `preserve`: `backup_file_once` перед изменением.
- `treed-shell-install.sh`
  - managed checkout находится в `${TREED_SHELL_HOME:-${PI_HOME}/treed/treed-shell}`;
  - runtime binary находится в `${TREED_SHELL_RUNTIME_DIR:-${PI_HOME}/treed/treed-shell-runtime}/treed-shell`;
  - выбранный UI хранится в `${TREED_UI_ENV_FILE:-/etc/default/treed-ui}`;
  - при `TREED_UI_MODE=ts` активируется `treed-shell.service`, а `KlipperScreen.service` отключается;
  - при `TREED_UI_MODE=ks` активируется `KlipperScreen.service`, а `treed-shell.service` отключается.

## Практические замечания

- `can-setup.sh` required: пишет `/etc/default/treed-can-setup`, `/usr/local/sbin/treed-can-setup.sh` и systemd unit `treed-can-setup.service`; на каждом boot применяет `bitrate`, `txqueuelen`, `restart-ms`, ждет появление интерфейса и выполняет reinit-циклы при старте.
- `packages-core.sh` перед `apt update/install` сверяет установленный пакетный набор через `dpkg-query`; если все пакеты уже актуально установлены, apt-фаза пропускается.
- `firmware-build.sh` required: компилирует `main_octopus`, `ebb42_can` и `eddy_can` (если enabled) в отдельный run-dir с `manifest.tsv`, `checksums.sha256`, `build-report.txt`.
- `firmware-build.sh` перед сборкой сверяет `inputs.env` в `latest`: commit Klipper и checksum target-конфигов; при совпадении входов повторная сборка пропускается.
- `firmware-build.sh` fail-fast при отсутствии `make`/toolchain, невалидном target-конфиге или ошибке сборки любого required MCU.
- `runtime-bootstrap.sh` формирует `klipper.service` с API-сокетом `-a ${PI_HOME}/printer_data/comms/klippy.sock` (ожидается Moonraker секцией `klippy_uds_address`).
- `runtime-bootstrap.sh` после подготовки `${TREED_KLIPPY_ENV_DIR:-${PI_HOME}/klippy-env}` проверяет импорт `numpy` и `matplotlib`: если пакет уже есть, логирует skip; если нет, ставит текущий стабильный релиз через pip.
- `runtime-bootstrap.sh` заменяет фиксированный cold-boot sleep на `/usr/local/sbin/treed-klipper-preflight.sh`: в default-режиме (`TREED_KLIPPER_PREFLIGHT_CAN_UUIDS_REQUIRED=0`) проверяется только состояние `can0`, а `canbus_query.py` не запускается; strict UUID-gate включается через `TREED_KLIPPER_PREFLIGHT_CAN_UUIDS_REQUIRED=1`.
- `runtime-bootstrap.sh` устанавливает/обновляет Crowsnest best-effort при `TREED_CAMERA_REQUIRED=0`; при `TREED_CAMERA_REQUIRED=1` ошибки Crowsnest становятся блокирующими.
- `runtime-bootstrap.sh` не создает shallow checkout'ы для Klipper/Moonraker/Crowsnest и разворачивает существующие shallow-репозитории через `git fetch --unshallow --tags`, чтобы Moonraker update_manager видел реальные semver-версии.
- `klipper-core.sh` раскладывает `printer_data/config` напрямую из staging-дерева `klipper/` (источник правды — репозиторий).
- Для `treed_v2_corexy_v1` X/Y homing работает в sensorless-контуре (`tmc5160_stepper_x/y:virtual_endstop`): перед deploy требуются TMC5160/TMC5160T Pro, корректная SPI/DIAG обвязка на X/Y и отключение X/Y механических endstop из логики. Профиль переопределяет `G28` через `macros_homing.cfg`: X/Y идут через `G28_BASE` с отходом на 10 мм от X-max/Y-max и паузой 1 секунду для сброса stall-флага TMC5160, а перед `G28 Z` голова переводится в безопасную точку `X122.5 Y122.5`, если X/Y уже захоумлены или должны быть захоумлены в текущем вызове `G28`; при `G28 Z` без готовых X/Y макрос завершает команду явной ошибкой. При Eddy enabled `stepper_z` использует `probe:z_virtual_endstop`, а `G28 Z` остается на штатном Z-endstop активного профиля; Zmax DIAG на `PG10` остается аппаратным резервом вне основного homing-контура.
- `runtime-bootstrap.sh` автоматически устанавливает PolicyKit правила Moonraker (по умолчанию включено), чтобы не было предупреждений `org.freedesktop.systemd1.manage-units`/`org.freedesktop.packagekit.*`.
- `mainsail-web.sh` required: ставит `nginx`, загружает `mainsail.zip` в `${TREED_MAINSAIL_WEB_PATH}` и публикует reverse-proxy конфиг сайта.
- `moonraker-config.sh` включает updater Mainsail только при наличии валидного локального пути (с `release_info.json`); при типовом порядке шагов путь уже существует после `mainsail-web.sh`.
- `moonraker-config.sh` включает updater Crowsnest только при наличии валидного git checkout с updater-метаданными (legacy `tools/pkglist.sh` или v5 `system-dependencies.json` + `requirements.txt`); updater KlipperScreen генерируется позже шагом `klipperscreen-install.sh`, когда checkout уже существует.
- `verify.sh` разделяет fatal и diagnostic: сервисы/HTTP/boot-путь, а также доступность MCU-объектов Klipper (`mcu`, `mcu EBBCan`, optional `mcu eddy`) остаются блокирующими; `Klippy state`, camera/Crowsnest HTTP и live-параметры CAN-интерфейса по умолчанию диагностические.
- `verify.sh` не проверяет runtime-конфиги Klipper (`printer.cfg`, include-цепочку, sensorless-параметры): этап оставлен только для runtime-сервисов и доступности контуров.
