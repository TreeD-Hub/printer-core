# Печать и Eddy mesh

Инструкция относится к активному профилю `treed_v2_corexy_v1`. Реализация
`START_PRINT` находится в `klipper/profiles/treed_v2_corexy_v1/macros_print_flow.cfg`.

## Start G-code слайсера

Передавайте температуры первого слоя:

```gcode
START_PRINT BED_TEMP=[bed_temperature_initial_layer_single] EXTRUDER_TEMP=[nozzle_temperature_initial_layer]
```

Не передавайте режим сохранённой mesh: перед каждой печатью профиль очищает
предыдущую mesh, выполняет Eddy Z-home и строит новую adaptive mesh через
`rapid_scan`. Сохранённые mesh-профили для обычной печати не загружаются.

Калибровка input shaper выполняется отдельно командой `TREED_SHAPER_CALIBRATE`.
Параметры `SHAPER` и `SHAPER_ACCEL` в `START_PRINT` отклоняются. Параметры `MESH=load`, `MESH=calibrate`,
`MESH_METHOD=scan|automatic|manual`, `MESH_PROFILE` и `MESH_MIN`/`MESH_MAX`
для `START_PRINT` не поддерживаются. Legacy-параметры `MESH=adaptive` и
`MESH_METHOD=rapid_scan` допустимы, но обычно не нужны.

## Требования к G-code и Moonraker

`START_PRINT` проверяет object-метаданные до очистки mesh и прогрева. В G-code
должны быть object labels с polygon-координатами, а в Moonraker включён
`enable_object_processing`. Без них макрос остановится до начала подготовки.

## Что делает `START_PRINT`

1. Проверяет параметры температур и данные объектов.
2. Очищает прошлую mesh и G-code offsets.
3. Начинает прогрев стола и preheat сопла.
4. Выполняет полный `G28` через sensorless X/Y и Eddy Z-home.
5. Строит новую Eddy mesh методом `rapid_scan` вокруг объектов с adaptive margin.
6. Включает print-offset, паркуется в передней полосе, догревает сопло и проводит фиксированную purge-линию.

Параметры `ADAPTIVE_MARGIN` и `HOTEND_READY_MARGIN` можно задавать в вызове;
значения по умолчанию и полный контракт перечислены в
[`macros-usage.md`](../klipper/profiles/treed_v2_corexy_v1/macros-usage.md#2-start-и-завершение-печати).

## Сервисная mesh

Для отдельного ручного сканирования используется публичная команда:

```gcode
TREED_BED_MESH_CALIBRATE_EDDY PROFILE=default METHOD=scan
```

Обёртка ограничивает sensing point безопасной Eddy scan area `X10..235 / Y10..210`.
Это не полный скан области печати `X0..245 / Y0..245`: смещение датчика не
позволяет покрыть заднюю часть стола. Допустимые методы и параметры описаны в
[`macros-usage.md`](../klipper/profiles/treed_v2_corexy_v1/macros-usage.md#7-bed-mesh-и-eddy).

Не добавляйте `SAVE_CONFIG` в start G-code. Автоматическая mesh применяется к
текущей печати; сохранение конфигурации — отдельная сервисная операция.
