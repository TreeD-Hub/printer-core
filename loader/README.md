# Loader

Каталог `loader/` содержит оркестратор provisioning и шаги, которые приводят систему к целевому runtime-состоянию V2.

## Структура

- `loader/loader.sh` — главный оркестратор.
- `loader/lib/*.sh` — общие библиотеки функций.
- `loader/steps/*.sh` — атомарные шаги provisioning.

## Как работает оркестратор

`loader.sh` выполняет:
1. Нормализацию `*.sh` в `loader/**` (убирает CRLF, выставляет executable-bit).
2. Определение `PI_USER` / `PI_HOME` и host-aware boot-контекста (`BOOT_DIR`, `TREED_BOOT_BACKEND`, `CMDLINE_FILE`, `CONFIG_FILE`, `ARMBIAN_ENV_FILE`).
3. Fail-fast проверку boot backend-контракта (`rpi`, `armbian` или `extlinux`).
4. Вычисление режима деплоя (`TREED_DEPLOY_MODE_EFFECTIVE`).
5. Последовательный запуск реестра шагов.

Контуры выполнения:
- `required` шаг: ошибка завершает loader.
- `optional` шаг: ошибка логируется, provisioning продолжается.

## Реестр шагов

| # | Шаг | Тип | Назначение |
|---|---|---|---|
| 1 | `check-env` | required | Проверка контракта V2 переменных и окружения. |
| 2 | `detect-rpi` | required | Host-aware определение backend (`rpi|armbian|extlinux`) и boot-файлов. |
| 3 | `timezone-sync` | required | Синхронизация timezone/NTP. |
| 4 | `maintenance-stop` | required | Контролируемая остановка runtime-сервисов. |
| 5 | `packages-core` | required | Установка базовых пакетов. |
| 6 | `runtime-bootstrap` | required | Bootstrap Klipper/Moonraker unit-файлов, venv и runtime-каталогов. |
| 7 | `can-setup` | required | Подъем `can0` через systemd oneshot + `ip link`. |
| 8 | `firmware-build` | required | Сборка firmware main+EBB(+Eddy) и публикация build-отчета. |
| 9 | `boot-hdmi-config` | required | RPi: `config.txt`; Armbian: `armbianEnv.txt`; Extlinux: `append video=...`. |
| 10 | `plymouth-theme-install` | required | Установка темы Plymouth. |
| 11 | `plymouth-initramfs` | required | Пересборка initramfs. |
| 12 | `plymouth-initramfs-config` | required | RPi/Armbian/Extlinux backend-aware валидация initrd-контура. |
| 13 | `plymouth-cmdline` | required | RPi: `cmdline.txt`; Armbian: `extraargs`; Extlinux: `append` в `extlinux.conf`. |
| 14 | `plymouth-systemd` | required | Политика `getty@tty1` и unit Plymouth. |
| 15 | `klipper-sync` | required | Синхронизация `klipper/` в staging. |
| 16 | `klipper-profiles` | required | Профиль V2: main USB serial + CAN UUID EBB/Eddy. |
| 17 | `klipper-core` | required | Раскладка staging в runtime-конфиг. |
| 18 | `klipper-adxl-rpi` | required | Проверка mandatory ADXL/Input Shaper через EBB. |
| 19 | `klipper-anti-shutdown` | required | Сброс MCU shutdown при необходимости. |
| 20 | `moonraker-config` | required | Деплой Moonraker-конфигов и компонента. |
| 21 | `crowsnest-webcam` | optional | Настройка камеры/crowsnest/Moonraker webcam-фрагмента. |
| 22 | `treed-cam` | required | Runtime-скрипты камеры TreeD. |
| 23 | `klipper-mainsail-theme` | required | Синхронизация темы Mainsail. |
| 24 | `klipperscreen-install` | optional | Установка/проверка KlipperScreen. |
| 25 | `klipperscreen-theme` | optional | Деплой темы/шрифта KlipperScreen. |
| 26 | `klipperscreen-integr` | optional | Systemd override KlipperScreen. |
| 27 | `maintenance-start` | required | Запуск required/best-effort сервисов. |
| 28 | `verify` | required | Финальная валидация V2-контура с паритетной отчетностью. |

## Ключевые переменные

- `TREED_MAIN_MCU_SERIAL_BY_ID` — optional override `/dev/serial/by-id/*`.
- `TREED_MAIN_MCU_SERIAL_MASK` — маска автопоиска main MCU (`/dev/serial/by-id/*stm32*` по умолчанию).
- `TREED_CAN_IFACE` — default `can0`.
- `TREED_CAN_BITRATE` — default `1000000`.
- `TREED_CAN_TXQUEUE` — default `1024`.
- `TREED_CAN_AUTOBITRATE` — `0|1`, default `1` (перебор типовых bitrate при auto-detect UUID).
- `TREED_CAN_AUTOBITRATE_LIST` — default `1000000 500000 250000 125000`.
- `TREED_EBB_CANBUS_UUID` — рекомендуется задавать явно; если пусто, `klipper-profiles.sh` пробует auto-detect через `canbus_query` (только при единственном UUID на шине).
- `TREED_EDDY_ENABLED` — `0|1`, default `0`.
- `TREED_EDDY_CANBUS_UUID` — required только при `TREED_EDDY_ENABLED=1`.
- `TREED_Z_ENDSTOP_PIN` — physical Z endstop when Eddy is disabled, default `PG10`.
- `TREED_Z_POSITION_ENDSTOP` — Z endstop coordinate when Eddy is disabled, default `0.5`.
- `TREED_NONINTERACTIVE` — `0|1`, default `1`; убирает apt/dpkg/needrestart prompts.
- `TREED_FIRMWARE_BUILD_ENABLED` — `0|1`, default `1`.
- `TREED_KLIPPER_SRC_DIR` — default `${PI_HOME}/klipper`.
- `TREED_FIRMWARE_ARTIFACTS_DIR` — default `${PI_HOME}/treed/firmware-artifacts/treed-v2`.
- `TREED_FW_MAIN_CONFIG` / `TREED_FW_EBB_CONFIG` / `TREED_FW_EDDY_CONFIG` — пути к Kconfig target-файлам сборки.
- `TREED_RUNTIME_BOOTSTRAP` — `0|1`, default `1` (создание/проверка unit-файлов и venv Klipper/Moonraker).

## Запуск

```bash
cd /home/pi/treed/treed-mainshellOS
sudo bash loader/loader.sh
```

## Смежная документация

- `loader/lib/README.md` — описание библиотек `loader/lib`.
- `loader/steps/README.md` — описание шагов и env-параметров.
- `docs/config-ownership.md` — ownership runtime-артефактов.
