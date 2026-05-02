# Профиль `treed_v2_corexy_v1`

Профиль задает V2-контур Klipper для ветки `treed-v2`:
- host: Rock Pi (Armbian Debian 12);
- main MCU: Octopus Pro по USB serial;
- CAN: U2C -> EBB42 (required), Eddy Duo (optional).

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
- управляет optional include Eddy в `printer.cfg`;
- при `TREED_EDDY_ENABLED=1` подставляет `canbus_uuid` и `canbus_interface` Eddy в `probe_eddy_duo_optional.cfg`.

## Переменные окружения

- `TREED_MAIN_MCU_SERIAL_BY_ID` — optional override для main MCU (`/dev/serial/by-id/*`).
- `TREED_CAN_IFACE` — интерфейс CAN, default `can0`.
- `TREED_EBB_CANBUS_UUID` — required CAN UUID EBB42.
- `TREED_EDDY_ENABLED` — `0|1`, default `0`.
- `TREED_EDDY_CANBUS_UUID` — required только если `TREED_EDDY_ENABLED=1`.

## Пины и калибровки на этапе шага 1

- В профиле сохранены шаблонные значения из текущей базы.
- Финальная карта пинов Octopus Pro, термисторы, offsets и калибровки не утверждаются в шаге 1.
