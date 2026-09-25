# Профиль `treed_v2_corexy_v1`

Профиль задает V2-контур Klipper для ветки `treed-v2`:
- host: Rock Pi (Armbian Debian 12);
- CAN: U2C -> Octopus Pro (main MCU, required), EBB42 (required), Eddy Duo (required).

## Include-цепочка

Основная include-цепочка задается в `klipper/printer.cfg`:
1. `profiles/treed_v2_corexy_v1/mcu_main_octopus_can.cfg`
2. `profiles/treed_v2_corexy_v1/ebb42_can.cfg`
3. `profiles/treed_v2_corexy_v1/printer_base.cfg`
4. `profiles/treed_v2_corexy_v1/geometry.cfg`
5. `profiles/treed_v2_corexy_v1/gcode_features.cfg`
6. `profiles/treed_v2_corexy_v1/probe_eddy_duo.cfg`
7. `profiles/treed_v2_corexy_v1/steppers.cfg`
8. `profiles/treed_v2_corexy_v1/z_recovery.cfg`
9. `profiles/treed_v2_corexy_v1/motion_guard.cfg`
10. `profiles/treed_v2_corexy_v1/macros_homing.cfg`
11. `profiles/treed_v2_corexy_v1/filament_sensor.cfg`
12. `filament_motion_runtime.cfg`
13. `profiles/treed_v2_corexy_v1/bed_heater_dc.cfg`
14. `profiles/treed_v2_corexy_v1/input_shaper.cfg`
15. `profiles/treed_v2_corexy_v1/service_fans.cfg`
16. `profiles/treed_v2_corexy_v1/macros.cfg`
17. `profiles/treed_v2_corexy_v1/ui.cfg`
18. `local_overrides.cfg`

`macros.cfg` дополнительно подключает:
- `macros_ui_contract.cfg` как versioned device handshake для TreeD Shell;
- `macros_input_shaper.cfg` содержит отдельную сервисную калибровку; `macros_start_purge.cfg` — фиксированные park/purge для `START_PRINT`;
- `macros_ui_tune.cfg` как публичный runtime tune контракт для TreeD Shell;
- `macros_ui_motion.cfg` как safety-boundary для относительного перемещения осей из TreeD Shell.

`macros_core.cfg` также объявляет capability state macro для системных действий UI:
- `_TREED_SYSTEM_POWER.enabled = 1` — runtime разрешает UI показывать reboot/shutdown host;
- `_TREED_SERVICE_COMMANDS.enabled = 1` — runtime разрешает UI показывать restart Klipper/Firmware/Moonraker.

Эти macro не выполняют действие сами. Они только публикуют state surface для Moonraker object query; destructive команды остаются штатными Moonraker endpoints и должны вызываться только после ручного подтверждения в UI.

Контракт UI live-тюнинга описан в `ui-runtime-tune-contract.md`: публичные `TREED_UI_*` команды, диапазоны, ошибки и Moonraker state surface.

Методичка по ручному и UI-использованию макросов профиля: `macros-usage.md`.

## Контракт loader

Текущий install pipeline:
- `loader/steps/klipper-sync.sh` синхронизирует дерево `klipper/` в staging без правок профиля;
- `loader/steps/klipper-core.sh` раскладывает staging в runtime без post-deploy подстановок в `printer.cfg`/`profiles/*`;
- Eddy является обязательным для этого профиля; `TREED_EDDY_ENABLED=0` не поддерживается как runtime-профиль без Eddy.

## X/Y sensorless (TMC5160 SPI) и Z через активный endstop профиля

Отдельный экспериментальный исполнитель подбора SGT включается явно:
[подключение, команда и ограничения](../../../docs/sgt-calibration.md).
Штатный G28 его не вызывает. Параметры двигателей остаются в `steppers.cfg`.

Для профиля `treed_v2_corexy_v1` X/Y работают в режиме sensorless homing через `tmc5160_*:virtual_endstop`.
Профиль подключает override `G28`, который:
- перед каждым `G28.1 X/Y` ждёт завершения движений и не менее 2 секунд без движения для сброса stall-флага TMC5160, затем делает отход на 10 мм от X-min/Y-max; ток и SGT при homing не меняет;
- до первого движения очищает активную mesh и G-code offsets, оставляя сервисные raw-координаты; старый print-offset не восстанавливается после homing;
- перед X/Y поднимает известную Z не выше `z_max - 1`; если Z уже у верхней границы, не двигает её вниз;
- при неизвестной Z вызывает `TREED_Z_HOME_BOTTOM`: один ограниченный поиск нижнего упора и безопасный отход; ошибка останавливает G28 до X/Y;
- для `G28 Z` переводит голову в центр пластины из `_TREED_GEOMETRY_CFG`, вызывает базовый `G28.1 Z` с `probe:z_virtual_endstop`, затем уточняет Z через `PROBE` и `SET_KINEMATIC_POSITION`;
- отдельный `G28 Z` требует готовые X/Y и при неизвестной Z сначала получает нижнюю опору; прямой `_TREED_EDDY_HOME_Z` также защищает этот путь.

После перезапуска или отключения моторов полный `G28` использует нижнюю опору, только если recovery допущен в конфиге. При уже известной Z нижний упор не используется. После `G28 X/Y` нижняя опора даёт приближённую Z для сервисных движений; рабочий Z0 и первый слой требуют полного `G28` или последующего `G28 Z` через Eddy.

Raw-координаты профиля и область печати остаются `X0..245 / Y0..245`.
Стол и print area имеют размер `245x245`, без расширения механики за `Y245`.
Eddy scan area меньше области печати: текущий штатный сервисный mesh сканирует `X10..235 / Y10..210`, потому что sensing point датчика смещен относительно сопла и физически не покрывает всю заднюю часть стола.

В текущем Eddy-профиле `stepper_z.endstop_pin = probe:z_virtual_endstop`, поэтому:
- `G28 Z` / кнопка Home Z в UI используют Eddy как обязательный Z-endstop и сразу делают точную PROBE-коррекцию;
- `TREED_Z_PARK_ZERO_EDDY` остается публичным рабочим макросом поиска Z0 через Eddy после `PROBE_EDDY_CURRENT_CALIBRATE`;
- `[force_move] enable_force_move: True` входит в штатный профиль, потому что `SET_KINEMATIC_POSITION` нужен для Eddy Z-home correction;
- `BED_MESH_CALIBRATE` переопределен wrapper-ом и всегда проходит через `TREED_BED_MESH_CALIBRATE_EDDY`, который строит Eddy service mesh внутри safe scan area, а не скан всей области печати.

`START_PRINT` сначала проверяет параметры и object-метаданные для native adaptive mesh. Затем очищает старую mesh и offsets, прогревает стол, делает Eddy Z-home и строит новую mesh через `METHOD=rapid_scan ADAPTIVE=1 ADAPTIVE_MARGIN=5`. После этого включает print-offset и паркуется в передней полосе. Сервисная калибровка input shaper запускается отдельно. Сохранённые mesh-профили в обычной печати не загружаются; сервисные методы доступны через `TREED_BED_MESH_CALIBRATE_EDDY`.
Так Z0, mesh и парковка фиксируются в тепловом состоянии печати.
`_TREED_SMART_PARK` паркует голову на Z=10 мм перед финальным нагревом сопла; эта высота ожидания не определяет рабочий Z0.
Финальный нагрев задает `EXTRUDER_TEMP`, но ждет только нижнюю готовность `EXTRUDER_TEMP - HOTEND_READY_MARGIN` (`3C` по умолчанию), поэтому штатный overshoot выше цели не блокирует старт purge/первого слоя.
`_TREED_LINE_PURGE` после готовности сопла безопасно перемещается к фиксированной линии в передней полосе `X10..50, Y5` по умолчанию. Эта полоса должна оставаться свободной от модели.

Обязательные аппаратные предпосылки перед запуском loader:
- на X/Y и Z стоят TMC5160/TMC5160T Pro;
- на X/Y и Z корректно заведены SPI-линии и `CS`;
- на X/Y включены DIAG-джамперы в линии endstop;
- X/Y механические концевики не участвуют в логике хоуминга;
- Z-драйвер TMC5160/TMC5160T Pro стоит в слоте `MOTOR2_1`;
- для нижней опоры Z DIAG-джампер должен подключать драйвер к `PG10`;
- рабочий Z0 ищется через Eddy.

## Датчик филамента

В профиль включен BTT Smart Filament Sensor SFS V2.0:
- конфиг: `filament_sensor.cfg`;
- текущий deployed switch-канал: `PG13`;
- текущий deployed encoder/motion-канал: `PG12`;
- питание датчика: `+5V` и `GND` на штатных filament-разъемах.

SFS V2.0 использует разветвитель: 4-pin коннектор подключается к датчику, два 3-pin коннектора подключаются к плате. Перед изменением pin mapping нужно сверить маркировку обоих 3-pin коннекторов на конкретном принтере.

`filament_motion_runtime.cfg` создаётся со средней чувствительностью (`15.0` мм), меняется только через host API и сохраняется loader-ом в `preserve`-режиме.

## Сервисные вентиляторы и подсветка

Подключения Octopus Pro определены в `service_fans.cfg`:
- `FAN2` / `PD12` — 24V-вентилятор обдува драйверов; перемычка питания `V_VDC`;
- `FAN3` / `PD13` — 5V-вентилятор охлаждения Rock Pi; перемычка питания `5VDC`;
- `FAN5` / `J55` / `PD15` — 24V-подсветка камеры; это правый управляемый FAN-разъем, перемычка питания должна стоять в положении `V_VDC`.

Подсветку подключать к `FAN5/J55` с соблюдением полярности: плюс к `V+`, минус к управляемому выходу `-`. Не использовать соседние постоянные 24V-разъемы: они не управляются Klipper.

Управление подсветкой:

```gcode
LIGHT_ON
LIGHT_OFF
```

По умолчанию и при аварийном завершении Klipper подсветка выключена (`value: 0`, `shutdown_value: 0`).

Распиновка моторов, SPI и DIAG, а также параметры осей и драйверов заданы в [`steppers.cfg`](steppers.cfg). X/Y используют DIAG для sensorless homing, Z — Eddy как штатный endstop, а Z DIAG — для отдельно допускаемой нижней опоры.

Для CoreXY X-знак развернут не одиночной инверсией `dir_pin`, а парной перестановкой MOTOR0/MOTOR1 с инверсией обоих направлений.
Это сохраняет направление Y и переводит `X0` в левый край.

Рабочие скорости homing, токи и чувствительность sensorless настраиваются в `steppers.cfg`; recovery использует отдельные параметры ниже. Скорость подбирают по стабильности и надёжности homing; `driver_SGT` — по надёжному обнаружению упора без ложных срабатываний; `run_current` — по достаточному усилию при приемлемом нагреве мотора и драйвера. После изменения параметры проверяют на принтере.

### Нижняя опора Z

`z_recovery.cfg` подключает `[treed_z_recovery]`, а `runtime-bootstrap.sh`
доставляет `klipper-host/treed_z_recovery.py` в `${PI_HOME}/klipper/klippy/extras`
(или `TREED_KLIPPER_SRC_DIR/klippy/extras`). Обновлять нужно и extra, и конфиг.
Штатный `stepper_z.endstop_pin` остаётся `probe:z_virtual_endstop`.
Отдельный `tmc5160_stepper_z:virtual_endstop` управляет только recovery.

При неизвестной Z контур запускается штатно, без флага `enabled`.
Оператор проверяет направление `+Z = стол вниз`, подключение DIAG к PG10,
свободный ход до крышки и пригодность механики для контакта. Параметры
SGT/тока можно уточнять в runtime `local_overrides.cfg`; прежний `enabled`
из него нужно удалить. Этот файл сохраняется в режиме loader `preserve`.
Аппаратная приёмка StallGuard пока не выполнена.

| Параметр | Кандидат | Назначение |
| --- | --- | --- |
| `sgt` | 3 | Чувствительность только recovery, диапазон -64..63. |
| `current` | 0.9 А | Run и hold ток на время проб; подобрать под нагрузку Z. |
| `speed` | 5 мм/с | Скорость поиска и отхода. |
| `accel` | 100 мм/с² | Временный потолок ускорения, не повышает рабочие лимиты. |
| `bottom_position` | 203 мм | Временная привязка после поиска; не выше position_max. |
| `max_seek` | 210 мм | Максимальный ход sensorless-поиска; обычный position_max остаётся 203 мм. |
| `bottom_clearance_mm` | 5 мм | Отход от фактического DIAG; временная финальная координата bottom_position − clearance. |
| `stallguard_pause` | 2 с | Покой перед пробой, минимум 2 с. |

`TREED_Z_HOME_BOTTOM` не принимает G-code параметров и пропускает поиск при
известной Z. Перед пробой завершается очередь и выдерживается пауза.
Единственный поиск идёт через `HomingMove` в пределах `max_seek`.
Измеряется MCU trigger position, а не заданная конечная точка; при раннем
контакте отход считается от DIAG. Его координата относительна к временной системе
поиска и не является измерением абсолютной высоты крышки. После отхода Z временно
привязывается к 198 мм, рабочий Z0 затем определяет Eddy. При отказе Z сбрасывается
в unknown. Исходные поля TMC (включая ток и SGT) и motion limits возвращаются
в `finally`; ошибка внутри homing или восстановления требует `FIRMWARE_RESTART`.
В shutdown восстанавливается кэш host, подтвердить регистры отключённого MCU нельзя.

Печать, пауза, фаза калибровки, активный manual probe и SGT-калибровка запрещают
recovery. Фаза `preparing` разрешена внутри `START_PRINT`, даже когда virtual_sd
уже имеет статус printing. После перехода в `printing` допуск закрыт.
Калибровка шейпера выполняет свой G28 до перехода в фазу калибровки.

Проверять на свободном столе под наблюдением, начиная с небольшого расстояния
до нижнего упора, затем с разных высот и при разных нагрузках. Подтверждать
повторный ход, финальный отход, возврат тока и рабочий Eddy Z0. Пробу без DIAG
нельзя считать безопасной: программный лимит ограничивает командный ход, но
при пропущенных шагах привод может давить в упор до конца поиска.
Два совпавших контакта подтверждают повторяемость, а не отсутствие препятствия;
счётчики шагов не являются энкодером. При нестабильном StallGuard контур отключить
и использовать отдельный физический нижний endstop после замены источника
endstop в исполнителе; автоматического fallback и обхода через FORCE_MOVE нет.

## Первичная калибровка Eddy

Пошаговая процедура находится в [`docs/eddy-calibration.md`](../../../docs/eddy-calibration.md).
`PROBE_EDDY_CURRENT_CALIBRATE_AUTO` требует предварительно достоверной
Z-позиции; при неизвестной Z нужен отдельный проверенный сервисный порядок.
Eddy scan area, runtime `[force_move]` и ограничения макроса описаны в
`probe_eddy_duo.cfg` и [`macros-usage.md`](macros-usage.md).

`eddy_force_move_calibration.cfg` больше не нужен для первичной калибровки:
runtime `[force_move]` живёт в `probe_eddy_duo.cfg`. Старый include оставлен
пустым только для совместимости с локальными конфигами. После успешной
калибровки не используйте `TREED_DEPLOY_MODE=clean`, если нужно сохранить
Klipper autosave-сегмент; для повторной раскладки используйте `preserve` или
`auto`.

## Калибровка input shaper

ADXL345 подключен на EBB42 в `ebb42_can.cfg`. Секция `input_shaper.cfg` намеренно не содержит частоты, типы и damping ratio: эти значения живут в stock `SAVE_CONFIG`-блоке `printer.cfg`, чтобы `SHAPER_CALIBRATE` мог сохранять новые результаты без конфликта с include.

Ручная full-калибровка:

```gcode
TREED_SHAPER_CALIBRATE_FULL ACCEL=25000
```

Макрос делает homing, запускает быстрый sweep X/Y, затем вызывает `TREED_SAVE_CONFIG`. После сохранения Klipper штатно перезапускается.

Отдельный легкий сервисный прогон:

```gcode
TREED_SHAPER_CALIBRATE_LIGHT ACCEL=12000
```

Команда измеряет узкие диапазоны вокруг сохраненных `shaper_freq_x/y`, применяет новые значения на текущую сессию и не вызывает `SAVE_CONFIG`.

PID хотэнда и стола остается в профильных heater-секциях, потому что Klipper требует `pid_Kp/Ki/Kd` при загрузке `control: pid`. После `PID_CALIBRATE HEATER=extruder ...` или `PID_CALIBRATE HEATER=heater_bed ...` новые значения нужно перенести в `ebb42_can.cfg` или `bed_heater_dc.cfg`.

## Переменные окружения

Диагностические серии нижней опоры, Eddy Z0 и mesh описаны в
[приёмке Z/Eddy](../../../docs/z-eddy-acceptance.md). Геометрия Z=203 мм измерена;
software PASS не заменяет аппаратную приёмку StallGuard и Eddy.

- `TREED_MAIN_MCU_CANBUS_UUID` — Octopus Pro UUID, default `d372e54bf965`.
- `TREED_CAN_IFACE` — интерфейс CAN, default `can0`.
- `TREED_EBB_CANBUS_UUID` — EBB42 UUID, default `efaf957ab20f`.
- `TREED_EDDY_ENABLED` — legacy-переменная loader; для этого Klipper-профиля должно оставаться `1`.
- `TREED_EDDY_CANBUS_UUID` — Eddy UUID, default `95485b93332a`.
