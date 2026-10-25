# Host-расширения Klipper для TreeD

`loader/steps/runtime-bootstrap.sh` копирует эти модули в `klippy/extras`
закреплённого Klipper. Здесь хранятся расширения для профиля `treed_v2_corexy_v1`.
Версию Klipper задаёт [`runtime-versions.env`](../runtime-versions.env).

## Модули и контракты

`treed_light_control.py` подключается `[treed_light_control]` в `service_fans.cfg`.
Webhook `treed/light/set` доступен через Moonraker как
`POST /printer/treed/light/set` с JSON `{"value": 0}` или `{"value": 1}`.
Другие значения и типы отклоняются; Klipper должен быть `ready`.
Модуль использует существующий `output_pin chamber_light` и его
`gcrq.send_async_request`, обходя G-code mutex. Макросы `LIGHT_ON`/`LIGHT_OFF`
вызывают `TREED_LIGHT_SET VALUE=0|1` через ту же очередь: автоматический `LIGHT_ON`
из `START_PRINT` не оставляет отложенного lookahead-запроса после ручного off.
Последний принятый запрос определяет заданный свет; последующее автоматическое
событие может снова включить его согласно сохранённым флагам.
Поле `requested_value` в ответе подтверждает принятие команды, а
`output_pin.value` отражает заданное значение, не физическую обратную связь.
При обновлении Klipper сверять `PrinterOutputPin.gcrq`, `GCodeRequestQueue`,
`send_async_request` и `WebRequest.get_int` с закреплённым исходником.
Capability `light_async` опубликована в `_TREED_UI_CONTRACT`; UI без этого флага
сохраняет вызовы макросов. Сеть, MCU и shutdown по-прежнему ограничивают управление.

`treed_hotend_limit.py` подключается `[treed_hotend_limit]` в `ebb42_can.cfg`.
`max_target: 280` ограничивает цели хотэнда для `M104`, `M109`,
`SET_HEATER_TEMPERATURE`, API и `PID_CALIBRATE`: запрос 390 °C становится 280 °C.
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

`treed_driver_mode.py` выбирает полный профиль XYZ до создания TMC/шаговых осей:
`quiet` задаёт 128 микрошагов, интерполяцию, 100 нс, явные TMC/PWM
и лимиты 180 мм/с и 25 000 мм/с²;
`normal` читает исходные конфиги. Смена сохраняет выбор и выполняет
`FIRMWARE_RESTART`, после которого нужен homing. Пользовательские конфиги
не переписываются; отказ сохранения/перезапуска имеет восстановление выбора,
а стартовая групповая запись TMC — проверку MRES и откат.
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

`treed_motor_guard.py` запускает G28 X/Y, калибровку шейпера, XY-тест и полную
подготовку/чистку сопла внутри границы восстановления лимитов. Сервисные
`TREED_SERVICE_MOVE` и `TREED_SERVICE_RESUME` валидируют параметры и профиль,
ограничивают запрошенную скорость действующим `toolhead.max_velocity` и передают
движение штатному G1 или RESUME_BASE; M220 учитывается только для G1 при вызове.
Номиналы берутся из `[treed_driver_mode]`, а скорость RESUME — из
`[pause_resume] recover_velocity`.
G1 выполняется с сохранением и восстановлением подачи и координатного режима
без обратного движения. При неподтверждённом восстановлении система переходит
в shutdown. Для отмены печати сохранён отдельный медленный номинал CANCEL_Z
5 мм/с; общий Z_TRAVEL равен 25 мм/с.
При раскрутке отмены `treed_print_cancel.run_state_cleanup` допускает только
возврат state через guard до освобождения G-code mutex; сама отмена и запрет
следующих движений остаются активными.

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
При неизвестной Z сначала выполняется поиск нижней опоры на 60 мм/с, затем
homing X/Y и штатный `G28.1 Z` к Eddy на 20 мм/с. Точный `PROBE` остаётся на 2 мм/с.
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
