# TreeD Klipper host extensions

`loader/steps/runtime-bootstrap.sh` копирует эти модули в `klippy/extras`
закреплённого Klipper. Здесь хранится только код, загружаемый активным
профилем `treed_v2_corexy_v1`.

`treed_motor_calibration.py` выполняет измерения, подбор, проверку и явное
применение XY-профиля; `treed_motor_math.py` проверяет геометрию и сигнал;
`treed_motor_wave.py` кодирует ограниченную таблицу токов TMC5160. Исполнитель
микрошагов остаётся штатным MCU/Klipper и внутренним секвенсором TMC5160.
Прямой режим `XTARGET` не используется. См. `docs/motor-noise-calibration.md`.

`treed_motor_guard.py` запускает G28 X/Y, калибровку шейпера и XY-тест с
временными лимитами и возвращает их при ошибке без движения.
