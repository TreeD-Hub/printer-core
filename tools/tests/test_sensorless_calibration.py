# Адресные синтетические проверки анализа sensorless калибровки.
# Контур: read-only; подключение к принтеру не требуется.

import importlib.util
import unittest
from pathlib import Path


PATH = Path(__file__).resolve().parents[1] / 'treed_sensorless_calibrate.py'
SPEC = importlib.util.spec_from_file_location('treed_sensorless_calibrate', PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
PLUGIN_PATH = Path(__file__).resolve().parents[2] / 'klipper-host/treed_motor_sensorless.py'
PLUGIN_SPEC = importlib.util.spec_from_file_location('treed_motor_sensorless', PLUGIN_PATH)
PLUGIN = importlib.util.module_from_spec(PLUGIN_SPEC)
PLUGIN_SPEC.loader.exec_module(PLUGIN)


def trial(speed, sgt, outcome='pass', phase='cold', stage='sweep', valid=True):
    return {'speed': speed, 'sgt': sgt, 'outcome': outcome,
            'confirmed': outcome, 'phase': phase, 'stage': stage,
            'telemetry': {'valid': valid, 'margin': 150}}


class SensorlessAnalysisTest(unittest.TestCase):
    # Блок 1: Сигнал, false trigger и пропущенный stall.
    def test_stable_signal_and_short_dips(self):
        samples = [[i * .001, 300 if i % 25 else 0, 16]
                   for i in range(100)]
        for row in samples[80:]:
            row[1] = 30
        stats = MODULE.telemetry_stats(samples, 0, .099)
        self.assertTrue(stats['valid'])
        self.assertEqual(stats['free_p10'], 300)
        self.assertFalse(MODULE.telemetry_stats(samples[:10], 0, .009)['valid'])

    def test_window_center_and_rejection(self):
        trials = []
        for speed in MODULE.SPEEDS:
            for sgt in (-3, -1, 1, 3, 5):
                outcome = ('false_trigger' if sgt == -3 else
                           'missed_stall' if sgt == 5 else 'pass')
                trials.append(trial(speed, sgt, outcome))
        self.assertEqual(MODULE.choose_candidate(trials), 1)
        trials.append(trial(80, 0, stage='fine'))
        trials.append(trial(80, 2, stage='fine'))
        for phase in ('cold', 'warm'):
            trials.extend(trial(80, 1, phase=phase, stage='validation')
                          for _ in range(10))
        self.assertEqual(MODULE.recommendation(trials, 1)['driver_SGT'], 1)
        trials[-1]['confirmed'] = 'false_trigger'
        self.assertIsNone(MODULE.recommendation(trials, 1))

    def test_narrow_or_noisy_window_rejected(self):
        narrow = [trial(speed, sgt, 'pass' if sgt == 1 else
                        'false_trigger' if sgt < 1 else 'missed_stall')
                  for speed in MODULE.SPEEDS for sgt in (-1, 1, 3)]
        self.assertIsNone(MODULE.choose_candidate(narrow))
        wide = [trial(speed, sgt) for speed in MODULE.SPEEDS
                for sgt in (-1, 1, 3)]
        wide[4]['telemetry']['valid'] = False
        self.assertIsNone(MODULE.choose_candidate(wide))


class SensorlessProbeTest(unittest.TestCase):
    # Блок 2: Ограничение диагностического хода и возврат TMC/кинематики.
    def run_probe(self, missed=False):
        class Rail:
            position_endstop = 0
            position_min = 0
            position_max = 245

            def get_endstops(self):
                return ['diag_x']

        class CoreXYKinematics:
            def __init__(self):
                self.rails = [Rail(), Rail()]
                self.limits = [(0, 245), (0, 245), (0, 255)]

        class Toolhead:
            def __init__(self):
                self.kin = CoreXYKinematics()
                self.pos = [122.5, 122.5, 10., 0.]
                self.max_velocity = 300.
                self.max_accel = 3000.
                self.square_corner_velocity = 5.
                self.min_cruise_ratio = .5

            def get_kinematics(self):
                return self.kin

            def get_status(self, now):
                return {'homed_axes': 'xyz'}

            def get_position(self):
                return list(self.pos)

            def get_last_move_time(self):
                return 1.

            def wait_moves(self):
                pass

            def set_max_velocities(self, velocity, accel, scv, ratio):
                self.max_velocity = self.max_velocity if velocity is None else velocity
                self.max_accel = self.max_accel if accel is None else accel
                self.square_corner_velocity = self.square_corner_velocity if scv is None else scv
                self.min_cruise_ratio = self.min_cruise_ratio if ratio is None else ratio

            def move(self, pos, speed):
                assert self.kin.limits[0] == (0, 245)
                self.pos = list(pos)

        class Fields:
            value = 1

            def get_field(self, name, value=None):
                return self.value if value is None else value

            def set_field(self, name, value):
                self.value = value
                return value

            def lookup_register(self, name):
                return 'COOLCONF'

        class Driver:
            def __init__(self):
                self.fields = Fields()
                self.mcu_tmc = self
                self.value = 1

            def get_fields(self):
                return self.fields

            def set_register(self, name, value, when):
                self.value = value

            def get_register_raw(self, name):
                return {'data': self.value}

        class Homing:
            def manual_home(self, head, endstops, target, speed, *args):
                assert head.kin.limits[0] == (-3., 245)
                assert target[0] == -3.
                head.pos[0] = -3. if missed else .2
                if missed:
                    raise RuntimeError('No trigger on stepper_x after full movement')

        class Gcode:
            def register_command(self, *args):
                pass

            def run_script_from_command(self, script):
                assert script == '_TREED_SENSORLESS_PREPARE'

        class Printer:
            command_error = RuntimeError

            def __init__(self):
                self.head = Toolhead()
                self.driver = Driver()
                self.gcode = Gcode()
                self.homing = Homing()

            def lookup_object(self, name, default=None):
                return {'toolhead': self.head, 'gcode': self.gcode,
                        'homing': self.homing,
                        'tmc5160 stepper_x': self.driver}.get(name, default)

            def get_reactor(self):
                return self

            def monotonic(self):
                return 0.

            def is_shutdown(self):
                return False

        class Gcmd:
            def get(self, key):
                return 'X'

            def get_float(self, key, **kwargs):
                return 80.

            def get_int(self, key, **kwargs):
                return -2

            def error(self, message):
                return RuntimeError(message)

            def respond_info(self, message):
                pass

        printer = Printer()
        config = type('Config', (), {'get_printer': lambda self: printer})()
        obj = PLUGIN.load_config(config)
        obj.cmd_probe(Gcmd())
        self.assertEqual(printer.head.kin.limits[0], (0, 245))
        self.assertEqual(printer.driver.value, 1)
        self.assertEqual(printer.head.max_accel, 3000.)
        return obj.last_result

    def test_pass_restores_limits_and_sgt(self):
        self.assertEqual(self.run_probe()['outcome'], 'pass')

    def test_missed_stall_restores_limits_and_sgt(self):
        self.assertEqual(self.run_probe(missed=True)['outcome'], 'missed_stall')


if __name__ == '__main__':
    unittest.main()
