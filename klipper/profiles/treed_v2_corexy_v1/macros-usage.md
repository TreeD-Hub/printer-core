# Методичка по макросам `treed_v2_corexy_v1`

Документ описывает фактические макросы активного Klipper-профиля TreeD V2 CoreXY V1.
Точка подключения: `klipper/printer.cfg` включает `profiles/treed_v2_corexy_v1/macros.cfg`, а `macros.cfg` подключает модульные `macros_*.cfg`.

## 1. Как вызывать макросы

Макросы вызываются из консоли Mainsail/Fluidd/KlipperScreen или через Moonraker `printer.gcode.script`.

Пример для консоли:

```gcode
START_PRINT BED_TEMP=60 EXTRUDER_TEMP=220
```

Пример для Moonraker:

```json
{"jsonrpc":"2.0","method":"printer.gcode.script","params":{"script":"PAUSE"},"id":1}
```

Правило эксплуатации:
- публичные макросы без префикса `_TREED_` можно вызывать оператору или UI;
- макросы с префиксом `_TREED_` считаются внутренними, если в этой методичке не сказано обратное;
- raw-команды Klipper для live-тюнинга (`M220`, `M221`, `SET_GCODE_OFFSET`, `SET_PRESSURE_ADVANCE` и т.п.) из TreeD Shell не слать напрямую, использовать `TREED_UI_*`.

## 2. Старт и завершение печати

### `START_PRINT`

Основная команда для start-gcode слайсера.

Минимальный вызов:

```gcode
START_PRINT BED_TEMP=60 EXTRUDER_TEMP=220
```

Этот минимальный вызов перед каждой печатью строит новую адаптивную Eddy mesh.

Параметры:

| Параметр | Значение по умолчанию | Что делает |
| --- | --- | --- |
| `BED_TEMP` | нет | Целевая температура стола. Обязательна, должна быть `> 0`. |
| `EXTRUDER_TEMP` | нет | Целевая температура сопла. Обязательна, должна быть выше `min_extrude_temp`. |
| `MESH` | `adaptive` | Необязательное legacy-значение `adaptive`; остальные режимы отклоняются. |
| `MESH_METHOD` | `rapid_scan` | Необязательное legacy-значение `rapid_scan`; остальные методы отклоняются. |
| `ADAPTIVE_MARGIN` | `5` | Отступ adaptive mesh от объектов. |
| `HOTEND_READY_MARGIN` | `3` | Сколько градусов можно не дождаться до цели сопла перед purge. |

Что делает `START_PRINT`:
1. До изменения состояния валидирует параметры и object-метаданные для нативной adaptive mesh.
2. Первой state-changing командой выполняет `BED_MESH_CLEAR`, затем сбрасывает G-code offsets.
3. Включает нагрев стола и preheat сопла, дожидается температуры стола.
4. Выполняет полный `G28` через sensorless X/Y и Eddy Z-home.
5. Строит новую mesh через Eddy в сервисных raw-координатах: `METHOD=rapid_scan ADAPTIVE=1` с отступом `ADAPTIVE_MARGIN`. Klipper сразу активирует результат.
6. Включает print-offset рабочей зоны и паркуется на Z=10 мм в передней полосе.
7. Догревает сопло, выполняет фиксированный `_TREED_LINE_PURGE` и запускает runtime-сессию камеры.

Object labels в G-code и `enable_object_processing` в Moonraker обязательны для adaptive mesh: без polygon-метаданных `START_PRINT` завершится до очистки mesh и прогрева. `MESH=load|none|calibrate`, `MESH_METHOD=scan|automatic|manual`, `MESH_PROFILE`, `MESH_MIN`/`MESH_MAX`, `SHAPER` и `SHAPER_ACCEL` отклоняются. Для сервисного сканирования используйте `TREED_BED_MESH_CALIBRATE_EDDY`. Высота ожидания сопла `park_height` задаётся отдельно от рабочего Z0.

### `END_PRINT`

Основная команда для end-gcode слайсера.

```gcode
END_PRINT
```

Опционально можно отключить сохранение live Z-offset для конкретного завершения:

```gcode
END_PRINT SAVE_Z_OFFSET=0
```

Что делает:
- сначала выключает нагреватели, обдув и останавливает камеру;
- при известной Z захватывает live Z-offset/babystep для Eddy autosave;
- отключает print-offset перед сервисной парковкой;
- при разрешённой экструзии делает retract; при достоверных X/Y/Z — Z-hop и парковку в raw-координатах;
- применяет накопленный Eddy Z-offset, если autosave включен.

При неизвестных или выходящих за пределы координатах парковка пропускается; отключение нагрева от неё не зависит.

## 3. Homing

### `G28`

Штатный `G28` переопределен TreeD-макросом.

```gcode
G28
G28 X
G28 Y
G28 Z
```

Поведение:
- X/Y идут через sensorless homing TMC5160;
- перед первым движением сбрасываются активная mesh и G-code offsets; `G28` оставляет сервисные raw-координаты;
- перед X/Y известная Z поднимается в пределах доступного хода; у верхней границы движение вниз запрещено;
- при неизвестной Z выполняется `TREED_Z_HOME_BOTTOM`: две sensorless-пробы нижнего упора с проверкой хода второй и финальным отходом; ошибка блокирует дальнейший homing;
- перед каждым X/Y завершается очередь движений и выдерживается не менее `2000 мс` без движения для сброса StallGuard; после срабатывания есть отход от упора на `10 мм`;
- Z всегда идет через Eddy-контур `_TREED_EDDY_HOME_Z`;
- отдельный `G28 Z` требует уже homed X/Y; неизвестная Z сначала получает нижнюю опору, затем рабочий Z0 ищет Eddy.

После потери координат выполните полный homing:

```gcode
G28
```

После успешного `G28` для парковки можно выполнить `G1 X10 Y235 F6000` в сервисных raw-координатах. Отдельный `G28 Z` использовать только после успешного `G28 X Y` или полного `G28`. При неизвестной Z и X/Y команда `G28 Z` не заменяет полный homing.

Нижняя опора по умолчанию заблокирована (`enabled: False`): нужны настройка и
[аппаратный допуск](README.md#нижняя-опора-z). `TREED_Z_HOME_BOTTOM` можно вызвать
отдельно на свободном принтере; известная Z пропускается. После `G28 X/Y` нижняя
опора даёт приближённую Z, перед печатью нужен Eddy Z0. При ошибке Z остаётся
неизвестной; при shutdown требуется `FIRMWARE_RESTART`. Слепого fallback нет.

## 4. Пауза, продолжение и отмена

Общий допуск различает простой, подготовку печати, печать, паузу, калибровку и зарезервированный автосъём. UI-перемещения разрешены в простое, сервисная протяжка — в простое или на подтверждённой паузе. Во время подготовки, печати, калибровки и автосъёма ручная протяжка запрещена. После ошибки подготовки или калибровки `CANCEL_PRINT` сбрасывает закрытую фазу.

### `PAUSE`

```gcode
PAUSE
```

Что делает:
- сохраняет текущие цели хотэнда/стола и idle timeout;
- останавливает runtime-снимки камеры;
- делает retract `E-5`, Z-hop и парковку в сервисной raw-зоне;
- снижает хотэнд до `140C`;
- увеличивает idle timeout до `43200` секунд.

Повторный `PAUSE` при активной паузе не выполняет новую парковку, а пишет сообщение `PAUSE уже активна`.
Первый вызов требует активной печати и достоверных X/Y/Z в пределах осей до любых действий парковки.

### `RESUME`

```gcode
RESUME
```

Что делает:
- возвращает цели нагрева;
- дожидается стола и хотэнда;
- делает purge `E50` и wipe в сервисной raw-зоне;
- восстанавливает idle timeout и print-offset;
- вызывает штатный `RESUME_BASE`;
- возвращает камеру, если она работала до паузы.

### `CANCEL_PRINT`

```gcode
CANCEL_PRINT
```

Что делает:
- сначала выключает нагреватели и вызывает штатный `CANCEL_PRINT_BASE`;
- отключает print-offset;
- сбрасывает состояние паузы;
- останавливает камеру;
- при достоверной Z и доступном ходе поднимает стол/голову; ошибка этого движения не отменяет уже выполненную остановку печати.

### `CLEAR_PAUSE`

```gcode
CLEAR_PAUSE
```

Очищает состояние паузы и восстанавливает `idle_timeout`, если пауза была активна.

## 5. Филамент

### `LOAD_FILAMENT`

```gcode
LOAD_FILAMENT
LOAD_FILAMENT LENGTH=120 SPEED=8
```

Параметры:
- `LENGTH` — длина протяжки в мм, по умолчанию `100`;
- `SPEED` — скорость в мм/с, по умолчанию `8`.

Макрос требует прогретое сопло и допускается в простое или на подтверждённой паузе. Если хотэнд ниже `min_extrude_temp`, будет ошибка. На время сервисной протяжки switch/motion датчики филамента временно отключаются и потом возвращаются в прежнее состояние.

### `UNLOAD_FILAMENT`

```gcode
UNLOAD_FILAMENT
UNLOAD_FILAMENT LENGTH=120 SPEED=8
```

Параметры те же, что у `LOAD_FILAMENT`. Направление движения отрицательное, но оператор передает положительную длину.
Во время активной печати команда запрещена; сценарий `M600` сначала ставит печать на паузу и только затем выгружает материал.

## 6. Live-тюнинг из UI

Эти команды являются публичным контрактом TreeD Shell. Они разрешены только во время `printing`/`paused` или когда `pause_resume.is_paused = true`.

| Команда | Пример | Диапазон |
| --- | --- | --- |
| `TREED_UI_SET_SPEED_FACTOR` | `TREED_UI_SET_SPEED_FACTOR PERCENT=120` | `10..300` % |
| `TREED_UI_SET_FLOW_FACTOR` | `TREED_UI_SET_FLOW_FACTOR PERCENT=97` | `50..150` % |
| `TREED_UI_SET_ACCEL` | `TREED_UI_SET_ACCEL ACCEL=12000` | `500..printer.max_accel` |
| `TREED_UI_SET_PRESSURE_ADVANCE` | `TREED_UI_SET_PRESSURE_ADVANCE ADVANCE=0.075` | `0..0.20` |
| `TREED_UI_SET_RETRACTION` | `TREED_UI_SET_RETRACTION RETRACT_LENGTH=0.9` | `0..5.0` мм |
| `TREED_UI_ADJUST_Z_OFFSET` | `TREED_UI_ADJUST_Z_OFFSET DELTA=0.02` | `-0.05..0.05` мм за команду, суммарно `-1.0..1.0` |

После успешной команды UI должен перечитать состояние через Moonraker objects, а не парсить текст `RESPOND`.
Минимальный набор объектов:
- `gcode_move`: `speed_factor`, `extrude_factor`, `homing_origin`;
- `toolhead`: `max_velocity`, `max_accel`;
- `extruder`: `temperature`, `target`, `pressure_advance`;
- `heater_bed`: `temperature`, `target`;
- `firmware_retraction`: `retract_length`;
- `gcode_macro _TREED_UI_TUNE_STATE`: `contract_version`, `applied_babystep`.

Для отображения Z-offset UI читает `gcode_move.homing_origin.z`; счётчик
`applied_babystep` обновляется вместе с ним командами профиля. Повторный homing
сбрасывает оба значения. `END_PRINT` захватывает offset для возможного
сохранения в Eddy probe и затем сбрасывает временные значения; `CANCEL_PRINT`
сбрасывает их без сохранения.

## 7. Bed mesh и Eddy

### `TREED_BED_MESH_CALIBRATE_EDDY`

Публичная Eddy-aware обертка для построения mesh. Штатный `BED_MESH_CALIBRATE` тоже переопределен и маршрутизируется сюда.

Примеры:

```gcode
TREED_BED_MESH_CALIBRATE_EDDY PROFILE=default METHOD=scan
TREED_BED_MESH_CALIBRATE_EDDY PROFILE=treed_adaptive METHOD=scan ADAPTIVE=1 ADAPTIVE_MARGIN=5
TREED_BED_MESH_CALIBRATE_EDDY PROFILE=eddy_test METHOD=rapid_scan MESH_MIN=80,80 MESH_MAX=165,165 PROBE_COUNT=3,3 SCAN_SPEED=60
BED_MESH_CALIBRATE PROFILE=default METHOD=automatic
```

Макрос:
- использует отдельную безопасную Eddy scan area `X10..235 / Y10..210`;
- принимает `MESH_MIN`/`MESH_MAX` внутри этой области и отвергает некорректные координаты до движения;
- не расширяет область движения и печати `X0..245 / Y0..245`;
- чистит старую mesh-трансформацию;
- доhomит только неизвестные оси;
- временно отключает print-offset, если он был включен;
- передает эффективные `MESH_MIN`/`MESH_MAX` в базовый Klipper `BED_MESH_CALIBRATE_BASE`;
- принимает `SCAN_SPEED` только для `METHOD=rapid_scan`; для остальных методов скорость XY берется из `[bed_mesh] speed`.

`TREED_EDDY_BED_MESH_CALIBRATE` также передаёт необязательные `MESH_MIN`/`MESH_MAX` в эту обертку. При `y_offset: -30` штатная область датчика `Y10..210` соответствует перемещению головы `Y40..240`.

Это Eddy service mesh в safe scan area, а не скан всей области печати. Scan area меньше стола, потому что sensing point датчика смещен относительно сопла и не может физически покрыть всю заднюю часть `245x245`.

### `TREED_Z_PARK_ZERO_EDDY`

```gcode
TREED_Z_PARK_ZERO_EDDY
```

Ручной рабочий поиск Z0 через Eddy. Использует тот же контур, что `G28 Z`; требует заранее привязанных X/Y и известной Z.

### `PROBE_EDDY_CURRENT_CALIBRATE_AUTO`

Запускайте только после того, как Z достоверно привязана. При неизвестной Z
макрос должен остановиться; для первого запуска нужен отдельный проверенный
сервисный порядок. Полная последовательность: [`docs/eddy-calibration.md`](../../../docs/eddy-calibration.md).

```gcode
PROBE_EDDY_CURRENT_CALIBRATE_AUTO CHIP=btt_eddy
```

Первичная калибровка Eddy с учетом безопасной scan area и runtime `[force_move]`. Макрос сам требует предварительно достоверной Z-позиции и при неизвестной Z прерывается до своего `G28 X Y`. После проверки Z ставит Eddy в центр `X10..235 / Y10..210` и запускает штатный `PROBE_EDDY_CURRENT_CALIBRATE`.

После интерактивной калибровки сохранять через:

```gcode
TREED_SAVE_CONFIG
```

### `TREED_SCREWS_TILT_CALIBRATE`

```gcode
TREED_SCREWS_TILT_CALIBRATE
```

Выравнивание четырех винтов стола через Eddy. Макрос чистит mesh, выполняет `G28`, ждет 1 секунду и запускает `SCREWS_TILT_CALCULATE`.

### Eddy Z-offset autosave

Команды управления:

```gcode
TREED_EDDY_Z_OFFSET_AUTOSAVE_ENABLE
TREED_EDDY_Z_OFFSET_AUTOSAVE_DISABLE
TREED_EDDY_Z_OFFSET_AUTOSAVE_STATUS
```

Если autosave включен, `END_PRINT` может применить накопленный live Z-offset к Eddy probe через `Z_OFFSET_APPLY_PROBE` и `SAVE_CONFIG`.

Использовать осторожно: это меняет сохраненную калибровку Eddy. Для одноразового завершения без сохранения offset использовать:

```gcode
END_PRINT SAVE_Z_OFFSET=0
```

## 8. Input shaper

### `TREED_SHAPER_CALIBRATE_FULL`

Полная ручная калибровка input shaper с сохранением результата.

```gcode
TREED_SHAPER_CALIBRATE_FULL ACCEL=25000
```

По умолчанию:
- делает `G28`;
- запускает full sweep;
- вызывает `TREED_SAVE_CONFIG`, если `SAVE=1`.

Вариант без сохранения:

```gcode
TREED_SHAPER_CALIBRATE_FULL ACCEL=25000 SAVE=0
```

### `TREED_SHAPER_CALIBRATE_LIGHT`

Легкая калибровка вокруг уже сохраненных `shaper_freq_x/y`, без рестарта.

```gcode
TREED_SHAPER_CALIBRATE_LIGHT ACCEL=12000
```

Требует, чтобы full-калибровка уже была сохранена. Вручную запрещена во время активной печати и паузы.

### `TREED_SHAPER_CALIBRATE`

Базовая команда с явным режимом:

```gcode
TREED_SHAPER_CALIBRATE MODE=light ACCEL=12000 SAVE=0 HOME=1
TREED_SHAPER_CALIBRATE MODE=full ACCEL=25000 SAVE=1 HOME=1
```

Калибровка допускается только в простое и запускается отдельно от `START_PRINT`.

## 9. Фиксированные park/purge

Эти helper-ы скрыты от Fluidd префиксом `_` и вручную обычно не вызываются: `START_PRINT` вызывает их сам после homing и mesh, при включённом print-offset.

### `_TREED_SMART_PARK`

```gcode
_TREED_SMART_PARK
```

Паркует голову в передней полосе print-координат. Требует homed X/Y/Z и включённый print-offset; при нарушении контракта завершится ошибкой до движения.

### `_TREED_LINE_PURGE`

```gcode
_TREED_LINE_PURGE
```

Делает фиксированную purge-линию с клипингом по рабочей области. По умолчанию полоса `X10..50, Y5` должна оставаться свободной от модели. Требует:
- homed X/Y/Z и включённый print-offset;
- `extruder.max_extrude_cross_section >= 5`.

Параметры калибровки хранятся в `_TREED_START_PURGE_CFG`: `park_height`, `purge_height`, `x_inset`, `y_inset`, `purge_amount`, `break_distance`, `flow_rate`.

## 10. Камера

Камера управляется автоматически:
- `START_PRINT` вызывает `_TREED_CAM_START`;
- `END_PRINT` и `CANCEL_PRINT` вызывают `_TREED_CAM_STOP`;
- `PAUSE` останавливает тикер снимков;
- `RESUME` возвращает тикер, если камера работала до паузы.

Runtime-снимки завязаны на Moonraker shell-command `treed_cam_session_start`, `treed_cam_snapshot`, `treed_cam_session_stop`.

## 11. Сервисные команды

### `TREED_SAVE_CONFIG`

```gcode
TREED_SAVE_CONFIG
```

Безопасная обертка `SAVE_CONFIG`. Запрещена во время печати и паузы.

### `TREED_XY_MOTION_TEST`

```gcode
TREED_XY_MOTION_TEST
TREED_XY_MOTION_TEST SPEED=200 ACCEL=5000 ITER=2 Z=20 END_Z=100
```

Сервисный stress-test XY после полного homing.

Основные параметры:
- `SPEED` — скорость XY в мм/с, по умолчанию `200`;
- `ACCEL` — ускорение, по умолчанию `5000`;
- `ITER` — число повторов, по умолчанию `2`;
- `MARGIN` — отступ от краев, по умолчанию `15`;
- `Z` — безопасная высота перед XY-тестом, по умолчанию `20`;
- `END_Z` — финальная Z-позиция после теста, по умолчанию `100`;
- `SCV` — square corner velocity, по умолчанию текущее значение;
- `ZIGZAG_STEPS`, `CIRCLE_RADIUS`, `SMALL_STEP`, `SMALL_REPEATS` — форма тестовой траектории.

Запрещен во время печати и паузы.

### `TREED_MOTION_LIMITS_DEFAULT`

```gcode
TREED_MOTION_LIMITS_DEFAULT
```

Восстанавливает `VELOCITY`, `ACCEL` и `SQUARE_CORNER_VELOCITY` из секции `[printer]`.

## 12. Capability state для TreeD Shell

Эти макросы не выполняют действие, а публикуют state surface для UI:

```gcode
_TREED_SYSTEM_POWER
_TREED_SERVICE_COMMANDS
```

Поля:
- `_TREED_SYSTEM_POWER.enabled = 1` — UI может показывать reboot/shutdown host;
- `_TREED_SERVICE_COMMANDS.enabled = 1` — UI может показывать restart Klipper/Firmware/Moonraker.

Сами reboot/shutdown/restart должны выполняться штатными Moonraker endpoints и только после подтверждения в UI.

## 13. Короткие рабочие сценарии

### Обычная печать из слайсера

```gcode
START_PRINT BED_TEMP=[first_layer_bed_temperature] EXTRUDER_TEMP=[first_layer_temperature]
; печатный G-code
END_PRINT
```

### Ручная загрузка филамента

```gcode
M104 S220
M109 S220
LOAD_FILAMENT LENGTH=120 SPEED=8
```

### Ручная выгрузка филамента

```gcode
M104 S220
M109 S220
UNLOAD_FILAMENT LENGTH=120 SPEED=8
```

### Команды калибровки Eddy после проверки достоверной Z

```gcode
LDC_CALIBRATE_DRIVE_CURRENT CHIP=btt_eddy
TREED_SAVE_CONFIG
PROBE_EDDY_CURRENT_CALIBRATE_AUTO CHIP=btt_eddy
TREED_SAVE_CONFIG
```

Этот короткий список не заменяет полный порядок: перед каждым запуском AUTO
сначала восстановите достоверную Z-позицию и следуйте
[`docs/eddy-calibration.md`](../../../docs/eddy-calibration.md). Интерактивные
шаги `ACCEPT`/paper test выполняются по подсказкам Klipper.

### Полная калибровка input shaper

```gcode
TREED_SHAPER_CALIBRATE_FULL ACCEL=25000
```

### Проверка механики XY

```gcode
TREED_XY_MOTION_TEST SPEED=200 ACCEL=5000 ITER=2 Z=20 END_Z=100
```

## 14. Что не делать

- Не вызывать внутренние `_TREED_*` helper-макросы вручную, кроме явно описанных сервисных команд.
- Не слать из TreeD Shell raw live-tune команды вместо `TREED_UI_*`.
- Не запускать `TREED_SAVE_CONFIG`, shaper-калибровку или XY stress-test во время печати/паузы.
- Не запускать `G28 Z`, пока X/Y не homed.
- Не запускать `START_PRINT`, если в G-code нет object labels с polygon-координатами.
- Не включать Eddy autosave без понимания, что `END_PRINT` может изменить сохраненный probe offset.
