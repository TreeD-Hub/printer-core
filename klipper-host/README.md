# TreeD Klipper host extensions

`loader/steps/runtime-bootstrap.sh` копирует эти модули в `klippy/extras`
закреплённого Klipper. Здесь хранится только код, загружаемый активным
профилем `treed_v2_corexy_v1`.

`treed_motor_guard.py` запускает G28 X/Y, калибровку шейпера и XY-тест с
временными лимитами и возвращает их при ошибке без движения.
