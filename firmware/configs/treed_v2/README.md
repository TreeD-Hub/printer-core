# TreeD V2 Firmware Target Configs

Дефолтные Kconfig-файлы для шага `loader/steps/firmware-build.sh` в ветке `treed-v2`.

## Файлы

- `main_octopus_pro_f446_usb.config`
  - Octopus Pro V1.0.1 (вариант F446) через USB serial.
- `ebb42_can_stm32g0b1.config`
  - EBB42 CAN (STM32G0B1, CAN PB0/PB1).
- `eddy_can_rp2040.config`
  - Eddy Duo CAN (RP2040, CAN GPIO4/GPIO5, 1M, UF2 artifact).

## Важно

- Если у вашей платы другая ревизия MCU (например F429/H723 или другой Eddy-чип),
  переопределите config-файл через env и перезапустите loader.
- Step `firmware-build` fail-fast проверяет, что итоговая `.config` содержит ожидаемую архитектуру.
