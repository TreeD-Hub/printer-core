# TreeD MainshellOS

Единая точка входа для ветки `treed-v2`.

## V2 runtime-модель

```text
Rock Pi (Armbian Debian 12)
 └─ USB -> U2C V2.1
          ├─ CAN -> Octopus Pro (main MCU, required)
          ├─ CAN -> EBB42 (required)
          └─ CAN -> Eddy Duo (enabled by default)
```

Ветка `treed-v2` не поддерживает RN12/RPi/UART legacy-контур.


## Версионирование и релизы

Версия `treed-mainshellOS` хранится в `VERSION` в формате `x.y.z`.

Релиз создается workflow `.github/workflows/release.yml` по тегу `vX.Y.Z`. Тег должен совпадать с содержимым `VERSION`; workflow публикует `treed-mainshellos-source.zip` и `treed-mainshellos-release.json`.

## Быстрый запуск (копируй в SSH)

```bash
curl -fsSL https://raw.githubusercontent.com/TreeD-Hub/treed-mainshellOS/treed-v2/bootstrap-pi.sh | bash
```

Loader сам определяет `fresh|update|recover`, выбирает `clean|preserve` и ребутает только после `fresh`.

## Вариации UI

sudo treed-ui ks
sudo treed-ui status

## Вариации работы перед печатью

Стартовый G-code слайсера должен вызывать `START_PRINT` и передавать температуры первого слоя:

```gcode
START_PRINT BED_TEMP=[bed_temperature_initial_layer_single] EXTRUDER_TEMP=[nozzle_temperature_initial_layer] MESH=load MESH_PROFILE=default
```

Дополнительно можно включить быструю TreeD-калибровку input shaper перед печатью:

```gcode
START_PRINT BED_TEMP=[bed_temperature_initial_layer_single] EXTRUDER_TEMP=[nozzle_temperature_initial_layer] MESH=adaptive SHAPER=light SHAPER_ACCEL=12000
```

Параметры shaper-контура:
- `SHAPER=none` — не трогать input shaper перед печатью (default).
- `SHAPER=light` — быстрый прогон вокруг уже сохраненных частот X/Y, применяет результат на текущую печать без `SAVE_CONFIG`.
- `SHAPER=full` — полный быстрый sweep X/Y перед печатью, но из `START_PRINT` всегда без сохранения, чтобы не перезапускать Klipper.
- `SHAPER_ACCEL=...` — целевое ускорение калибровки. Можно передать число из профиля/слайсера; если не передано, `light` берет текущий live `max_accel`, а ручной `full` использует `25000`.

Варианты mesh-контура:
- `MESH=load MESH_PROFILE=default` — использовать сохраненную сетку без новой калибровки.
- `MESH=adaptive MESH_METHOD=scan` — повседневный вариант: прогреть стол, сделать Eddy Z-home, построить адаптивную сетку рядом с моделью и выполнить KAMP purge.
- `MESH=adaptive MESH_METHOD=rapid_scan` — быстрый повседневный вариант, если важнее скорость, чем максимальная точность.
- `MESH=calibrate MESH_METHOD=rapid_scan MESH_PROFILE=treed_full` — быстрая калибровка всего стола перед печатью.
- `MESH=calibrate MESH_METHOD=scan MESH_PROFILE=treed_full` — более точная калибровка всего стола перед печатью.
- `MESH=calibrate MESH_METHOD=automatic MESH_PROFILE=treed_full` — самый точный, но самый медленный точечный режим.

Пример для быстрой калибровки всего стола:

```gcode
START_PRINT BED_TEMP=[bed_temperature_initial_layer_single] EXTRUDER_TEMP=[nozzle_temperature_initial_layer] MESH=calibrate MESH_METHOD=rapid_scan MESH_PROFILE=treed_full
```

`SAVE_CONFIG` в стартовый G-code добавлять не нужно: mesh и `SHAPER=light` активируются для текущей печати, а сохранение конфигурации остается ручной сервисной операцией.

## Калибровка input shaper

В профиле включен ADXL345 на EBB42 и TreeD-макросы:

```gcode
TREED_SHAPER_CALIBRATE_FULL
TREED_SHAPER_CALIBRATE_LIGHT
```

`TREED_SHAPER_CALIBRATE_FULL` делает `G28`, запускает быстрый полный sweep X/Y на `ACCEL=25000` по умолчанию, сохраняет результат через `TREED_SAVE_CONFIG` и перезапускает Klipper штатным `SAVE_CONFIG`.

`TREED_SHAPER_CALIBRATE_LIGHT` делает `G28`, измеряет узкие диапазоны вокруг сохраненных `shaper_freq_x/y`, применяет результат сразу и не сохраняет конфиг.

Если нужно задать ускорение явно:

```gcode
TREED_SHAPER_CALIBRATE_FULL ACCEL=25000
TREED_SHAPER_CALIBRATE_LIGHT ACCEL=12000
```

PID хотэнда/стола сейчас задается в профильных heater-секциях, потому что Klipper требует `pid_Kp/Ki/Kd` при загрузке `control: pid`. После `PID_CALIBRATE` новые значения нужно перенести в `ebb42_can.cfg` или `bed_heater_dc.cfg`; параметры input shaper остаются в stock `SAVE_CONFIG`-блоке `printer.cfg`.

## Калибровка Eddy

До сохраненной калибровки `PROBE_EDDY_CURRENT_CALIBRATE` команды `PROBE`, `BED_MESH_CALIBRATE`, `START_PRINT` с mesh-контуром и `TREED_Z_PARK_ZERO_EDDY` будут падать с ошибкой `Must calibrate probe_eddy_current first`.

Базовый порядок первичной калибровки:

1. Навести Eddy примерно в центр стола и поставить датчик около 20 мм над поверхностью.
2. Выполнить калибровку drive current:

```gcode
LDC_CALIBRATE_DRIVE_CURRENT CHIP=btt_eddy
TREED_SAVE_CONFIG
```

3. После рестарта выполнить автоматизированную калибровку Eddy через проектный макрос:

```gcode
PROBE_EDDY_CURRENT_CALIBRATE_AUTO CHIP=btt_eddy
```

4. Пройти paper test, выполнить `ACCEPT`, затем сохранить:

```gcode
TREED_SAVE_CONFIG
```

5. После рестарта снова выполнить homing, затем запустить компенсацию температурного дрейфа:

```gcode
G28
SET_IDLE_TIMEOUT TIMEOUT=36000
TEMPERATURE_PROBE_CALIBRATE PROBE=btt_eddy TARGET=56 STEP=4
```

6. Пройти запрошенные paper test шаги, выполнить `ACCEPT`, затем сохранить:

```gcode
TREED_SAVE_CONFIG
```

После этого рабочая проверка Z0:

```gcode
G28
TREED_Z_PARK_ZERO_EDDY
```

`PROBE_EDDY_CURRENT_CALIBRATE_AUTO` сам использует runtime `[force_move]`, ставит Eddy в центр пластины с учетом offset и запускает штатный `PROBE_EDDY_CURRENT_CALIBRATE`. После успешной калибровки не запускать deploy в `TREED_DEPLOY_MODE=clean`, если нужно сохранить autosave-сегмент Klipper; использовать `preserve` или `auto`.

## Сервисные тесты движения

`TREED_XY_MOTION_TEST` — ручной XY stress-test без печати. Макрос сам делает `G28`, поднимается на безопасный Z, гоняет периметр, диагонали, зигзаг, круг, мелкие перемещения вокруг центра и ромбовую восьмерку. Во время активной печати или паузы запуск запрещен.

Базовый безопасный прогон:

```gcode
TREED_XY_MOTION_TEST SPEED=150 ACCEL=3000 ITER=1 Z=20 END_Z=100
```

Рабочий прогон для проверки скорости, ускорений и ремней:

```gcode
TREED_XY_MOTION_TEST SPEED=250 ACCEL=7000 ITER=2 SCV=8 SMALL_STEP=5 SMALL_REPEATS=8 ZIGZAG_STEPS=5 Z=20 END_Z=100
```

Стресс-прогон с кругом и мелкими разворотами:

```gcode
TREED_XY_MOTION_TEST SPEED=350 ACCEL=10000 ITER=3 SCV=9 CIRCLE_RADIUS=70 SMALL_STEP=5 SMALL_REPEATS=12 ZIGZAG_STEPS=8 Z=20 END_Z=120
```

После теста макрос восстанавливает `VELOCITY`, `ACCEL` и `SQUARE_CORNER_VELOCITY` из состояния до запуска. Если нужно вручную вернуть лимиты из `[printer]`, выполнить:

```gcode
TREED_MOTION_LIMITS_DEFAULT
```

## Карта слоев

1. Репозиторий (source of truth)
- `loader/` — pipeline provisioning и проверки.
- `klipper/` — канонические конфиги Klipper.
- `moonraker/` — базовый конфиг Moonraker и компоненты.
- `runtime-scripts/` — runtime-скрипты (например, камера).
- `mainsail/` — тема и UI-ресурсы Mainsail.
- `firmware/` — репозиторные firmware-артефакты.

2. Loader
- entrypoint: `loader/loader.sh`
- шаги: `loader/steps/*.sh`

3. Staging на устройстве
- `${PI_HOME}/treed/klipper`

4. Runtime на устройстве
- `${PI_HOME}/printer_data/config`
- `${PI_HOME}/treed/cam/bin`

5. Сервисы и UI
- `klipper`, `moonraker`, `crowsnest`, `treed-shell`, `KlipperScreen`, `mainsail`
- активный экранный UI по умолчанию: TreeD Shell (`TREED_UI_MODE=ts`, release asset `treed-shell-ui.zip`)
- ручное переключение на Rock Pi:

```bash
sudo treed-ui ts
sudo treed-ui ks
treed-ui status
```

## Документация

- Быстрый install path: `docs/README.md`
- Модель владения конфигами: `docs/config-ownership.md`
- Профиль Klipper V2: `klipper/profiles/treed_v2_corexy_v1/README.md`
