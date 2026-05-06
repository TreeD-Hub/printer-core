# Профиль `treed_v2_corexy_v1`

Профиль задает V2-контур Klipper для ветки `treed-v2`:
- host: Rock Pi (Armbian Debian 12);
- main MCU: Octopus Pro по USB serial;
- CAN: U2C -> EBB42 (required), Eddy Duo (enabled by default).

## Include-цепочка

Основная include-цепочка задается в `klipper/printer.cfg`:
1. `profiles/treed_v2_corexy_v1/mcu_main_octopus_usb.cfg`
2. `profiles/treed_v2_corexy_v1/ebb42_can.cfg`
3. `profiles/treed_v2_corexy_v1/printer_base.cfg`
4. `profiles/treed_v2_corexy_v1/gcode_features.cfg`
5. `profiles/treed_v2_corexy_v1/steppers.cfg`
6. `profiles/treed_v2_corexy_v1/bed_heater_dc.cfg`
7. `profiles/treed_v2_corexy_v1/input_shaper.cfg`
8. `profiles/treed_v2_corexy_v1/macros.cfg`
9. `profiles/treed_v2_corexy_v1/ui.cfg`
10. `local_overrides.cfg`

Optional include:
- `profiles/treed_v2_corexy_v1/probe_eddy_duo_optional.cfg` включается loader-шагом `klipper-profiles.sh` только при `TREED_EDDY_ENABLED=1`.

## Контракт loader

Шаг `loader/steps/klipper-profiles.sh`:
- подставляет `serial` main MCU в `mcu_main_octopus_usb.cfg`;
- подставляет `canbus_uuid` и `canbus_interface` EBB в `ebb42_can.cfg`;
- управляет include Eddy в `printer.cfg` (по умолчанию включен);
- при `TREED_EDDY_ENABLED=1` подставляет `canbus_uuid` и `canbus_interface` Eddy в `probe_eddy_duo_optional.cfg`;
- при `TREED_EDDY_ENABLED=1` переводит `stepper_z.endstop_pin` на `probe:z_virtual_endstop` и убирает `position_endstop`;
- при `TREED_EDDY_ENABLED=0` возвращает physical Z endstop из `TREED_Z_ENDSTOP_PIN` и `TREED_Z_POSITION_ENDSTOP`.

## X/Y sensorless (TMC5160 SPI) и Z (TMC5160 SPI)

Для профиля `treed_v2_corexy_v1` X/Y работают в режиме sensorless homing через `tmc5160_*:virtual_endstop`.
Z работает через Eddy probe (`probe:z_virtual_endstop`) и управляется TMC5160 на слоте `MOTOR2_1`.

Обязательные аппаратные предпосылки перед запуском loader:
- на X/Y и Z стоят TMC5160/TMC5160T Pro;
- на X/Y и Z корректно заведены SPI-линии и `CS`;
- на X/Y включены DIAG-джамперы в линии endstop;
- X/Y механические концевики не участвуют в логике хоуминга;
- Z-драйвер TMC5160/TMC5160T Pro стоит в слоте `MOTOR2_1`;
- Z-homing идет через Eddy probe (`probe:z_virtual_endstop`).

База пинов (Octopus Pro):
- `stepper_x`: `step_pin=PF13`, `dir_pin=PF12`, `enable_pin=!PF14`, `cs_pin=PC4`, `diag1_pin=^!PG6`;
- `stepper_y`: `step_pin=PG0`, `dir_pin=PG1`, `enable_pin=!PF15`, `cs_pin=PD11`, `diag1_pin=^!PG9`;
- `stepper_z`: `step_pin=PF11`, `dir_pin=!PG3`, `enable_pin=!PG5`, `cs_pin=PC6`;
- общая software-SPI обвязка: `sclk=PA5`, `mosi=PA7`, `miso=PA6`.

Стартовые параметры X/Y:
- `homing_speed: 20`, `homing_retract_dist: 0` (второй проход отключен);
- `run_current: 0.90`, `sense_resistor: 0.075`, `stealthchop_threshold: 0`, `driver_SGT: -64`;
- `hold_current` для X/Y не используется.

## Тюн `driver_SGT` (обязательный после внедрения)

1. Проверить связь с драйверами: `DUMP_TMC STEPPER=stepper_x`, `DUMP_TMC STEPPER=stepper_y`, `DUMP_TMC STEPPER=stepper_z`.
2. По одной оси подобрать диапазон чувствительности через `SET_TMC_FIELD STEPPER=stepper_x FIELD=SGT VALUE=...` и аналогично для Y.
3. Зафиксировать финальные `driver_SGT` в рабочем диапазоне без ложных срабатываний.
4. Критерий приемки: `G28 X` и `G28 Y` с single touch, без ложных срабатываний; затем `G28 Z` через Eddy probe.

## Переменные окружения

- `TREED_MAIN_MCU_SERIAL_BY_ID` — Octopus Pro serial, default `/dev/serial/by-id/usb-Klipper_stm32f446xx_3B0027000D50535556323420-if00`.
- `TREED_CAN_IFACE` — интерфейс CAN, default `can0`.
- `TREED_EBB_CANBUS_UUID` — EBB42 UUID, default `efaf957ab20f`.
- `TREED_EDDY_ENABLED` — `0|1`, default `1`.
- `TREED_EDDY_CANBUS_UUID` — Eddy UUID, default `95485b93332a`.
- `TREED_Z_ENDSTOP_PIN` — physical Z endstop when Eddy is disabled, default `PG10`.
- `TREED_Z_POSITION_ENDSTOP` — Z endstop coordinate when Eddy is disabled, default `0.5`.
