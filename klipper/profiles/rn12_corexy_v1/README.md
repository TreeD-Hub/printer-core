# Профиль `rn12_corexy_v1`

Профиль описывает рабочую конфигурацию Klipper для RN12 CoreXY и раскладывается loader-пайплайном в runtime (`/home/pi/printer_data/config`).

## Состав профиля

Основная include-цепочка задается в `klipper/printer.cfg`:
1. `profiles/rn12_corexy_v1/mcu_rn12.cfg`
2. `profiles/rn12_corexy_v1/ebb42_v1_2_usb.cfg`
3. `profiles/rn12_corexy_v1/printer_base.cfg`
4. `profiles/rn12_corexy_v1/gcode_features.cfg`
5. `profiles/rn12_corexy_v1/probe_eddy_duo.cfg`
6. `profiles/rn12_corexy_v1/steppers.cfg`
7. `profiles/rn12_corexy_v1/bed_heater_dc.cfg`
8. `profiles/rn12_corexy_v1/input_shaper.cfg`
9. `profiles/rn12_corexy_v1/macros.cfg`
10. `profiles/rn12_corexy_v1/ui.cfg`
11. `local_overrides.cfg` — локальный runtime-файл на Pi

Файлы профиля:
- `mcu_rn12.cfg` — основной MCU RN12.
- `ebb42_v1_2_usb.cfg` — toolhead на EBB42 v1.2, экструдер, вентиляторы, onboard ADXL345.
- `probe_eddy_duo.cfg` — `Eddy Duo` как отдельный CAN MCU и обязательный контур Z-probe/bed mesh.
- `steppers.cfg` — кинематика и оси, включая `stepper_z` с `probe:z_virtual_endstop`.
- `printer_base.cfg` — базовые ограничения и общая геометрия.
- `bed_heater_dc.cfg` — стол.
- `input_shaper.cfg` — параметры Input Shaper.
- `gcode_features.cfg` — G-code совместимость.
- `macros*.cfg` — макросный слой.
- `ui.cfg` — UI и сервисные интерфейсы.
- `optional_service_fans.cfg` — необязательные сервисные вентиляторы, не участвуют в базовом контуре.
- `filament_sensor.cfg` — заготовка под датчик филамента, не включен в базовую цепочку.

## Аппаратный контракт

Текущая сборка профиля рассчитана на три MCU:
- RN12 — основной контроллер осей и стола, транспорт `uart`, runtime-таргет `/dev/serial0`.
- EBB42 v1.2 — toolhead MCU, транспорт `usb`, runtime-таргет `/dev/serial/by-id/*stm32g0b1*`.
- Eddy Duo — датчик автоуровня как отдельный CAN MCU, транспорт `canbus`, интерфейс `can0`.

Инварианты:
- физический Z-концевик больше не используется;
- `stepper_z.endstop_pin` обязан быть `probe:z_virtual_endstop`;
- `position_endstop` в секции `stepper_z` отсутствует;
- старые файлы `optional_bed_mesh.cfg` и `optional_screws_tilt_adjust.cfg` удалены и не должны возвращаться в include-цепочку;
- `probe_eddy_duo.cfg` обязателен для рабочего профиля;
- ADXL345 обслуживается только через onboard-конфиг EBB42, без автопереключения на RPi.

## Loader-контракт

Профиль раскладывается стандартными шагами loader:
1. `loader/steps/klipper-sync.sh` синхронизирует `klipper/` в staging (`/home/pi/treed/klipper`).
2. `loader/steps/klipper-profiles.sh` подставляет runtime-идентификаторы транспорта:
   - serial RN12 в `mcu_rn12.cfg`;
   - serial EBB42 в `ebb42_v1_2_usb.cfg`;
   - `canbus_uuid` Eddy Duo в `probe_eddy_duo.cfg`.
3. `loader/steps/klipper-core.sh` раскладывает staging в runtime (`/home/pi/printer_data/config`).

Переменные окружения:
- `TREED_EBB_SERIAL_BY_ID` — опциональный override пути EBB42; если не задан, loader берет единственный `/dev/serial/by-id/*stm32g0b1*`.
- `TREED_EDDY_CANBUS_UUID` — обязательный UUID Eddy Duo при первом деплое; далее loader может использовать уже сохраненное значение из `probe_eddy_duo.cfg`.

В `preserve`-режиме `klipper-core.sh` сохраняет `SAVE_CONFIG`, но вычищает из него legacy-секции старого автоуровня: `bltouch`, `probe`, сохраненные `bed_mesh` и `position_endstop` внутри `[stepper_z]`.

## Eddy Duo

`probe_eddy_duo.cfg` задает обязательный контур автоуровня:
- `[mcu eddy]` — отдельный CAN MCU датчика;
- `[probe_eddy_current btt_eddy]` — основной датчик z-пробинга;
- `[bed_mesh]` — базовая сетка стола;
- `[safe_z_home]` — безопасный Z-home в центре стола;
- `[gcode_macro G28]` и `[gcode_macro SET_Z_FROM_PROBE]` — привязка Z к последнему измерению пробника;
- `[gcode_macro PROBE_EDDY_CURRENT_CALIBRATE_AUTO]` — первичная калибровка без физического концевика.

Что требует ручной калибровки после монтажа:
- `canbus_uuid` в `probe_eddy_duo.cfg`;
- `x_offset` и `y_offset` Eddy Duo под фактический кронштейн;
- `z_offset` после калибровки пробника;
- `mesh_min`/`mesh_max`, если механика или смещения отличаются от базовых значений.

Базовый сценарий ввода в эксплуатацию:
1. задать `TREED_EDDY_CANBUS_UUID` и развернуть профиль;
2. выполнить `RESTART`;
3. выполнить `PROBE_EDDY_CURRENT_CALIBRATE_AUTO`;
4. откалибровать `z_offset`;
5. выполнить `BED_MESH_CALIBRATE METHOD=rapid_scan`;
6. сохранить результат через `TREED_SAVE_CONFIG`.

## ADXL345 и Input Shaper

Для измерения резонансов используется onboard ADXL345 на EBB42 v1.2:
- `ebb42_v1_2_usb.cfg` содержит секции `adxl345` и `resonance_tester`;
- `input_shaper.cfg` включен напрямую в `printer.cfg`;
- старый контур ADXL через Raspberry Pi не используется.

Минимальная проверка после деплоя:
- `ACCELEROMETER_QUERY`
- `MEASURE_AXES_NOISE`
- `TEST_RESONANCES AXIS=X`
- `TEST_RESONANCES AXIS=Y`

## Макросный слой

`macros.cfg` остается агрегатором include-файлов и не должен превращаться в монолит. Точка входа не меняется: `printer.cfg -> macros.cfg`.

Публичные макросы оператора:
- `START_PRINT`
- `END_PRINT`
- `PAUSE`
- `RESUME`
- `CANCEL_PRINT`
- `LOAD_FILAMENT`
- `UNLOAD_FILAMENT`
- `TREED_SAVE_CONFIG`
- `M600`

Рабочие фазы `START_PRINT/PAUSE/RESUME` используют private-макросы и остаются fail-fast при неполном runtime-конфиге.

## Runtime-пути

- staging: `/home/pi/treed/klipper`
- runtime: `/home/pi/printer_data/config`
- runtime-профиль: `/home/pi/printer_data/config/profiles/rn12_corexy_v1`
- локальные override: `/home/pi/printer_data/config/local_overrides.cfg`

## Связанные слои

- `klipper/README.md` — общий слой конфигов Klipper.
- `loader/README.md` — верхнеуровневый пайплайн deploy.
- `loader/steps/README.md` — контракты отдельных шагов loader.
