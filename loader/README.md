# Loader

Каталог `loader/` содержит оркестратор provisioning и шаги, которые приводят систему к целевому runtime-состоянию.

## Структура

- `loader/loader.sh` — главный оркестратор.
- `loader/lib/*.sh` — общие библиотеки функций.
- `loader/steps/*.sh` — атомарные шаги provisioning.

## Как работает оркестратор

`loader.sh` выполняет:

1. Нормализацию `*.sh` в `loader/**` (убирает CRLF, выставляет executable-bit).
2. Определение `PI_USER` / `PI_HOME` и boot-путей (`BOOT_DIR`, `CMDLINE_FILE`, `CONFIG_FILE`).
3. Fail-fast проверку наличия `cmdline.txt` и `config.txt`.
4. Вычисление режима деплоя (`TREED_DEPLOY_MODE_EFFECTIVE`).
5. Последовательный запуск реестра шагов.

Контуры выполнения:

- `required` шаг: ошибка завершает loader.
- `optional` шаг: ошибка логируется, provisioning продолжается.

## Реестр шагов

Порядок фиксирован и задается в `loader/loader.sh`.

| # | Шаг | Тип | Назначение |
|---|---|---|---|
| 1 | `check-env` | required | Проверка базового окружения и контракта переменных. |
| 2 | `detect-rpi` | required | Детект модели RPi и boot-путей. |
| 3 | `timezone-sync` | required | Синхронизация timezone/NTP. |
| 4 | `maintenance-stop` | required | Контролируемая остановка runtime-сервисов. |
| 5 | `packages-core` | required | Установка базовых пакетов. |
| 6 | `boot-hdmi-config` | required | Управляемый HDMI-блок и `gpu_mem`. |
| 7 | `rpi-uart-config` | required | Подготовка UART-контура MCU. |
| 8 | `plymouth-theme-install` | required | Установка темы Plymouth. |
| 9 | `plymouth-initramfs` | required | Пересборка initramfs. |
| 10 | `plymouth-initramfs-config` | required | Привязка initramfs в `config.txt`. |
| 11 | `plymouth-cmdline` | required | Нормализация kernel cmdline. |
| 12 | `plymouth-systemd` | required | Политика `getty@tty1` и unit Plymouth. |
| 13 | `klipper-sync` | required | Синхронизация `klipper/` в staging. |
| 14 | `klipper-profiles` | required | Применение профиля RN12, serial-path для RN12 + EBB42 USB и canbus_uuid для Eddy Duo. |
| 15 | `klipper-core` | required | Раскладка staging в runtime-конфиг. |
| 16 | `klipper-adxl-rpi` | required | Обязательная интеграция ADXL345/Input Shaper (только onboard EBB42). |
| 17 | `klipper-anti-shutdown` | required | Сброс MCU shutdown при необходимости. |
| 18 | `moonraker-config` | required | Деплой Moonraker-конфигов и компонента. |
| 19 | `crowsnest-webcam` | optional | Настройка камеры/crowsnest/Moonraker webcam-фрагмента. |
| 20 | `treed-cam` | required | Runtime-скрипты камеры TreeD. |
| 21 | `klipper-mainsail-theme` | required | Синхронизация темы Mainsail. |
| 22 | `klipperscreen-install` | optional | Установка/проверка KlipperScreen. |
| 23 | `klipperscreen-theme` | optional | Деплой темы/шрифта KlipperScreen. |
| 24 | `klipperscreen-integr` | optional | Systemd override KlipperScreen. |
| 25 | `maintenance-start` | required | Запуск required/best-effort сервисов. |
| 26 | `verify` | required | Финальная валидация всего контура. |

## Ключевые переменные оркестратора

- `REPO_DIR` — путь к репозиторию.
- `PI_USER`, `PI_HOME` — целевой пользователь и его домашний каталог.
- `BOOT_DIR`, `CMDLINE_FILE`, `CONFIG_FILE` — найденные boot-пути.
- `TREED_MAINTENANCE_MODE` — включение maintenance-шагов (`1`/`0`).
- `TREED_DEPLOY_MODE` — `auto|clean|preserve`.
- `TREED_DEPLOY_MODE_EFFECTIVE` — вычисленное итоговое значение.
- `TREED_DEPLOY_BRANCH` — обнаруженная git-ветка (пусто в detached `HEAD`).
- `TREED_EBB_SERIAL_BY_ID` — опциональный override USB serial EBB (`/dev/serial/by-id/*`).
- `TREED_EDDY_CANBUS_UUID` — обязательный canbus UUID для `profiles/rn12_corexy_v1/probe_eddy_duo.cfg` при первом деплое Eddy Duo.
- Автоподхват EBB использует vendor-маску `/dev/serial/by-id/*stm32g0b1*`: при одном кандидате путь берется автоматически, при 0/многих — fail-fast.

Auto-резолв для `TREED_DEPLOY_MODE=auto`:

- `dev` -> `clean`
- любая определенная не-`dev` ветка -> `preserve`
- неопределенная ветка (`HEAD`) -> `clean`

## Запуск

```bash
cd /home/pi/treed/treed-mainshellOS
sudo bash loader/loader.sh
```

## Смежная документация

- `loader/lib/README.md` — описание библиотек `loader/lib`.
- `loader/steps/README.md` — описание шагов и env-параметров.
- `docs/config-ownership.md` — ownership runtime-артефактов.
- `docs/rn_v12_to_klipper.md` — детали перехода RN12 `usb -> uart`.
