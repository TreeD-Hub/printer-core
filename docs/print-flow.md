# Печать и Eddy mesh

Инструкция относится к активному профилю `treed_v2_corexy_v1`. Реализация
`START_PRINT` находится в `klipper/profiles/treed_v2_corexy_v1/macros_print_flow.cfg`.

## Start G-code слайсера

Передавайте температуры первого слоя:

```gcode
START_PRINT BED_TEMP=[bed_temperature_initial_layer_single] EXTRUDER_TEMP=[nozzle_temperature_initial_layer]
```

Перед каждой печатью профиль очищает предыдущую mesh и выполняет Eddy Z-home.
По умолчанию (`MESH=1`) строится новая adaptive mesh через `rapid_scan`.
`MESH=0` пропускает сканирование; сохранённые mesh-профили не загружаются.

Калибровка input shaper выполняется отдельно командой `TREED_SHAPER_CALIBRATE`.
Параметры `SHAPER`, `SHAPER_ACCEL`, `MESH_METHOD`, `MESH_PROFILE`,
`MESH_MIN` и `MESH_MAX` в `START_PRINT` отклоняются. Включённое сканирование
использует штатный метод `adaptive rapid_scan`.

## Требования к G-code и Moonraker

При `MESH=1` команда `START_PRINT` проверяет object-метаданные до очистки mesh
и прогрева. В G-code должны быть object labels с polygon-координатами, а в
Moonraker включён `enable_object_processing`. Без них макрос остановится до
начала подготовки. При `MESH=0` object-метаданные не требуются.

## Что делает `START_PRINT`

1. Проверяет параметры температур и данные объектов.
2. Очищает прошлую mesh и G-code offsets.
3. Включает нагрев стола до `BED_TEMP`, а сопла до 175 °C, не ожидая температур.
4. Выполняет полный `G28` через sensorless X/Y и Eddy Z-home, на Z1 переезжает к скребку `X40 Y257`.
5. У скребка дожидается 175 °C, выполняет пять циклов `X100 Z0,5 → X40 Z0,15` с половиной текущих скорости и ускорения XY, восстанавливает ускорение и устанавливает Z1.
6. Ждёт стол у скребка, затем повторяет `G28 Z` с Eddy в центре.
7. При `MESH=1` строит новую Eddy mesh методом `rapid_scan` вокруг объектов с adaptive margin.
8. Синхронизирует область печати с полным ходом осей без XY-смещения, паркуется у минимального Y, догревает сопло и проводит одну purge-линию вдоль X длиной в половину ширины стола.

Параметры `ADAPTIVE_MARGIN` и `HOTEND_READY_MARGIN` можно задавать в вызове;
значения по умолчанию и полный контракт перечислены в
[`macros-usage.md`](../klipper/profiles/treed_v2_corexy_v1/macros-usage.md#2-start-и-завершение-печати).

## Сервисная mesh

Для отдельного ручного сканирования используется публичная команда:

```gcode
TREED_BED_MESH_CALIBRATE_EDDY PROFILE=default METHOD=scan
```

Обёртка ограничивает sensing point безопасной Eddy scan area `X25..225 / Y25..222`.
Это не полный скан области печати `X0..250 / Y0..257`: смещение датчика не
позволяет покрыть заднюю часть стола. Допустимые методы и параметры описаны в
[`macros-usage.md`](../klipper/profiles/treed_v2_corexy_v1/macros-usage.md#7-bed-mesh-и-eddy).

Не добавляйте `SAVE_CONFIG` в start G-code. Автоматическая mesh применяется к
текущей печати; сохранение конфигурации — отдельная сервисная операция.
