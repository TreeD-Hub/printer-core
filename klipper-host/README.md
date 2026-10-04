# TreeD Klipper host extensions

`loader/steps/runtime-bootstrap.sh` копирует эти модули в `klippy/extras`
закреплённого Klipper. Здесь хранятся расширения для профиля `treed_v2_corexy_v1`.

`treed_driver_mode.py` управляет режимом TMC5160 XYZ, с допуском и групповым откатом.
`treed_driver_fan_mode.py` включает обдув по нагрузке и защитному таймеру,
меняет мощность в проверенном диапазоне. Пин и timer принадлежат controller_fan;
его callback заменяется до `klippy:ready` для закреплённого Klipper ce7002bed.
[Команды, сохранение и аппаратный допуск](../klipper/profiles/treed_v2_corexy_v1/ui-driver-modes-contract.md).

`treed_print_cancel.py` подключается секцией `[treed_print_cancel]` в `ui.cfg`.
Штатный `/printer/print/cancel` и одиночный `CANCEL_PRINT` через Moonraker
сигнализируют отмену до ожидания G-code mutex: нагрев выключается, SD-чтение
останавливается, `M109`/`M190`/`TEMPERATURE_WAIT` прерываются, остаток макроса
не выполняется. Затем штатный `CANCEL_PRINT` закрывает задачу как `cancelled`.
Текущая команда движения/homing завершается штатно; для немедленного отключения
движения нужен аварийный стоп. При обновлении Klipper сверять GCodeDispatch,
`PrinterHeaters._wait_for_temperature` и регистрацию `TEMPERATURE_WAIT`.

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
Для рабочего Z-лимита используются `rail.position_max`, `CoreXY.limits` и
`CoreXY.axes_max`; `MCU_stepper.get_mcu_position` должен переживать смену координат.

Этот же extra предоставляет диагностические команды и telemetry Z/Eddy.
Диагностический mesh временно подавляет `bed_mesh.save_profile`, восстанавливает
callback и проверяет неизменность pending autosave. Сверять также BedMesh,
configfile.get_status и GCodeDispatch.ready_gcode_handlers при обновлении Klipper.
[Контракты и запуск серий](../docs/z-eddy-acceptance.md).
