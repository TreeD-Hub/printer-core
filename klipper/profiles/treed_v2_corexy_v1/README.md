# Профиль `treed_v2_corexy_v1`

Профиль задает V2-контур Klipper для ветки `treed-v2`:
- host: Rock Pi (Armbian Debian 12);
- CAN: U2C -> Octopus Pro (main MCU, required), EBB42 (required), Eddy Duo (required).

## Include-цепочка

Основная include-цепочка задается в `klipper/printer.cfg`:
1. `profiles/treed_v2_corexy_v1/mcu_main_octopus_can.cfg`
2. `profiles/treed_v2_corexy_v1/ebb42_can.cfg`
3. `profiles/treed_v2_corexy_v1/printer_base.cfg`
4. `profiles/treed_v2_corexy_v1/geometry.cfg`
5. `profiles/treed_v2_corexy_v1/gcode_features.cfg`
6. `profiles/treed_v2_corexy_v1/probe_eddy_duo.cfg`
7. `profiles/treed_v2_corexy_v1/steppers.cfg`
8. `profiles/treed_v2_corexy_v1/macros_homing.cfg`
9. `profiles/treed_v2_corexy_v1/filament_sensor.cfg`
10. `profiles/treed_v2_corexy_v1/bed_heater_dc.cfg`
11. `profiles/treed_v2_corexy_v1/input_shaper.cfg`
12. `profiles/treed_v2_corexy_v1/service_fans.cfg`
13. `profiles/treed_v2_corexy_v1/macros.cfg`
14. `profiles/treed_v2_corexy_v1/ui.cfg`
15. `local_overrides.cfg`

## Контракт loader

Текущий install pipeline:
- `loader/steps/klipper-sync.sh` синхронизирует дерево `klipper/` в staging без правок профиля;
- `loader/steps/klipper-core.sh` раскладывает staging в runtime без post-deploy подстановок в `printer.cfg`/`profiles/*`;
- Eddy является обязательным для этого профиля; `TREED_EDDY_ENABLED=0` не поддерживается как runtime-профиль без Eddy.

## X/Y sensorless (TMC5160 SPI) и Z через активный endstop профиля

Для профиля `treed_v2_corexy_v1` X/Y работают в режиме sensorless homing через `tmc5160_*:virtual_endstop`.
Профиль подключает override `G28`, который:
- для `G28 X`, `G28 Y` и `G28 X Y` вызывает штатный `G28_BASE`, затем делает отход на 10 мм от X-max/Y-max и паузу 1 секунду для сброса stall-флага TMC5160;
- для `G28 Z` переводит голову в центр пластины из `_TREED_GEOMETRY_CFG`, вызывает базовый `G28.1 Z` с `probe:z_virtual_endstop`, затем уточняет Z через `PROBE` и `SET_KINEMATIC_POSITION`;
- если `G28 Z` вызван без готовых X/Y, макрос завершится с явной ошибкой и подсказкой сначала выполнить `G28` или `G28 X Y`.

В текущем Eddy-профиле `stepper_z.endstop_pin = probe:z_virtual_endstop`, поэтому:
- `G28 Z` / кнопка Home Z в UI используют Eddy как обязательный Z-endstop и сразу делают точную PROBE-коррекцию;
- `TREED_Z_PARK_ZERO_EDDY` остается публичным рабочим макросом поиска Z0 через Eddy после `PROBE_EDDY_CURRENT_CALIBRATE`;
- `[force_move] enable_force_move: True` входит в штатный профиль, потому что `SET_KINEMATIC_POSITION` нужен для Eddy Z-home correction;
- `BED_MESH_CALIBRATE` переопределен wrapper-ом и всегда проходит через `TREED_BED_MESH_CALIBRATE_EDDY`.

`START_PRINT` сначала прогревает стол до `BED_TEMP` и делает preheat сопла, затем выполняет рабочий Eddy Z-home, включает print-offset, строит/загружает mesh и только после этого делает `SMART_PARK`.
Так Z0, mesh и парковка фиксируются в тепловом состоянии печати.
`SMART_PARK` паркует голову у стола на Z0 перед финальным нагревом сопла; `LINE_PURGE` после нагрева сначала поднимается на `purge_height`, затем едет к старту purge-линии.

Обязательные аппаратные предпосылки перед запуском loader:
- на X/Y и Z стоят TMC5160/TMC5160T Pro;
- на X/Y и Z корректно заведены SPI-линии и `CS`;
- на X/Y включены DIAG-джамперы в линии endstop;
- X/Y механические концевики не участвуют в логике хоуминга;
- Z-драйвер TMC5160/TMC5160T Pro стоит в слоте `MOTOR2_1`;
- Z DIAG-джампер на `PG10` остается аппаратным резервом для fallback-сценариев;
- рабочий Z0 ищется через Eddy.

## Датчик филамента

В профиль включен BTT Smart Filament Sensor SFS V2.0:
- конфиг: `filament_sensor.cfg`;
- разъем Octopus Pro для switch-канала: `FIL-DET0` / `material_0`;
- сигнал switch-канала: `PG12`, в Klipper указан как `^PG12`;
- разъем Octopus Pro для encoder/motion-канала: `FIL-DET1` / `material_1`;
- сигнал encoder/motion-канала: `PG13`, в Klipper указан как `^PG13`;
- питание датчика: `+5V` и `GND` на штатных filament-разъемах.

SFS V2.0 использует разветвитель: 4-pin коннектор подключается к датчику, два 3-pin коннектора подключаются к плате. Коннектор switch-канала подключить в `FIL-DET0`, encoder/motion-канала — в `FIL-DET1`.

Стартовое `detection_length` для encoder/motion-канала — `3.0`. Если будут ложные срабатывания, увеличивать параметр в `filament_sensor.cfg` шагом по 1 мм.

База пинов (Octopus Pro):
- `stepper_x`: `step_pin=PF13`, `dir_pin=PF12`, `enable_pin=!PF14`, `cs_pin=PC4`, `diag1_pin=^!PG6`;
- `stepper_y`: `step_pin=PG0`, `dir_pin=PG1`, `enable_pin=!PF15`, `cs_pin=PD11`, `diag1_pin=^!PG9`;
- `stepper_z`: `step_pin=PF11`, `dir_pin=!PG3`, `enable_pin=!PG5`, `cs_pin=PC6`, `diag1_pin=^!PG10`;
- общая software-SPI обвязка: `sclk=PA5`, `mosi=PA7`, `miso=PA6`.

Стартовые параметры X/Y:
- `homing_speed: 20`, `homing_retract_dist: 0` (второй проход отключен);
- `run_current: 0.90`, `sense_resistor: 0.075`, `stealthchop_threshold: 0`, `driver_SGT: -64`;
- `hold_current` для X/Y не используется.

## Тюн `driver_SGT` (обязательный после внедрения)

1. Проверить связь с драйверами: `DUMP_TMC STEPPER=stepper_x`, `DUMP_TMC STEPPER=stepper_y`, `DUMP_TMC STEPPER=stepper_z`.
2. По одной оси подобрать диапазон чувствительности через `SET_TMC_FIELD STEPPER=stepper_x FIELD=SGT VALUE=...` и аналогично для Y/Z.
3. Зафиксировать финальные `driver_SGT` в рабочем диапазоне без ложных срабатываний.
4. Критерий приемки: `G28 X` и `G28 Y` делают single touch, отход на 10 мм и паузу 1 секунду; полный `G28` перед `G28 Z` переводит голову в `X122.5 Y122.5`, затем `G28 Z` делает `G28.1 Z` и `PROBE`-коррекцию Eddy; отдельный `G28 Z` без предварительного/встроенного XY homing дает явную ошибку.

## Первичная калибровка Eddy

До сохраненной калибровки `PROBE_EDDY_CURRENT_CALIBRATE` любые `PROBE`, `BED_MESH_CALIBRATE` и `TREED_Z_PARK_ZERO_EDDY` будут падать с `Must calibrate probe_eddy_current first`.

Базовый порядок:
1. Навести датчик примерно в центр стола и около 20 мм над поверхностью.
2. Выполнить `LDC_CALIBRATE_DRIVE_CURRENT CHIP=btt_eddy`, затем `TREED_SAVE_CONFIG`.
3. После рестарта выполнить `PROBE_EDDY_CURRENT_CALIBRATE_AUTO CHIP=btt_eddy`, пройти paper test и `ACCEPT`.
4. Снова выполнить `TREED_SAVE_CONFIG`.
5. Для компенсации thermal drift выполнить `SET_IDLE_TIMEOUT TIMEOUT=36000`, затем `TEMPERATURE_PROBE_CALIBRATE PROBE=btt_eddy TARGET=56 STEP=4`, пройти запрошенные paper test шаги и сохранить через `TREED_SAVE_CONFIG`.
   Если камера/датчик стабильно выходят выше 56C, `TARGET` подбирать по фактической максимальной температуре Eddy.

`eddy_force_move_calibration.cfg` больше не нужен для первичной калибровки: runtime `[force_move]` живет в `probe_eddy_duo.cfg`. Старый include оставлен пустым только для совместимости с локальными конфигами.

После успешной калибровки не запускать deploy в `TREED_DEPLOY_MODE=clean`, если нужно сохранить autosave-сегмент Klipper. Для обычных повторных раскладок использовать `preserve` или `auto` на ветке `treed-v2`.

## Переменные окружения

- `TREED_MAIN_MCU_CANBUS_UUID` — Octopus Pro UUID, default `d372e54bf965`.
- `TREED_CAN_IFACE` — интерфейс CAN, default `can0`.
- `TREED_EBB_CANBUS_UUID` — EBB42 UUID, default `efaf957ab20f`.
- `TREED_EDDY_ENABLED` — legacy-переменная loader; для этого Klipper-профиля должно оставаться `1`.
- `TREED_EDDY_CANBUS_UUID` — Eddy UUID, default `95485b93332a`.
