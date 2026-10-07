# Целевые платы TreeD V2

Штатные Kconfig-файлы для шага `loader/steps/firmware-build.sh`.
До сборки и записи сверьте модель MCU, ревизию платы и bootloader offset
с фактическим оборудованием; имя файла не подтверждает их соответствие.

## Файлы

- `main_octopus_pro_f446_can.config`
  - Octopus Pro V1.0.1 (вариант F446) через CAN PD0/PD1.
- `ebb42_can_stm32g0b1.config`
  - EBB42 CAN (STM32G0B1, CAN PB0/PB1).
- `eddy_can_rp2040.config`
  - Eddy Duo CAN (RP2040, CAN GPIO4/GPIO5, 1M, UF2 artifact).

## Важно

- Если у вашей платы другая ревизия MCU (например F429/H723 или другой Eddy-чип),
  переопределите config-файл через env и перезапустите loader.
- Step `firmware-build` fail-fast проверяет, что итоговая `.config` содержит ожидаемую архитектуру.

Переменные выбора target: [README конфигураций](../README.md).
Артефакты и ручная запись: [README прошивок](../../README.md).
