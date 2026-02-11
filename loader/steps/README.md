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
- `rpi-uart-config.sh`
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

- `TREED_MCU_TRANSPORT` - режим связи с MCU (`usb` или `uart`).
- `TREED_MCU_UART_DEV` - UART-устройство для MCU (по умолчанию `/dev/serial0`).
- `TREED_UART_DISABLE_BT` - добавить `dtoverlay=disable-bt` в `config.txt` для UART-контура (default для `uart`: `1`, установите `0`, если BT нужно сохранить).
  В `verify` без явного значения используется auto-режим проверки (не падает, если BT оставлен включенным).
- `MCU_SERIAL_BY_ID` - явная привязка MCU serial (`/dev/serial/by-id/*`).
- `KLIPPER_SERVICE` - имя systemd-сервиса для шага `klipper-anti-shutdown` (по умолчанию `klipper`).

Камера:

- `CAM_DEVICE` - принудительно указать устройство камеры.
- `CAM_ALLOW_VIDEO0_FALLBACK=1` - разрешить fallback на `/dev/video0`.
- `TREED_CAM_RESOLUTION` - постоянное разрешение стрима crowsnest (по умолчанию `800x600`).
- `TREED_CAM_FPS` - постоянный FPS стрима crowsnest (по умолчанию `10`).
- `MOONRAKER_READY_RETRIES` - количество попыток ожидания API Moonraker после рестарта.

KlipperScreen:

- `TREED_FORCE_KLIPPERSCREEN_INSTALL=1` - принудительная переустановка KlipperScreen.
- `TREED_KLIPPERSCREEN_REPO` - URL репозитория KlipperScreen для установки.
- `TREED_KLIPPERSCREEN_REF` - pinned ref/commit для воспроизводимой установки.
- `TREED_KLIPPERSCREEN_START_TIMEOUT` - timeout ожидания старта сервиса.
- `TREED_KLIPPERSCREEN_REQUIRED=1` - делать проверки KlipperScreen в `verify` обязательными (по умолчанию `0`, best-effort).

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

- `crowsnest-webcam.sh` по умолчанию best-effort, fail-fast включается через `TREED_CAMERA_REQUIRED=1`.
- `klipperscreen-install.sh` и `klipperscreen-integr.sh` fail-fast, если сервис KlipperScreen не поднимается в заданный timeout.
- `verify.sh` по умолчанию проверяет KlipperScreen в best-effort режиме; strict-режим включается через `TREED_KLIPPERSCREEN_REQUIRED=1`.
- `verify.sh` можно запускать standalone: `REPO_DIR` вычисляется автоматически, если не передан.

Пример ручного запуска `verify`:

```bash
cd /home/pi/treed/treed-mainshellOS
sudo REPO_DIR="$(pwd)" bash loader/steps/verify.sh
```

## UART-контур (дополнительно)

В режиме `TREED_MCU_TRANSPORT=uart` шаг `rpi-uart-config.sh` дополнительно делает две вещи для стабильного старта Klipper:

- маскирует `serial-getty@ttyAMA0.service` и `serial-getty@ttyS0.service`, чтобы исключить захват UART-консолью;
- разворачивает udev-правило `/etc/udev/rules.d/99-treed-uart-perms.rules` с правами `0660` и группой `dialout` для `/dev/ttyAMA0` и `/dev/ttyS0`, а также применяет эти права сразу в рантайме.

Это убирает сценарий, когда Klipper получает `Permission denied` на `/dev/serial0` после чистого деплоя.
