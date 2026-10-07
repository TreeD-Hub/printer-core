# Документация `printer-core`

Инструкции установки, калибровки и обслуживания TreeD V2.
Обзор проекта: [корневой README](../README.md).
Границы репозиторных конфигов и локальных настроек: [владение конфигами](config-ownership.md).

## Быстрый install path (V2)

Команда выполняется на подготовленном Rock Pi и применяет изменения.
Перед запуском прочитайте [первую настройку](firstStart.md) и
[режимы loader](../loader/README.md); после `fresh` по умолчанию следует перезагрузка.

```bash
curl -fsSL https://raw.githubusercontent.com/TreeD-Hub/printer-core/treed-v2/bootstrap-pi.sh | bash
```

## Контракт переменных

- `TREED_MAIN_MCU_CANBUS_UUID` — Octopus Pro UUID, default `d372e54bf965`.
- `TREED_CAN_IFACE` — default `can0`.
- `TREED_CAN_BITRATE` — default `1000000`.
- `TREED_CAN_TXQUEUE` — default `128`.
- `TREED_DEPLOY_MODE` — `auto|clean|preserve`, default `auto`; auto выбирает режим по `/run/treed-loader/state.env`.
- `TREED_KLIPPER_PREFLIGHT` — `0|1`, default `1`; включает readiness-проверку перед стартом Klipper.
- `TREED_KLIPPER_PREFLIGHT_WAIT_SEC` — default `12`; общий таймаут ожидания CAN-интерфейса.
- `TREED_KLIPPER_PREFLIGHT_INTERVAL_SEC` — default `1`; интервал повторной проверки.
- `TREED_KLIPPER_PREFLIGHT_CAN_UUIDS_REQUIRED` — `0|1`, default `0`; при `1` strict UUID-gate через `canbus_query.py`, при `0` query не запускается.
- `TREED_EBB_CANBUS_UUID` — EBB42 UUID, default `efaf957ab20f`; auto-detect не используется, чтобы не принять Eddy за EBB.
- `TREED_EDDY_CANBUS_UUID` — Eddy UUID, default `95485b93332a`.
- `TREED_NONINTERACTIVE` — `0|1`, по умолчанию `1`; отключает запросы apt/dpkg/needrestart.
- `TREED_KLIPPERSCREEN_INSTALL_SERVICE` — ответ installer на установку сервиса, по умолчанию `1`.
- `TREED_KLIPPERSCREEN_BACKEND` — графический backend, по умолчанию `X`.
- `TREED_KLIPPERSCREEN_NETWORK_MANAGER` — ответ installer на установку NetworkManager, по умолчанию `N`.
- `TREED_KLIPPERSCREEN_START_AFTER_INSTALL` — запуск сервиса внешним installer, по умолчанию `0`.
- `TREED_UI_MODE` — `ts|ks`, по умолчанию `ts`; экранный UI (`ts` — TreeD Shell, `ks` — KlipperScreen).
- `TREED_SHELL_RELEASE_API_URL` — API релизов UI, по умолчанию `https://api.github.com/repos/TreeD-Hub/printer-ui/releases`.
- `TREED_SHELL_RELEASE_TAG_PREFIX` — префикс тега релиза, по умолчанию `ui-main-`.
- `TREED_SHELL_UI_ASSET_NAME` — имя артефакта, по умолчанию `treed-shell-ui.zip`.
- `TREED_SHELL_UI_ARCHIVE_URL` — необязательный прямой URL архива; заменяет поиск через GitHub API.
- `TREED_SHELL_RUNTIME_DIR` — корневой runtime-каталог, по умолчанию `${PI_HOME}/treed/treed-shell-runtime`.
- `TREED_SHELL_WEB_DIR` — распакованный UI, по умолчанию `${TREED_SHELL_RUNTIME_DIR}/ui`.
- `TREED_SHELL_HTTP_PORT` — порт локального HTTP-сервера, по умолчанию `8787`.
- `TREED_SHELL_BROWSER_BIN` — необязательный путь к исполняемому файлу браузера.

Полный справочник параметров: [`loader/steps`](../loader/steps/README.md).

## Контракт железа

- Host SBC: Rock Pi / Rock Pi 4 Plus.
- Main MCU: Octopus Pro по CAN.
- CAN adapter: U2C V2.1 (USB -> CAN).
- Toolhead MCU: EBB42 по CAN (required).
- Probe: Eddy / Eddy Duo по CAN (required для профиля `treed_v2_corexy_v1`).
- На EBB42 контакт `CAN L` соединяется с зелёным проводом Eddy Duo.
- Z: при Eddy enabled штатный `G28 Z`/полный `G28` использует Eddy как `probe:z_virtual_endstop`, затем уточняет Z через `PROBE` и `SET_KINEMATIC_POSITION`; Zmax sensorless оставлен только как аппаратный резерв вне основного профиля.

Ветка `treed-v2` не поддерживает RN12/RPi/UART-миграции.

Другие инструкции: [справочник макросов принтера](../klipper/profiles/treed_v2_corexy_v1/macros-usage.md), [поток печати и mesh](print-flow.md), [калибровка Eddy](eddy-calibration.md), [камера](camera.md), [сервисные тесты движения](service-motion-tests.md).

[Приёмка Z-bottom/Eddy](z-eddy-acceptance.md): программные проверки, аппаратные
серии, защита pending autosave и сравнение cold-start пакетов.

[Архитектура обновлений](update-architecture.md): служба обновления, целевой A/B-контур, сохранение данных и условия аппаратной приёмки.

Программные проверки и требования к среде: [`tools/tests`](../tools/tests/README.md).
