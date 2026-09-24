# TreeD Printer Core

Единая точка входа для поддерживаемого runtime `treed-v2`: образ принтера,
конфигурация Klipper/Moonraker, firmware targets и экранный UI.

## Поддерживаемая V2-модель

```text
Rock Pi (Armbian Debian 12)
 └─ USB -> U2C V2.1
          ├─ CAN -> Octopus Pro (main MCU, required)
          ├─ CAN -> EBB42 (required)
          └─ CAN -> Eddy Duo (required)
```

Активный профиль `treed_v2_corexy_v1` использует Eddy как штатный Z-endstop.
Без Eddy этот профиль не поддерживается. Ветка не поддерживает RN12/RPi/UART
legacy-контур.

## Версии и release assets

Версия проекта хранится в `VERSION`. Совместимые версии Klipper, Moonraker,
KlipperScreen и Crowsnest, а также версия и checksum Mainsail закреплены в
`runtime-versions.env`. Loader не выбирает `latest` для runtime-зависимостей.

Release workflow запускается тегом `vX.Y.Z`, совпадающим с `VERSION`. Имена
`treed-mainshellos-source.zip` и `treed-mainshellos-release.json` — сохранённые
**legacy compatibility asset names** для совместимости релизного процесса.

## Установка и обслуживание

Быстрый install на Rock Pi:

```bash
curl -fsSL https://raw.githubusercontent.com/TreeD-Hub/printer-core/treed-v2/bootstrap-pi.sh | bash
```

Loader определяет состояние устройства как `fresh`, `update` или `recover`.
`TREED_DEPLOY_MODE=auto` выбирает `clean` для `fresh` и `preserve` для
`update`/`recover`. После `fresh` выполняется перезагрузка; повторные раскладки
сохраняют runtime-параметры.

Для read-only проверки существующего клона запускать loader в режиме `check`:

```bash
sudo env TREED_LOADER_MODE=check bash loader/loader.sh
```

Обычный apply требует готового Klipper и доступных main MCU, EBB42 и Eddy.
`TREED_ALLOW_HARDWARE_NOT_READY=1` разрешает стендовый apply без полного набора
железа, но не означает готовность принтера к печати.

## Часто используемые команды

В start G-code слайсера передавайте температуры первого слоя:

```gcode
START_PRINT BED_TEMP=[bed_temperature_initial_layer_single] EXTRUDER_TEMP=[nozzle_temperature_initial_layer]
```

Перед каждой печатью `START_PRINT` строит новую adaptive Eddy mesh через
`rapid_scan`. Сохранённые mesh-профили в обычной печати не загружаются.
Подробности — в [потоке печати и mesh](docs/print-flow.md).

Ручное сервисное сканирование стола:

```gcode
TREED_BED_MESH_CALIBRATE_EDDY PROFILE=default METHOD=scan
```

Полная калибровка input shaper:

```gcode
TREED_SHAPER_CALIBRATE_FULL ACCEL=25000
```

Это сервисная операция; профильное описание — в разделе
[Input shaper](klipper/profiles/treed_v2_corexy_v1/README.md#калибровка-input-shaper).

Переключение экранного UI и проверка текущего режима:

```bash
sudo treed-ui ts
sudo treed-ui ks
treed-ui status
```

Loader собирает firmware-артефакты Octopus, EBB42 и Eddy, но не прошивает MCU.
Ручную прошивку выполнять по [инструкции firmware](firmware/README.md) после
проверки модели платы, target, артефакта и `klipper.dict`.

`TREED_XY_MOTION_TEST` — сервисный XY-тест, не команда для печати:

```gcode
TREED_XY_MOTION_TEST SPEED=200 ACCEL=5000 ITER=2 Z=20 END_Z=100
```

Перед его запуском прочитайте [инструкцию сервисного теста движения](docs/service-motion-tests.md).

## Безопасность и статус

Production apply требует `Klipper state=ready` и подключённые Octopus Pro,
EBB42 и Eddy. Камера необязательна, если не включён строгий режим.
Перед ручной прошивкой проверьте ревизию платы и соответствие артефакта.
Стартовую подготовку Rock Pi см. в [`docs/firstStart.md`](docs/firstStart.md).

## Первичная калибровка Eddy

`PROBE_EDDY_CURRENT_CALIBRATE_AUTO` разрешён только при достоверной Z-позиции.
При неизвестной Z нужен отдельный проверенный сервисный порядок; не запускайте
AUTO как следующий шаг вслепую. Полная процедура находится в
[инструкции калибровки Eddy](docs/eddy-calibration.md).

## Runtime и source of truth

- `loader/loader.sh` — entrypoint provisioning; актуальный порядок шагов описан
  в [модели владения конфигами](docs/config-ownership.md).
- `loader/steps/` — изолированные шаги apply/check.
- `klipper/` — канонические конфигурации и профиль `treed_v2_corexy_v1`.
- `moonraker/` — базовая конфигурация и компоненты Moonraker.
- `runtime-scripts/` — устанавливаемые runtime-скрипты.
- `mainsail/` — тема и UI-ресурсы Mainsail.
- `klipperscreen/` — тема KlipperScreen.
- `firmware/` — target-конфиги и описание процесса сборки/прошивки.

Репозиторные конфиги копируются loader-ом через staging в runtime на устройстве.
Границы владения и локальных overrides описаны в
[`docs/config-ownership.md`](docs/config-ownership.md).

## Документация

- [Установка и переменные окружения](docs/README.md)
- [Владение runtime-конфигами](docs/config-ownership.md)
- [Профиль `treed_v2_corexy_v1`](klipper/profiles/treed_v2_corexy_v1/README.md)
- [Поток печати и mesh](docs/print-flow.md)
- [Первичная калибровка Eddy](docs/eddy-calibration.md)
- [Диагностика камеры](docs/camera.md)
- [Сервисные тесты движения](docs/service-motion-tests.md)
- [Сборка и ручная прошивка MCU](firmware/README.md)
- [Использование макросов профиля](klipper/profiles/treed_v2_corexy_v1/macros-usage.md)
