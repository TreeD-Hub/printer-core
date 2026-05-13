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

## Быстрый запуск (копируй в SSH)

```bash
curl -fsSL https://raw.githubusercontent.com/TreeD-Hub/treed-mainshellOS/treed-v2/bootstrap-pi.sh | bash
```

Loader сам определяет `fresh|update|recover`, выбирает `clean|preserve` и ребутает только после `fresh`.

## Вариации работы перед печатью

Стартовый G-code слайсера должен вызывать `START_PRINT` и передавать температуры первого слоя:

```gcode
START_PRINT BED_TEMP=[bed_temperature_initial_layer_single] EXTRUDER_TEMP=[nozzle_temperature_initial_layer] MESH=load MESH_PROFILE=default
```

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

`SAVE_CONFIG` в стартовый G-code добавлять не нужно: сетка активируется для текущей печати, а сохранение конфигурации остается ручной сервисной операцией.

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
- `/home/pi/treed/klipper`

4. Runtime на устройстве
- `/home/pi/printer_data/config`
- `/home/pi/treed/cam/bin`

5. Сервисы и UI
- `klipper`, `moonraker`, `crowsnest`, `KlipperScreen`, `mainsail`

## Документация

- Быстрый install path: `docs/README.md`
- Модель владения конфигами: `docs/config-ownership.md`
- Профиль Klipper V2: `klipper/profiles/treed_v2_corexy_v1/README.md`
