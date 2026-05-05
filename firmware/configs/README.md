# Firmware Build Configs

Каталог хранит Kconfig-фрагменты для автоматической сборки прошивок Klipper в loader pipeline.

## Назначение

- зафиксировать воспроизводимые target-конфиги сборки;
- хранить source-of-truth для шага `loader/steps/firmware-build.sh`;
- позволить переопределять конкретные target-файлы через env без правки кода шага.

## Структура

- `treed_v2/` — дефолтные конфиги для контура Rock Pi + Octopus + U2C + EBB + Eddy.

## Контракт

- файлы в этом каталоге не прошиваются автоматически;
- loader только компилирует бинарники и публикует артефакты + manifest/checksum;
- для специфичного железа (другая ревизия MCU/bootloader) используйте env override:
  - `TREED_FW_MAIN_CONFIG`
  - `TREED_FW_EBB_CONFIG`
  - `TREED_FW_EDDY_CONFIG`
