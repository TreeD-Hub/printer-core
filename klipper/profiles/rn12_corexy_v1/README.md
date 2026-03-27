# Профиль `rn12_corexy_v1` (MKS Robin Nano 1.2)

Профиль описывает рабочую конфигурацию Klipper для RN12 и раскладывается loader-пайплайном в runtime (`/home/pi/printer_data/config`).

## Точка входа и include-цепочка

Точка входа: `klipper/printer.cfg`.

Текущий порядок include:
1. `profiles/rn12_corexy_v1/mcu_rn12.cfg`
2. `profiles/rn12_corexy_v1/ebb42_v1_2_usb.cfg`
3. `profiles/rn12_corexy_v1/printer_base.cfg`
4. `profiles/rn12_corexy_v1/gcode_features.cfg`
5. `profiles/rn12_corexy_v1/steppers.cfg`
6. `profiles/rn12_corexy_v1/bed_heater_dc.cfg`
7. `profiles/rn12_corexy_v1/input_shaper.cfg`
8. `profiles/rn12_corexy_v1/macros.cfg`
9. `profiles/rn12_corexy_v1/ui.cfg`
10. `local_overrides.cfg` (локальный runtime-файл на Pi)

## Аппаратный контракт (текущая сборка)

Экструдер и хотэнд перенесены на EBB42 v1.2 (USB Type-C):
- `full_steps_per_rotation: 200`
- `gear_ratio: 60:8`
- `rotation_distance: 35.8`
- `heater_pin: EBBCan:PB13` (для v1.2, не `PA2`)
- `TREED_EBB_SERIAL_BY_ID` — опциональный override; при пустом значении loader берет единственный путь из `/dev/serial/by-id/*stm32g0b1*` (иначе fail-fast).

ADXL345 (onboard на EBB42 v1.2):
- `cs_pin: EBBCan:PB12`
- `spi_bus: spi2_PB2_PB11_PB10`
- базовый `axes_map: x,y,z` (дальше калибруется по фактическому монтажу)
- значения сверены с `sample-bigtreetech-ebb-canbus-v1.2.cfg` из репозитория BIGTREETECH/EBB

Если меняется экструдер, мотор экструдера или ориентация ADXL, эти параметры должны быть
пересмотрены и обновлены в профиле.

## Макросы: публичный интерфейс и private-слой

`macros.cfg` не содержит логику и остается агрегатором include-файлов.  
Точка входа и контракт не изменены: `printer.cfg -> macros.cfg`.

Порядок модулей внутри `macros.cfg`:
1. `macros_core.cfg`
2. `macros_camera.cfg`
3. `macros_kamp.cfg`
4. `macros_print_flow.cfg`
5. `macros_pause_resume.cfg`
6. `macros_filament.cfg`
7. `macros_utils.cfg`

### Публичные макросы (для оператора)
- `START_PRINT`
- `END_PRINT`
- `PAUSE`
- `RESUME`
- `CANCEL_PRINT`
- `LOAD_FILAMENT`
- `UNLOAD_FILAMENT`
- `TREED_SAVE_CONFIG`
- `M600` (определен в `gcode_features.cfg`)

### Служебные системные
- `CLEAR_PAUSE` — override штатной команды Klipper, оставлен публичным для безопасного восстановления pause-state.
- `SET_IDLE_TIMEOUT` — wrapper штатной команды Klipper, сохраняет runtime timeout для корректного `PAUSE/RESUME/CANCEL_PRINT`.

### Внутренние private-макросы (не для ручного запуска)
- `_TREED_PRINT_DEFAULTS`
- `_TREED_PRINT_AREA_CFG`
- `_TREED_PRINT_OFFSET_ENABLE`
- `_TREED_PRINT_OFFSET_DISABLE`
- `_TREED_PAUSE_PARK_CFG`
- `_TREED_IDLE_TIMEOUT_STATE`
- `_TREED_PAUSE_STATE`
- `_TREED_START_STATE`
- `_TREED_PAUSE_EXEC_STATE`
- `_TREED_RESUME_WIPE_STATE`
- `_TREED_CAM_STATE`
- `_TREED_CAM_TICK` (`[delayed_gcode]`)
- `_TREED_CAM_START`
- `_TREED_CAM_STOP`
- `_TREED_KAMP_REQUIRE_READY`
- `_TREED_START_PREP_STATE`
- `_TREED_START_MACHINE_PREP`
- `_TREED_START_PREHEAT`
- `_TREED_START_WAIT_PREHEAT_NOZZLE`
- `_TREED_START_KAMP_PREP`
- `_TREED_START_FINAL_HEAT`
- `_TREED_START_KAMP_PURGE`
- `_TREED_START_POST_HOOKS`
- `_TREED_PAUSE_PREP_STATE`
- `_TREED_PAUSE_EXEC`
- `_TREED_RESUME_PREP_WIPE`
- `_TREED_RESUME_HEAT_PURGE_WIPE`
- `_TREED_RESUME_FINALIZE`
- `_TREED_RESUME_POST_HOOKS`
- `_TREED_FILAMENT_MOVE`

`START_PRINT`, `PAUSE`, `RESUME` работают как тонкие оркестраторы и вызывают фазовые private-хелперы в фиксированном порядке.
`START_PRINT` использует KAMP (`SMART_PARK` + `LINE_PURGE`) без fallback на legacy prime/wipe.

Для RN12 действует fail-fast контракт: рабочие фазы `START_PRINT/PAUSE/RESUME` требуют наличие секции `[heater_bed]`.
Если секция отсутствует (поврежденный/неполный runtime-конфиг), макросы аварийно завершаются с ошибкой.

### Временные алиасы совместимости (deprecated)
| Старое имя | Новое имя | План удаления |
|---|---|---|
| `TREED_CAM_START` | `_TREED_CAM_START` | после `dev` + ближайший релиз в `main` |
| `TREED_CAM_STOP` | `_TREED_CAM_STOP` | после `dev` + ближайший релиз в `main` |

Все алиасы в таблице считаются системными и не предназначены для прямого использования оператором.

## Политика `TREED_SAVE_CONFIG`

`TREED_SAVE_CONFIG` блокируется только в двух случаях:
- идет активная печать (`print_stats.state == printing`);
- активна пауза (`pause_resume.is_paused == 1`).

В остальных состояниях (`ready/idle/complete/cancelled` и т.д.) команда разрешена.

## G-code совместимость

`gcode_features.cfg` отвечает за совместимость слайсерного G-code:
- `G2/G3` (`[gcode_arcs]`)
- `G10/G11` (`[firmware_retraction]`)
- `M486` (`[exclude_object]`)
- `M600` (обертка под текущую логику `PAUSE` + `UNLOAD_FILAMENT`)

## Контракт со слайсером

Профиль использует две системы координат:
- `print coords`: координаты слайсера (операторская модель).
- `raw coords`: физические координаты механики.

Текущий контракт зон в `raw coords`:
- сервис/перемещения: `Y=0..64`
- печать: `Y=65..245` (длина 180)

Трансляция `print coords -> raw coords` выполняется макросным `SET_GCODE_OFFSET`:
- включение: `_TREED_PRINT_OFFSET_ENABLE`
- отключение: `_TREED_PRINT_OFFSET_DISABLE`

Для `PAUSE/RESUME` действует инвариант:
- `RESUME_BASE` вызывается только после восстановления того же offset-state,
  который был сохранен на `PAUSE_BASE`.
- `PAUSE` паркует голову в raw `X5 Y5` (fallback: raw `X5 Y30`).
- purge/wipe в `RESUME` выполняются только в raw сервисной зоне (`Y<=64`).

Ожидаемая настройка слайсера:
- origin: `X=0`, `Y=0`
- размер стола: `245 x 180`
- стартовый G-code: `START_PRINT EXTRUDER_TEMP=<temp> BED_TEMP=<temp>`
- slicer обязан включать object labels (`exclude_object`) для KAMP
- legacy-параметры `PRIME_*` больше не поддерживаются

## Локальные override

- Шаблон в репозитории: `klipper/local_overrides.example.cfg`
- Runtime-файл на устройстве: `local_overrides.cfg`
- `local_overrides.cfg` не коммитится и считается локальным source-of-truth для конкретного экземпляра принтера

## ADXL345 / Input Shaper (обязательный контур через EBB42)

Для измерения резонансов через onboard ADXL345 на EBB42:

0. Базовый путь (через loader, без ручной правки runtime-файлов):
- ADXL (EBB) описан в `profiles/rn12_corexy_v1/ebb42_v1_2_usb.cfg`;
- `input_shaper.cfg` включен напрямую в `klipper/printer.cfg`.

1. Loader не добавляет ADXL/Input Shaper include в `local_overrides.cfg`.
- `local_overrides.cfg` используется только для локальных пользовательских override;
- ADXL-контур обслуживается только через onboard-конфиг EBB42.
2. Проверить связь с акселерометром после `RESTART`:
- `ACCELEROMETER_QUERY`
- `MEASURE_AXES_NOISE`

3. Короткая проверка после монтажа/поворота ADXL:
- выполнить `ACCELEROMETER_QUERY` в покое;
- выполнить малые перемещения `G91`, `G1 X10`, `G1 Y10` (без движения Z);
- убедиться, что отклик соответствует текущему `axes_map`.

4. Калибровка:
- `TEST_RESONANCES AXIS=X`
- `TEST_RESONANCES AXIS=Y`
- или `SHAPER_CALIBRATE`

## Как это раскладывает loader

1. `loader/steps/klipper-sync.sh` синхронизирует дерево `klipper/` в staging (`/home/pi/treed/klipper`)
2. `loader/steps/klipper-profiles.sh` подставляет актуальный transport/serial в `mcu_rn12.cfg`
   и опциональный override `TREED_EBB_SERIAL_BY_ID` в `ebb42_v1_2_usb.cfg`
3. `loader/steps/klipper-core.sh` раскладывает staging в runtime (`/home/pi/printer_data/config`)

## KAMP Integration

В профиле используется vendored snapshot KAMP:
- `profiles/rn12_corexy_v1/kamp/KAMP_Settings.cfg`
- `profiles/rn12_corexy_v1/kamp/Smart_Park.cfg`
- `profiles/rn12_corexy_v1/kamp/Line_Purge.cfg`

Интеграционный слой:
- `profiles/rn12_corexy_v1/macros_kamp.cfg`

Инварианты интеграции:
- KAMP вызывается только после `_TREED_PRINT_OFFSET_ENABLE`.
- `START_PRINT` выполняет fail-fast, если нет object-метаданных или KAMP-макросов.
- fallback на старый координатный prime/wipe отсутствует.
- Для Moonraker обязателен `enable_object_processing: True`.
