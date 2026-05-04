# Loader Steps

Каталог `loader/steps/` содержит атомарные этапы provisioning. Порядок и тип шага (`required`/`optional`) задаются в `loader/loader.sh`.

## Порядок выполнения

| # | Шаг | Тип | Назначение |
|---|---|---|---|
| 1 | `check-env.sh` | required | Проверка V2-контракта переменных и базового окружения loader. |
| 2 | `detect-boot-env.sh` | required | Host-aware определение backend (`rpi|armbian|extlinux`) и boot-путей. |
| 3 | `timezone-sync.sh` | required | Синхронизация timezone/NTP. |
| 4 | `maintenance-stop.sh` | required | Остановка runtime-сервисов перед provisioning. |
| 5 | `packages-core.sh` | required | Базовые системные пакеты. |
| 6 | `runtime-bootstrap.sh` | required | Bootstrap Klipper/Moonraker unit-файлов, venv и runtime-каталогов. |
| 7 | `can-setup.sh` | required | Подъем CAN интерфейса (`can0`) через systemd oneshot + `ip link`. |
| 8 | `firmware-build.sh` | required | Сборка firmware main+EBB(+Eddy), публикация artifact/report/checksum. |
| 9 | `boot-hdmi-config.sh` | required | Backend-aware HDMI policy: `auto` (удаляет forced `video=`), `fixed` (добавляет `video=`), `off` (не трогает `video=`). |
| 10 | `plymouth-theme-install.sh` | required | Установка темы Plymouth. |
| 11 | `plymouth-initramfs.sh` | required | Пересборка initramfs. |
| 12 | `plymouth-initramfs-config.sh` | required | RPi/Armbian/Extlinux backend-aware валидация initrd. |
| 13 | `plymouth-cmdline.sh` | required | RPi: `cmdline.txt`; Armbian: `extraargs`; Extlinux: `append` в `extlinux.conf`. |
| 14 | `plymouth-systemd.sh` | required | Политика `getty@tty1` и `plymouth-quit*`. |
| 15 | `klipper-sync.sh` | required | Синхронизация дерева `klipper/` в staging. |
| 16 | `klipper-profiles.sh` | required | Профиль V2: main USB serial, EBB CAN UUID, optional Eddy UUID, контур X/Y sensorless (tmc5160 virtual endstop). |
| 17 | `klipper-core.sh` | required | Раскладка staging в runtime (`printer_data/config`). |
| 18 | `klipper-anti-shutdown.sh` | required | Обработка состояния MCU `shutdown`. |
| 19 | `mainsail-web.sh` | required | Установка/обновление web-слоя Mainsail и nginx reverse proxy. |
| 20 | `moonraker-config.sh` | required | Деплой Moonraker-конфига и компонента. |
| 21 | `crowsnest-webcam.sh` | optional | Настройка камеры/crowsnest/webcam-фрагмента. |
| 22 | `treed-cam.sh` | required | Runtime-скрипты камеры TreeD. |
| 23 | `klipper-mainsail-theme.sh` | required | Деплой темы Mainsail. |
| 24 | `klipperscreen-install.sh` | optional | Установка/проверка KlipperScreen. |
| 25 | `klipperscreen-theme.sh` | optional | Деплой темы/шрифта KlipperScreen. |
| 26 | `klipperscreen-integr.sh` | optional | Systemd override KlipperScreen. |
| 27 | `maintenance-start.sh` | required | Запуск required/best-effort сервисов. |
| 28 | `verify.sh` | required | Финальная валидация V2-контура с паритетной отчетностью, включая проверки sensorless X/Y и Input Shaper. |

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
- `TREED_DEPLOY_MODE_EFFECTIVE` (`clean|preserve`)
- `TREED_MAINTENANCE_MODE` (`1|0`)
- `TREED_NONINTERACTIVE` (`0|1`, default `1`; apt/dpkg/needrestart без prompt)

### Main MCU / CAN / Eddy

- `TREED_MAIN_MCU_SERIAL_BY_ID` (optional, `/dev/serial/by-id/*`)
- `TREED_MAIN_MCU_SERIAL_MASK` (default `/dev/serial/by-id/*stm32*`)
- `TREED_CAN_IFACE` (default `can0`)
- `TREED_CAN_BITRATE` (default `1000000`)
- `TREED_CAN_TXQUEUE` (default `1024`)
- `TREED_CAN_RESTART_MS` (default `100`; значение `restart-ms` для auto-recovery CAN controller)
- `TREED_CAN_IFACE_WAIT_SEC` (default `20`; ожидание появления `can0` после boot/USB init)
- `TREED_CAN_REINIT_ATTEMPTS` (default `5`; число циклов down/up при инициализации CAN)
- `TREED_CAN_REINIT_DELAY_SEC` (default `2`; пауза между циклами reinit CAN)
- `TREED_CAN_AUTOBITRATE` (`0|1`, default `1`; при auto-detect CAN UUID допускает перебор типовых bitrate)
- `TREED_CAN_AUTOBITRATE_LIST` (default `1000000 500000 250000 125000`)
- `TREED_CAN_SETUP_ENV_FILE` (default `/etc/default/treed-can-setup`)
- `TREED_CAN_SETUP_UNIT` (default `treed-can-setup.service`)
- `TREED_EBB_CANBUS_UUID` (optional hex UUID; если пусто, `klipper-profiles.sh` сначала переиспользует runtime hint из `generated/treed_machine_mcus.cfg`; первичный auto-detect EBB доступен только при `TREED_EBB_CANBUS_AUTODETECT=1`)
- `TREED_EBB_CANBUS_AUTODETECT` (`0|1`, default `0`; при `1` разрешает первичный выбор единственного видимого CAN UUID как EBB, использовать только в provisioning-режиме с одной подключенной EBB)
- `TREED_CANBUS_QUERY_PYTHON` (optional override интерпретатора для `canbus_query.py`; по умолчанию используется `${TREED_KLIPPY_ENV_DIR}/bin/python*`)
- `TREED_EDDY_ENABLED` (`0|1`, default `0`)
- `TREED_EDDY_CANBUS_UUID` (optional hex UUID when `TREED_EDDY_ENABLED=1`; если пусто, после резолва EBB используется единственный оставшийся неизвестный CAN UUID)
- `TREED_Z_ENDSTOP_PIN` (default `PG10`, used when `TREED_EDDY_ENABLED=0`)
- `TREED_Z_POSITION_ENDSTOP` (default `0.5`, used when `TREED_EDDY_ENABLED=0`)

### Firmware build

- `TREED_FIRMWARE_BUILD_ENABLED` (`0|1`, default `1`)
- `TREED_KLIPPER_SRC_DIR` (default `${PI_HOME}/klipper`)
- `TREED_FIRMWARE_ARTIFACTS_DIR` (default `${PI_HOME}/treed/firmware-artifacts/treed-v2`)
- `TREED_FW_MAIN_CONFIG` (default `firmware/configs/treed_v2/main_octopus_pro_f446_usb.config`)
- `TREED_FW_EBB_CONFIG` (default `firmware/configs/treed_v2/ebb42_can_stm32g0b1.config`)
- `TREED_FW_EDDY_CONFIG` (default `firmware/configs/treed_v2/eddy_can_rp2040.config`)

### Runtime bootstrap

- `TREED_RUNTIME_BOOTSTRAP` (`0|1`, default `1`)
- `TREED_ALLOW_MISSING_REQUIRED_SERVICES_ON_STOP` (`0|1`, default `1`)
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

### KlipperScreen

- `TREED_FORCE_KLIPPERSCREEN_INSTALL` (`1` — принудительная установка)
- `TREED_KLIPPERSCREEN_INSTALL_SERVICE` (default `1`, ответ installer-у на установку service)
- `TREED_KLIPPERSCREEN_BACKEND` (default `X`, ответ installer-у на выбор Xserver/Wayland)
- `TREED_KLIPPERSCREEN_NETWORK_MANAGER` (default `N`, ответ installer-у на установку NetworkManager)
- `TREED_KLIPPERSCREEN_START_AFTER_INSTALL` (default `0`, внешний installer не стартует сервис сам)
- `TREED_KLIPPERSCREEN_REPO`
- `TREED_KLIPPERSCREEN_REF`
- `TREED_KLIPPERSCREEN_START_TIMEOUT` (default `45`)
- `TREED_KLIPPERSCREEN_HOME`
- `TREED_KS_THEME` (`treed-oled|...|keep`, default `treed-oled`)
- `TREED_KS_LANGUAGE` (`ru|...|keep`, default `ru`)
- `TREED_KLIPPERSCREEN_REQUIRED` (`1` делает проверки UI обязательными в `verify`)

### Time / systemd / verify

- `TREED_SET_TIMEZONE` (default `1`)
- `TREED_TIMEZONE` (default `Europe/Moscow`)
- `TREED_ENABLE_NTP` (default `1`)
- `TREED_MASK_TTY1` (default `1`)
- `TREED_VERIFY_CAMERA` (`auto|0|1`, default `auto`; в `auto` HTTP-проверки камеры запускаются только при наличии webcam-fragment и `crowsnest.service`)
- `TREED_CAM_HTTP_RETRIES` (default `3`)
- `TREED_CAM_HTTP_TIMEOUT` (default `8`)
- `TREED_MOONRAKER_HTTP_RETRIES` (default `30`)
- `TREED_REQUIRE_KLIPPER_READY` (`0|1`, default `0`; при `1` `verify.sh` считает `Klippy state!=ready` блокирующей ошибкой)
- `TREED_ARMBIAN_VERBOSITY` (default `1`)
- `TREED_ARMBIAN_BOOTLOGO` (default `true`)
- `TREED_ARMBIAN_CONSOLE` (default `both`)
- `TREED_HDMI_MODE` (`auto|fixed|off`, default `auto`)
- `TREED_HDMI_VIDEO_MODE` (optional full mode override for `fixed`, e.g. `HDMI-A-1:960x544@60`)
- `TREED_ARMBIAN_VIDEO_MODE` (default `HDMI-A-1:960x544@60`)

## Поведение `TREED_DEPLOY_MODE_EFFECTIVE`

`TREED_DEPLOY_MODE_EFFECTIVE` влияет на шаги:

- `klipper-core.sh`
  - `clean`: полная пересборка runtime без возврата локальных файлов.
  - `preserve`: сохраняются `local_overrides.cfg` и stock `SAVE_CONFIG`-сегмент из `printer.cfg`.
- `moonraker-config.sh`
  - `clean`: `moonraker.conf` без `.bak`.
  - `preserve`: `backup_file_once` перед перезаписью.
- `klipperscreen-theme.sh`
  - `clean`: `KlipperScreen.conf` без `.bak`.
  - `preserve`: `backup_file_once` перед изменением.

## Практические замечания

- `can-setup.sh` required: пишет `/etc/default/treed-can-setup`, `/usr/local/sbin/treed-can-setup.sh` и systemd unit `treed-can-setup.service`; на каждом boot применяет `bitrate`, `txqueuelen`, `restart-ms`, ждет появление интерфейса и выполняет reinit-циклы при старте.
- `firmware-build.sh` required: компилирует `main_octopus`, `ebb42_can` и `eddy_can` (если enabled) в отдельный run-dir с `manifest.tsv`, `checksums.sha256`, `build-report.txt`.
- `firmware-build.sh` fail-fast при отсутствии `make`/toolchain, невалидном target-конфиге или ошибке сборки любого required MCU.
- `runtime-bootstrap.sh` формирует `klipper.service` с API-сокетом `-a ${PI_HOME}/printer_data/comms/klippy.sock` (ожидается Moonraker секцией `klippy_uds_address`).
- `klipper-profiles.sh` fail-fast при ambiguous main MCU auto-resolve (`0` или `>1` кандидатов по маске).
- `klipper-profiles.sh` генерирует `generated/treed_machine_mcus.cfg` с `[mcu]`, `[mcu EBBCan]` и optional `[mcu eddy]`; реальные serial/UUID не хранятся в профильных cfg.
- `klipper-profiles.sh` fail-fast, если `TREED_EBB_CANBUS_UUID` пуст, runtime hint отсутствует и `TREED_EBB_CANBUS_AUTODETECT!=1`; UUID сам по себе не кодирует роль платы, поэтому первичный auto-detect EBB должен включаться явно.
- `klipper-profiles.sh` при `TREED_EDDY_ENABLED=1` и пустом `TREED_EDDY_CANBUS_UUID` выбирает Eddy только если после EBB остается ровно один неизвестный UUID.
- Явно заданные `TREED_EBB_CANBUS_UUID`/`TREED_EDDY_CANBUS_UUID` проверяются на формат и наличие на `${TREED_CAN_IFACE}`.
- При нахождении UUID на bitrate, отличном от `TREED_CAN_BITRATE`, `klipper-profiles.sh` обновляет `${TREED_CAN_SETUP_ENV_FILE}` и перезапускает `${TREED_CAN_SETUP_UNIT}`.
- `klipper-profiles.sh` включает Eddy include только при `TREED_EDDY_ENABLED=1`.
- Для `treed_v2_corexy_v1` X/Y homing работает в sensorless-контуре (`tmc5160_stepper_x/y:virtual_endstop`): перед deploy требуются корректная SPI/DIAG обвязка на TMC5160 и отключение X/Y механических endstop из логики.
- `runtime-bootstrap.sh` автоматически устанавливает PolicyKit правила Moonraker (по умолчанию включено), чтобы не было предупреждений `org.freedesktop.systemd1.manage-units`/`org.freedesktop.packagekit.*`.
- `mainsail-web.sh` required: ставит `nginx`, загружает `mainsail.zip` в `${TREED_MAINSAIL_WEB_PATH}` и публикует reverse-proxy конфиг сайта.
- `moonraker-config.sh` включает updater Mainsail только при наличии валидного локального пути (с `release_info.json`); при типовом порядке шагов путь уже существует после `mainsail-web.sh`.
- `verify.sh` проверяет V2-контур с паритетом `dev`: boot/initramfs/cmdline, timezone/NTP, web-слой (`nginx` + Mainsail web-root + proxy к Moonraker), camera/webcam/crowsnest, KlipperScreen, `klipper`/`moonraker`, `treed-can-setup`, `can0` (`bitrate`/`txqueuelen`/`restart-ms`), main USB serial, EBB CAN UUID, Input Shaper и optional Eddy; HTTP-ready Moonraker и готовность Klippy разделены через `TREED_REQUIRE_KLIPPER_READY`.
