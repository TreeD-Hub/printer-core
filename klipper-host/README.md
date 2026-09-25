# TreeD Klipper host extensions

`loader/steps/runtime-bootstrap.sh` копирует эти модули в `klippy/extras`
закреплённого Klipper. Здесь хранятся расширения для профиля `treed_v2_corexy_v1`.

`treed_motor_guard.py` запускает G28 X/Y, калибровку шейпера и XY-тест с
временными лимитами и возвращает их при ошибке без движения.

`treed_sgt_executor.py` — отдельно включаемый исполнитель проб X/Y. Ядро
`tools/treed_sgt_calibration.py` доставляется рядом с ним из единственного исходника.
Активация, команда и ограничения: [подбор SGT](../docs/sgt-calibration.md).
