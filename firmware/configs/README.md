# Конфигурации сборки прошивок

Kconfig-фрагменты для сборки прошивок Klipper шагом `firmware-build`.

## Назначение

- зафиксировать воспроизводимые target-конфиги сборки;
- задавать конфигурацию для шага `loader/steps/firmware-build.sh`;
- поддерживать выбор другого target-файла через переменные окружения без правки шага.

## Структура

- [`treed_v2/`](treed_v2/README.md) — штатные конфиги Octopus Pro, EBB42 и Eddy Duo.

## Контракт

- файлы в этом каталоге не прошиваются автоматически;
- loader только компилирует бинарники и публикует артефакты + manifest/checksum;
- для специфичного железа (другая ревизия MCU/bootloader) используйте env override:
  - `TREED_FW_MAIN_CONFIG`
  - `TREED_FW_EBB_CONFIG`
  - `TREED_FW_EDDY_CONFIG`

Пути артефактов и порядок ручной записи: [README прошивок](../README.md).
