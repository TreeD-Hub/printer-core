# TreeD Printer Core

Установка и обслуживание поддерживаемого runtime `treed-v2`: конфигурация
Klipper/Moonraker, host-расширения Klipper, firmware targets и доставка экранного UI.

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

Начальная версия проекта хранится в `VERSION`. Совместимые версии Klipper, Moonraker,
KlipperScreen и Crowsnest, а также версия и checksum Mainsail закреплены в
`runtime-versions.env`. Loader устанавливает эти компоненты по закреплённым
ревизиям и release asset.

TreeD Shell доставляется отдельно из артефакта `treed-shell-ui.zip` релизов
репозитория [TreeD-Hub/printer-ui](https://github.com/TreeD-Hub/printer-ui).
Настройки источника и загрузки UI описаны в [документации loader](loader/README.md).

Core обновляется через тот же виджет настроек пакетом `treed-core-runtime.zip`:
конфиги, host-модули и скрипты TreeD с сохранением локальных данных и откатом.
Для первого включения новый updater доставляется loader, после успешного verify
фиксируется baseline. [Контракт и границы обновления](docs/update-architecture.md).

Workflow релиза запускается после каждого push в `treed-v2`, включая merge PR,
или вручную. Он назначает версию `0.<run_number>.<run_attempt>` и тег
`v0.<run_number>.<run_attempt>` без коммита `VERSION` в ветку. При запуске
тегом `vX.Y.Z` версия должна совпадать с `VERSION` этого коммита. Имена
`treed-mainshellos-source.zip` и `treed-mainshellos-release.json` — сохранённые
имена артефактов для совместимости релизного процесса.

## Установка и обслуживание

Перед установкой подготовьте Rock Pi по [инструкции первого запуска](docs/firstStart.md)
и сверьте состав оборудования ниже. Команда запускает установку, меняет runtime
и после первой установки по умолчанию перезагружает устройство:

```bash
curl -fsSL https://raw.githubusercontent.com/TreeD-Hub/printer-core/treed-v2/bootstrap-pi.sh | bash
```

Loader определяет состояние устройства как `fresh`, `update` или `recover`.
`TREED_DEPLOY_MODE=auto` выбирает `clean` для `fresh` и `preserve` для
`update`/`recover`. После `fresh` bootstrap по умолчанию перезагружает устройство
(`TREED_REBOOT_AFTER_FRESH=1`); повторные раскладки сохраняют runtime-параметры
по [правилам владения конфигами](docs/config-ownership.md).

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
Для adaptive mesh нужны object labels с polygon-координатами в G-code и
`enable_object_processing` в Moonraker; без них подготовка печати не начинается.
Подробности — в [потоке печати и mesh](docs/print-flow.md).

Обычный end G-code слайсера:

```gcode
END_PRINT
```

По умолчанию автосъём выключен (`AUTO_REMOVE=0`): макрос отключает нагрев,
обдув и камеру, выполняет retract при допустимой температуре, Z-hop и парковку
при достоверных координатах, затем отключает моторы. Охлаждения стола не ждёт
и нижнюю Z-парковку не запускает.

Чтобы включить автосъём для конкретной печати, используйте:

```gcode
END_PRINT AUTO_REMOVE=1
```

В этом режиме макрос ждёт охлаждения стола до 40 °C, находит нижнюю опору
через Z DIAG и выполняет пять циклов Z на 25 мм вверх-вниз при 50 мм/с.
Флаг действует только на текущий вызов. Допустимы `0` и `1`; другие значения
отклоняются до выполнения команд. Полный контракт — в
[методичке по макросам](klipper/profiles/treed_v2_corexy_v1/macros-usage.md#end_print).

Отмена через кнопку UI (`/printer/print/cancel`) или одиночный `CANCEL_PRINT`
в консоли Moonraker сразу выключает нагрев, прерывает температурные ожидания
и остаток макроса, затем выполняет штатную отмену. Уже начатая команда
движения/homing завершается штатно; для немедленного отключения движения
используется отдельный аварийный стоп. Подробности — в
[контракте отмены](klipper/profiles/treed_v2_corexy_v1/macros-usage.md#cancel_print).

Свет при запуске принтера по умолчанию выключен, при начале печати — включается.
Переключатели находятся в UI «Управление → Освещение»; настройки сохраняются на
принтере через `TREED_LIGHT_SETTINGS STARTUP=0|1 PRINT_START=0|1`.
Отключение автосвета не меняет текущее ручное состояние лампы.

Вкладка перемещений доступна во время задания, но оси и парковка заблокированы.
Подача/выгрузка филамента разрешена на паузе при нагретом сопле, если сейчас не
идёт автоматическая прочистка. Во время печати ручная подача запрещена.

Паузы, runout, этапы и результат прочистки, завершение и отмена публикуются через
[контракт событий Moonraker](contracts/printer-events.md). Обновлённый экранный UI
показывает окно с причиной и хранит историю текущего сеанса; веб-клиент может
подписаться на тот же поток. Для новых функций обновляются и core, и UI.

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

Переключение экранного UI и проверка текущего режима
(`ts` — TreeD Shell, по умолчанию; `ks` — KlipperScreen):

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

## Состав и runtime-пути

- `loader/loader.sh` — точка входа установки; актуальный порядок шагов описан
  в [README loader](loader/README.md#реестр-шагов).
- `loader/steps/` — шаги provisioning режима `apply`; `check` выполняется
  оркестратором без запуска шагов.
- `klipper/` — канонические конфигурации и профиль `treed_v2_corexy_v1`.
- `klipper-host/` — доставляемые в `klippy/extras` расширения: нижняя опора и
  измерение доступного хода Z, защита движений, sensorless-калибровка и датчик филамента.
- `moonraker/` — базовая конфигурация и компоненты Moonraker.
- `runtime-scripts/` — устанавливаемые runtime-скрипты.
- `contracts/` — образец опубликованного контракта Printer Core ↔ UI для проверок совместимости.
- `mainsail/` — тема и UI-ресурсы Mainsail.
- `klipperscreen/` — тема KlipperScreen.
- `plymouth/` — тема загрузочного экрана.
- `firmware/` — target-конфиги и описание процесса сборки/прошивки.
- `tools/` — диагностика, сервисные утилиты и проверки контрактов.

Репозиторные конфиги копируются loader-ом через staging в runtime на устройстве.
Границы владения и локальных overrides описаны в
[`docs/config-ownership.md`](docs/config-ownership.md).

## Документация

- [Установка и переменные окружения](docs/README.md)
- [Loader: режимы, реестр шагов и настройки](loader/README.md)
- [Владение runtime-конфигами](docs/config-ownership.md)
- [Профиль `treed_v2_corexy_v1`](klipper/profiles/treed_v2_corexy_v1/README.md)
- [Host-расширения Klipper](klipper-host/README.md)
- [Поток печати и mesh](docs/print-flow.md)
- [Первичная калибровка Eddy](docs/eddy-calibration.md)
- [Диагностика камеры](docs/camera.md)
- [Сервисные тесты движения](docs/service-motion-tests.md)
- [Сборка и ручная прошивка MCU](firmware/README.md)
- [Использование макросов профиля](klipper/profiles/treed_v2_corexy_v1/macros-usage.md)
- [Локальные проверки контрактов](tools/tests/README.md)

Команды проверки выбирайте по затронутой подсистеме из README выше.
Статические и офлайн-проверки не подтверждают готовность механики, CAN и нагревателей.
