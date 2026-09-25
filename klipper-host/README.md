# TreeD Klipper host extensions

`loader/steps/runtime-bootstrap.sh` копирует эти модули в `klippy/extras`
закреплённого Klipper. Здесь хранятся расширения для профиля `treed_v2_corexy_v1`.

`treed_motor_guard.py` запускает G28 X/Y, калибровку шейпера и XY-тест с
временными лимитами и возвращает их при ошибке без движения.

`treed_sgt_executor.py` — отдельно включаемый исполнитель проб X/Y. Ядро
`tools/treed_sgt_calibration.py` доставляется рядом с ним из единственного исходника.
Активация, команда и ограничения: [подбор SGT](../docs/sgt-calibration.md).

`treed_z_recovery.py` — нижняя опора неизвестной Z: одна ограниченная проба
TMC5160 и отход от фактического DIAG. Доставляется обязательно и вызывается
штатным homing при неизвестной Z.
[Параметры и допуск](../klipper/profiles/treed_v2_corexy_v1/README.md#нижняя-опора-z).
При обновлении Klipper сверять `HomingMove`, CoreXY и внутренний экспорт
`TMC5160.get_status.__self__.current_helper` с закреплённой версией.

Этот же extra предоставляет диагностические команды и telemetry Z/Eddy.
Диагностический mesh временно подавляет `bed_mesh.save_profile`, восстанавливает
callback и проверяет неизменность pending autosave. Сверять также BedMesh,
configfile.get_status и GCodeDispatch.ready_gcode_handlers при обновлении Klipper.
[Контракты и запуск серий](../docs/z-eddy-acceptance.md).
