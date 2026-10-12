# Host-расширения Klipper для TreeD

`loader/steps/runtime-bootstrap.sh` копирует эти модули в `klippy/extras`
закреплённого Klipper. Здесь хранятся расширения для профиля `treed_v2_corexy_v1`.
Версию Klipper задаёт [`runtime-versions.env`](../runtime-versions.env).

## Модули и контракты

`treed_hotend_limit.py` подключается `[treed_hotend_limit]` в `ebb42_can.cfg`.
`max_target: 285` ограничивает цели хотэнда для `M104`, `M109`,
`SET_HEATER_TEMPERATURE`, API и `PID_CALIBRATE`: запрос 390 °C становится 285 °C.
Аварийный предел датчика задаётся отдельно через `[extruder] max_temp: 300`.
Модуль сохраняет штатный PID, выключение нагрева и проверку нижнего предела;
неконечные числа отклоняет. При обновлении Klipper сверять `Heater.set_temp`,
`Heater.alter_target` и создание `ControlAutoTune` в `PID_CALIBRATE`.

`treed_calibration.py` подключается `[treed_calibration]` в `ui.cfg`.
`TREED_EDDY_SET_OFFSETS X=... Y=...` проверяет область mesh и винтов, сохраняет
XY в `local_overrides.cfg` и перезапускает Klipper. Зависимые результаты
инвалидируются до записи; Z-home остаётся закрытым до новой калибровки высоты.
`TREED_EDDY_HEIGHT_SAVE_MARK` вызывается после первичного `ACCEPT`: сохраняет
hash новой кривой и XY. Следующий ready снимает запрет только при совпадении
фактически загруженных значений. Ошибка `SAVE_CONFIG` не подтверждает высоту.
`TREED_EDDY_TEMPERATURE_SAVE_MARK` проверяет свежие pending коэффициенты drift:
штатный Klipper сообщает ошибку fit без исключения. Флаг завершения серии
устанавливается только на следующем ready после сверки сохранённой кривой.

`TREED_EXTRUDER_CALIBRATION_EXTRUDE LENGTH=100 SPEED=2 TEMP=200` нагревает сопло,
выполняет ограниченную относительную подачу и возвращает E-режим/исходный target.
`TREED_EXTRUDER_ROTATION_DISTANCE DISTANCE=... SAVE=0|1` применяет значение
в текущей сессии либо записывает override с restart. Команды запрещены при
печати, ручном probe, температурной серии и другом сервисе; сохраняющие
команды требуют отсутствия pending config. Публикуемый `rotation_distance`
берётся из действующего stepper, а не из устаревшей копии configfile.settings.
Запись сохраняет прочие секции, комментарии, кодировку UTF-8 и переводы строк;
неоднозначная секция, многострочная числовая опция и symlink требуют ручной правки.
При обновлении Klipper сверять status configfile/manual_probe/temperature_probe,
extruder_stepper.stepper и stock SET_EXTRUDER_ROTATION_DISTANCE.

`treed_driver_mode.py` управляет режимом TMC5160 XYZ, с допуском и групповым откатом.
`treed_driver_fan_mode.py` включает обдув по нагрузке и защитному таймеру
только на 100%: подключённые вентиляторы не вращаются при меньшем PWM.
Пин и timer принадлежат controller_fan;
его callback заменяется до `klippy:ready` для закреплённого Klipper ce7002bed.
[Команды и сохранение режима](../klipper/profiles/treed_v2_corexy_v1/ui-driver-modes-contract.md).

`treed_print_cancel.py` подключается секцией `[treed_print_cancel]` в `ui.cfg`.
Штатный `/printer/print/cancel` и одиночный `CANCEL_PRINT` (включая `REASON=...`) через Moonraker
сигнализируют отмену до ожидания G-code mutex: нагрев выключается, SD-чтение
останавливается, `M109`/`M190`/`TEMPERATURE_WAIT` прерываются, остаток макроса
не выполняется. Затем штатный `CANCEL_PRINT` закрывает задачу как `cancelled`.
Текущая команда движения/homing завершается штатно; для немедленного отключения
движения нужен аварийный стоп. После cleanup команда
`_TREED_EDDY_APPLY_LIVE_Z_OFFSET Z=...` готовит переданную Z-поправку штатным
`Z_OFFSET_APPLY_PROBE` и обновляет действующий `EddyCalibration` в памяти.
Временный live offset для штатной команды восстанавливается и при ошибке записи.
Следующий Z-home использует новую кривую без перезапуска и повторного babystep.
Команда запрещена при активной или приостановленной SD-печати. Запись на диск
остаётся отдельным `TREED_SAVE_CONFIG` в простое; несохранённая поправка теряется
при перезапуске. При обновлении Klipper сверять GCodeDispatch,
`PrinterHeaters._wait_for_temperature`, регистрацию `TEMPERATURE_WAIT`,
`EddyCalibration.get_calibration` / `_load_calibration` и `Z_OFFSET_APPLY_PROBE`.

`treed_filament_motion_sensor.py` расширяет штатный encoder sensor: публикует
`pulse_count` и совместимое имя объекта `filament_motion_sensor filament_motion`.
Один счётный импульс — фронт `0→1`; настройка `detection_length` остаётся
штатным порогом отсутствия движения, а не счётчиком пройденных миллиметров.

`treed_motor_guard.py` запускает G28 X/Y, калибровку шейпера и XY-тест с
временными лимитами и возвращает их при ошибке без движения.

`treed_sgt_executor.py` — отдельно включаемый исполнитель проб X/Y. Ядро
`tools/treed_sgt_calibration.py` доставляется рядом с ним из единственного исходника.
Активация, команда и ограничения: [подбор SGT](../docs/sgt-calibration.md).

`treed_z_recovery.py` — нижняя опора неизвестной Z: одна ограниченная проба
TMC5160 и отход от фактического DIAG. Доставляется обязательно и вызывается
штатным homing при неизвестной Z. После рабочего Eddy Z0 измеряет ход от DIAG
по общей MCU-опоре и ограничивает runtime Z с запасом `bottom_clearance_mm`.
[Параметры и допуск](../klipper/profiles/treed_v2_corexy_v1/README.md#нижняя-опора-z).
При обновлении Klipper сверять `HomingMove`, CoreXY и внутренний экспорт
`TMC5160.get_status.__self__.current_helper` с закреплённой версией.
Перед Eddy-home `_TREED_EDDY_FAST_APPROACH` поднимает стол до Z=50 на 60 мм/с
с активным аналоговым триггером Eddy. Раннее срабатывание останавливает сценарий.
На границе движение завершается, затем штатный `G28.1 Z` ищет Eddy на 10 мм/с;
точный `PROBE` остаётся на 2 мм/с. Временный лимит скорости Z возвращается
после быстрого участка, в том числе при ошибке.
Для этого пути сверять также `PrinterHoming.manual_home`,
`SampleAveragingHelper.hw_probe_session` и `EddyDescend._trigger_analog`:
доступ к аналоговому триггеру зависит от закреплённого API.
Для рабочего Z-лимита используются `rail.position_max`, `CoreXY.limits` и
`CoreXY.axes_max`; `MCU_stepper.get_mcu_position` должен переживать смену координат.

Этот же extra предоставляет диагностические команды и telemetry Z/Eddy.
Диагностический mesh временно подавляет `bed_mesh.save_profile`, восстанавливает
callback и проверяет неизменность pending autosave. Сверять также BedMesh,
configfile.get_status и GCodeDispatch.ready_gcode_handlers при обновлении Klipper.
[Контракты и запуск серий](../docs/z-eddy-acceptance.md).

## Проверка изменений

Адресные офлайн-проверки перечислены в [`tools/tests`](../tools/tests/README.md).
Они проверяют программные контракты; движение, StallGuard и работу Eddy
подтверждают отдельно на оборудовании по связанным инструкциям.
