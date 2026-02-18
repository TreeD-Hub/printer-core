# Loader Steps

Каталог `loader/steps/` содержит атомарные этапы provisioning, которые запускаются из `loader/loader.sh`.

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
- `klipperscreen-theme.sh`
- `klipperscreen-integr.sh`

Финальная верификация:

- `verify.sh`

## Контракт шага

Каждый шаг должен:

- быть идемпотентным;
- логировать ключевые действия через `log_info`/`log_warn`/`log_error`;
- завершаться с ненулевым кодом только при реальной блокирующей ошибке.

## Важные управляющие переменные

Klipper:

- `TREED_MCU_TRANSPORT` — режим связи с MCU (`usb` или `uart`).
- `TREED_MCU_UART_DEV` — UART-устройство для MCU (по умолчанию `/dev/serial0`).
- `TREED_UART_DISABLE_BT` — добавлять `dtoverlay=disable-bt` для UART-контура (`1`/`0`).
- `MCU_SERIAL_BY_ID` — явная привязка MCU serial (`/dev/serial/by-id/*`).
- `KLIPPER_SERVICE` — имя systemd-сервиса для `klipper-anti-shutdown`.
- `TREED_DEPLOY_MODE` — режим runtime-деплоя (`auto|clean|preserve`, по умолчанию `auto`).
  Auto-резолв: `dev -> clean`, любая определенная не-`dev` ветка -> `preserve`, неопределенная ветка (`HEAD`) -> `clean`.
  Для шагов используется вычисленное значение `TREED_DEPLOY_MODE_EFFECTIVE`.

Камера:

- `CAM_DEVICE` — принудительно задать устройство камеры.
- `CAM_ALLOW_VIDEO0_FALLBACK=1` — разрешить fallback на `/dev/video0`.
- `TREED_CAM_RESOLUTION` — разрешение crowsnest (по умолчанию `1024x768`).
- `TREED_CAM_FPS` — FPS crowsnest (по умолчанию `10`).
- `MOONRAKER_READY_RETRIES` — число попыток ожидания API Moonraker.

KlipperScreen:

- `TREED_FORCE_KLIPPERSCREEN_INSTALL=1` — принудительная переустановка KlipperScreen.
- `TREED_KLIPPERSCREEN_REPO` — URL репозитория KlipperScreen.
- `TREED_KLIPPERSCREEN_REF` — pinned ref/commit.
- `TREED_KLIPPERSCREEN_START_TIMEOUT` — timeout ожидания старта сервиса.
- `TREED_KLIPPERSCREEN_HOME` — путь к каталогу установки KlipperScreen.
  Если не задан, шаги KlipperScreen пытаются взять `WorkingDirectory` из `KlipperScreen.service`, fallback — `${PI_HOME}/KlipperScreen`.
- `TREED_KS_THEME` — тема KlipperScreen (`treed-oled`, `material-dark`, `keep`).
- `TREED_KS_LANGUAGE` — язык интерфейса KlipperScreen (по умолчанию `ru`, `keep` — не менять `language` в `KlipperScreen.conf`).
- `TREED_KLIPPERSCREEN_REQUIRED=1` — сделать проверки KlipperScreen обязательными в `verify`.

Time/NTP:

- `TREED_SET_TIMEZONE=1` — применять timezone.
- `TREED_TIMEZONE` — целевая timezone.
- `TREED_ENABLE_NTP=1` — включать NTP через `timedatectl`.

Plymouth/systemd/verify:

- `PLYMOUTH_THEME_NAME` — имя темы Plymouth.
- `TREED_MASK_TTY1` — политика `getty@tty1`.
- `TREED_VERIFY_CAMERA` — режим camera-проверок в `verify` (`auto`, `0`, `1`).
- `TREED_CAM_HTTP_RETRIES`, `TREED_CAM_HTTP_TIMEOUT` — retry/timeout snapshot-проверок.
- `TREED_MOONRAKER_HTTP_RETRIES` — retry проверки `server/webcams/list`.

## TREED_DEPLOY_MODE: матрица поведения

- `klipper-core.sh`
  `clean`: полная пересборка runtime без восстановления локальных файлов.
  `preserve`: сохраняется только `local_overrides.cfg`.
- `moonraker-config.sh`
  `clean`: `moonraker.conf` перезаписывается без `.bak`.
  `preserve`: перед перезаписью выполняется `backup_file_once`.
- `klipperscreen-theme.sh`
  `clean`: `KlipperScreen.conf` обновляется без `.bak`.
  `preserve`: перед обновлением выполняется `backup_file_once`.

## Важные замечания по поведению

- `crowsnest-webcam.sh` по умолчанию best-effort (optional step). Для строгого режима используйте `TREED_CAMERA_REQUIRED=1` и/или `TREED_VERIFY_CAMERA=1`.
- `klipperscreen-install.sh`, `klipperscreen-theme.sh`, `klipperscreen-integr.sh` остаются optional на уровне `loader.sh`.
- `klipperscreen-theme.sh` для `treed-oled` дополнительно гарантирует наличие `styles/treed-oled/images`: если в теме нет иконок, копирует fallback-пакет из доступной стоковой темы KlipperScreen и выбирает пакет, который покрывает обязательные `images/*`-ссылки из `style.css`.
- `klipperscreen-theme.sh` устанавливает шрифт темы `WebPlus IBM MDA` в `/usr/local/share/fonts/treed` и обновляет fontconfig (`fc-cache`).
- `verify.sh` можно запускать standalone: `REPO_DIR` вычисляется автоматически, если не передан.

Пример ручного запуска `verify`:

```bash
cd /home/pi/treed/treed-mainshellOS
sudo REPO_DIR="$(pwd)" bash loader/steps/verify.sh
```

## UART-контур (дополнительно)

В режиме `TREED_MCU_TRANSPORT=uart` шаг `rpi-uart-config.sh`:

- маскирует `serial-getty@ttyAMA0.service` и `serial-getty@ttyS0.service`;
- разворачивает правило `/etc/udev/rules.d/99-treed-uart-perms.rules` c правами `0660` и группой `dialout` для `ttyAMA0/ttyS0`;
- применяет права сразу в runtime.

Это убирает сценарий `Permission denied` на `/dev/serial0` после чистого деплоя.
