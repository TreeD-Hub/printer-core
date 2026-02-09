# Loader Steps

Каталог `loader/steps/` содержит атомарные этапы провижининга, которые запускаются из `loader/loader.sh`.

## Группы шагов

Проверки и детект:

- `check-env.sh`
- `detect-rpi.sh`
- `timezone-sync.sh`

Сервисный контур (остановка/возврат сервисов):

- `maintenance-stop.sh`
- `maintenance-start.sh`

Базовая система и boot/UI:

- `packages-core.sh`
- `boot-hdmi-config.sh`
- `plymouth-theme-install.sh`
- `plymouth-initramfs.sh`
- `plymouth-initramfs-config.sh`
- `plymouth-cmdline.sh`
- `plymouth-systemd.sh`

Klipper:

- `klipper-sync.sh`
- `klipper-profiles.sh`
- `klipper-core.sh`
- `klipper-anti-shutdown.sh`

Moonraker/камера/runtime:

- `moonraker-config.sh`
- `crowsnest-webcam.sh`
- `treed-cam.sh`

UI:

- `klipper-mainsail-theme.sh`
- `klipperscreen-install.sh`
- `klipperscreen-integr.sh`

Финальная верификация:

- `verify.sh`

## Контракт шага

Каждый шаг должен:

- быть идемпотентным (повторный запуск не ломает систему);
- логировать ключевые действия через `log_info`/`log_warn`/`log_error`;
- завершаться с ненулевым кодом только при реальной блокирующей ошибке.

## Важные управляющие переменные

Klipper:

- `MCU_SERIAL_BY_ID` - явная привязка MCU serial (`/dev/serial/by-id/*`).
- `KLIPPER_SERVICE` - имя systemd-сервиса для шага `klipper-anti-shutdown` (по умолчанию `klipper`).

Камера:

- `CAM_DEVICE` - принудительно указать устройство камеры.
- `CAM_ALLOW_VIDEO0_FALLBACK=1` - разрешить fallback на `/dev/video0`.
- `MOONRAKER_READY_RETRIES` - количество попыток ожидания API Moonraker после рестарта.

KlipperScreen:

- `TREED_FORCE_KLIPPERSCREEN_INSTALL=1` - принудительная переустановка KlipperScreen.
- `TREED_KLIPPERSCREEN_REPO` - URL репозитория KlipperScreen для установки.
- `TREED_KLIPPERSCREEN_REF` - pinned ref/commit для воспроизводимой установки.
- `TREED_KLIPPERSCREEN_START_TIMEOUT` - timeout ожидания старта сервиса.

Time/NTP:

- `TREED_SET_TIMEZONE=1` - применять timezone в `timezone-sync`.
- `TREED_TIMEZONE` - целевая timezone (по умолчанию `Europe/Moscow`).
- `TREED_ENABLE_NTP=1` - включать NTP через `timedatectl`.

Plymouth/systemd/verify:

- `PLYMOUTH_THEME_NAME` - имя темы Plymouth.
- `TREED_MASK_TTY1` - политика `getty@tty1` (используется в `plymouth-systemd` и `verify`).
- `TREED_VERIFY_CAMERA` - режим camera-проверок в `verify` (`auto`, `0`, `1`).
- `TREED_CAM_HTTP_RETRIES`, `TREED_CAM_HTTP_TIMEOUT` - retry/timeout для snapshot-проверок.
- `TREED_MOONRAKER_HTTP_RETRIES` - retry для проверки `server/webcams/list`.

## Важные замечания по поведению

- `crowsnest-webcam.sh` fail-fast при неоднозначной/неразрешимой камере, если не задан `CAM_DEVICE` и не включен fallback.
- `klipperscreen-install.sh` и `klipperscreen-integr.sh` fail-fast, если сервис KlipperScreen не поднимается в заданный timeout.
- `verify.sh` рассчитан на запуск из loader. Для ручного запуска требуется передать `REPO_DIR`.

Пример ручного запуска `verify`:

```bash
cd /home/pi/treed/treed-mainshellOS
sudo REPO_DIR="$(pwd)" bash loader/steps/verify.sh
```
