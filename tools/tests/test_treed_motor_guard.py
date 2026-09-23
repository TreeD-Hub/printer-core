"""Проверка восстановления лимитов после ошибок трёх сервисных операций."""

import importlib.util
from pathlib import Path


source = Path(__file__).resolve().parents[2] / 'klipper-host' / 'treed_motor_guard.py'
spec = importlib.util.spec_from_file_location('treed_motor_guard', source)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Toolhead:
    def __init__(self):
        self.max_velocity = 300.
        self.max_accel = 6000.
        self.square_corner_velocity = 5.
        self.min_cruise_ratio = .5

    def set_max_velocities(self, velocity, accel, scv, ratio):
        if velocity is not None:
            self.max_velocity = velocity
        if accel is not None:
            self.max_accel = accel
        if scv is not None:
            self.square_corner_velocity = scv
        if ratio is not None:
            self.min_cruise_ratio = ratio


class Gcode:
    def __init__(self, toolhead, move):
        self.toolhead = toolhead
        self.move = move
        self.commands = []
        self.fail = True

    def register_command(self, name, callback):
        self.callback = callback

    def run_script_from_command(self, script):
        self.commands.append(script)
        assert self.toolhead.max_accel != 6000.
        if not self.fail:
            return
        self.move.absolute_coord = False
        raise ValueError('ошибка движения')


class Command:
    error = ValueError

    def __init__(self, params):
        self.params = params

    def get(self, name):
        return self.params[name]

    def get_float(self, name, above=None, minval=None):
        value = float(self.params[name])
        assert above is None or value > above
        assert minval is None or value >= minval
        return value

    def get_command_parameters(self):
        return self.params


class Config:
    def __init__(self):
        self.ready = False
        self.toolhead = Toolhead()
        self.move = type('Move', (), {'absolute_coord': True})()
        self.gcode = Gcode(self.toolhead, self.move)
        self.state = type('State', (), {'variables': {'phase': 'calibrating'}})()

    def get_printer(self):
        return self

    def lookup_object(self, name):
        if not self.ready and name != 'gcode':
            raise AssertionError('ранний доступ к объекту Klipper')
        return {'toolhead': self.toolhead, 'gcode_move': self.move,
                'gcode': self.gcode,
                'gcode_macro _TREED_OPERATION_STATE': self.state}[name]


for params, macro in (
    ({'ACTION': 'HOME_XY', 'X': '1', 'Y': '1', 'ACCEL': '700'},
     '_TREED_HOME_XY_RUN'),
    ({'ACTION': 'SHAPER', 'MODE': 'light', 'ACCEL': '25000'},
     '_TREED_SHAPER_CALIBRATE_RUN'),
    ({'ACTION': 'XY_TEST', 'SPEED': '200', 'ACCEL': '5000', 'SCV': '4'},
     '_TREED_XY_MOTION_TEST_RUN'),
):
    config = Config()
    guard = module.load_config(config)
    config.ready = True
    before = vars(config.toolhead).copy()
    try:
        guard.cmd_run(Command(params))
    except ValueError as error:
        assert str(error) == 'ошибка движения'
    else:
        raise AssertionError('ошибка операции потеряна')
    assert vars(config.toolhead) == before
    assert config.move.absolute_coord is True
    if params['ACTION'] == 'SHAPER':
        assert config.state.variables['phase'] == 'idle'
    assert len(config.gcode.commands) == 1
    assert config.gcode.commands[0].startswith(macro + ' ')

config = Config()
guard = module.load_config(config)
config.ready = True
config.gcode.fail = False
guard.cmd_run(Command({'ACTION': 'HOME_XY', 'X': '1', 'Y': '0',
                       'ACCEL': '700'}))
assert config.toolhead.max_accel == 6000.

print('treed_motor_guard: OK')
