# Loader Steps

Каталог `loader/steps/` содержит атомарные этапы provisioning. Порядок и тип шага (`required`/`optional`) задаются в `loader/loader.sh`.

## Порядок выполнения

| # | Шаг | Тип | Назначение |
|---|---|---|---|
| 1 | `check-env.sh` | required | Проверка базового окружения loader. |
| 2 | `detect-rpi.sh` | required | Детект модели RPi и boot-путей. |
| 3 | `timezone-sync.sh` | required | Синхронизация timezone/NTP. |
| 4 | `maintenance-stop.sh` | required | Остановка runtime-сервисов перед provisioning. |
| 5 | `packages-core.sh` | required | Базовые системные пакеты. |
| 6 | `boot-hdmi-config.sh` | required | HDMI-параметры и `gpu_mem` в `config.txt`. |
| 7 | `rpi-uart-config.sh` | required | Подготовка UART-контура MCU. |
| 8 | `plymouth-theme-install.sh` | required | Установка темы Plymouth. |
| 9 | `plymouth-initramfs.sh` | required | Пересборка initramfs. |
| 10 | `plymouth-initramfs-config.sh` | required | Строка `initramfs ... followkernel` в `config.txt`. |
| 11 | `plymouth-cmdline.sh` | required | Нормализация kernel cmdline. |
| 12 | `plymouth-systemd.sh` | required | Политика `getty@tty1` и `plymouth-quit*`. |
| 13 | `klipper-sync.sh` | required | Синхронизация дерева `klipper/` в staging. |
| 14 | `klipper-profiles.sh` | required | Профиль RN12 и serial-path MCU. |
| 15 | `klipper-core.sh` | required | Раскладка staging в runtime (`printer_data/config`). |
| 16 | `klipper-anti-shutdown.sh` | required | Обработка состояния MCU `shutdown`. |
| 17 | `moonraker-config.sh` | required | Деплой Moonraker-конфига и компонента. |
| 18 | `crowsnest-webcam.sh` | optional | Настройка raw камеры (crowsnest), zoom-sidecar, nginx webcam-proxy и Moonraker webcam-фрагмента. |
| 19 | `treed-cam.sh` | required | Runtime-скрипты камеры TreeD (включая zoom-sidecar helpers) и отложенный рестарт zoom-сервиса. |
| 20 | `klipper-mainsail-theme.sh` | required | Деплой темы Mainsail. |
| 21 | `klipperscreen-install.sh` | optional | Установка/проверка KlipperScreen. |
| 22 | `klipperscreen-theme.sh` | optional | Деплой темы/шрифта KlipperScreen. |
| 23 | `klipperscreen-integr.sh` | optional | Systemd override KlipperScreen. |
| 24 | `maintenance-start.sh` | required | Запуск required/best-effort сервисов. |
| 25 | `verify.sh` | required | Финальная валидация результата. |

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
- `BOOT_DIR`, `CMDLINE_FILE`, `CONFIG_FILE`
- `TREED_DEPLOY_MODE_EFFECTIVE` (`clean|preserve`)
- `TREED_MAINTENANCE_MODE` (`1|0`)

### Klipper / MCU / UART

- `TREED_MCU_TRANSPORT` (`usb|uart`, default `uart`)
- `TREED_MCU_UART_DEV` (default `/dev/serial0`)
- `TREED_UART_DISABLE_BT` (`1|0`, default `1` в `rpi-uart-config`)
- `MCU_SERIAL_BY_ID` (`/dev/serial/by-id/*`, для USB-режима)
- `KLIPPER_SERVICE` (default `klipper`)
- `TREED_ANTI_SHUTDOWN_INFO_TIMEOUT` (default `2`)

### Moonraker / Camera

- `CAM_DEVICE`
- `CAM_ALLOW_VIDEO0_FALLBACK` (`1` разрешает fallback на `/dev/video0`)
- `TREED_CAMERA_REQUIRED` (`1` переводит шаг камеры в fail-fast)
- `TREED_CAM_RESOLUTION` (default `1920x1080`, zoom-профили рассчитаны на этот raw capture)
- `TREED_CAM_FPS` (default `10`)
- `TREED_CAM_ZOOM_DEFAULT_PROFILE` (default `medium`, `wide|medium|close`)
- `TREED_CAM_ZOOM_PORT` (default `8081`, локальный port zoom-sidecar stream backend)
- `TREED_CAM_ZOOM_OUTPUT_WIDTH` / `TREED_CAM_ZOOM_OUTPUT_HEIGHT` (default `1280x720`, выход zoom-sidecar)
- `MOONRAKER_READY_RETRIES` (default `30`)

### KlipperScreen

- `TREED_FORCE_KLIPPERSCREEN_INSTALL` (`1` — принудительная установка)
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
- `TREED_VERIFY_CAMERA` (`auto|0|1`, default `auto`)
- `TREED_CAM_HTTP_RETRIES` (default `3`)
- `TREED_CAM_HTTP_TIMEOUT` (default `8`)
- `TREED_MOONRAKER_HTTP_RETRIES` (default `30`)
- `TREED_REQUIRED_SERVICE_STOP_TIMEOUT` / `TREED_BEST_EFFORT_SERVICE_STOP_TIMEOUT`
- `TREED_REQUIRED_SERVICE_START_TIMEOUT` / `TREED_BEST_EFFORT_SERVICE_START_TIMEOUT`
- `TREED_MAINTENANCE_STATUS_LOG` (`1` — печатать `systemctl status` для optional-service)

## Поведение `TREED_DEPLOY_MODE_EFFECTIVE`

`TREED_DEPLOY_MODE_EFFECTIVE` влияет на шаги:

- `klipper-core.sh`
  - `clean`: полная пересборка runtime без возврата локальных файлов.
  - `preserve`: сохраняется `local_overrides.cfg`.
- `moonraker-config.sh`
  - `clean`: `moonraker.conf` без `.bak`.
  - `preserve`: `backup_file_once` перед перезаписью.
- `klipperscreen-theme.sh`
  - `clean`: `KlipperScreen.conf` без `.bak`.
  - `preserve`: `backup_file_once` перед изменением.

## Практические замечания

- `crowsnest-webcam.sh` optional на уровне оркестратора; для строгого режима используйте `TREED_CAMERA_REQUIRED=1`.
- `crowsnest-webcam.sh` владеет единым camera zoom-контуром:
  - raw `crowsnest.conf` (`ustreamer`, обычно `:8080`);
  - `~/treed/cam/config/zoom_profiles.env` (single source of truth URL/ROI/output);
  - `~/treed/cam/config/zoom_active.env` (default profile, затем runtime-владелец — `zoom_profile_set.sh`);
  - `treed-cam-zoom.service`;
  - marker-блоком nginx routes в server-файле с существующим `/webcam` (`/webcam-treed/*`);
  - Moonraker webcam fragment `50-webcam-treed.conf`.
- `treed-cam.sh` раскладывает runtime scripts в `~/treed/cam/bin`, готовит `~/treed/cam/{config,logs,prints}` и выполняет отложенный `restart treed-cam-zoom.service`, если unit уже задеплоен предыдущим шагом.
- `klipperscreen-install.sh`, `klipperscreen-theme.sh`, `klipperscreen-integr.sh` optional на уровне оркестратора.
- `klipperscreen-theme.sh` для `treed-oled` проверяет наличие `images/*` из `style.css`, при необходимости копирует fallback icon-pack.
- `klipperscreen-theme.sh` устанавливает шрифт `WebPlus IBM MDA` в `/usr/local/share/fonts/treed` и обновляет fontconfig (`fc-cache`).
- `verify.sh` можно запускать отдельно.

Пример standalone-запуска `verify.sh`:

```bash
cd /home/pi/treed/treed-mainshellOS
sudo REPO_DIR="$(pwd)" bash loader/steps/verify.sh
```
