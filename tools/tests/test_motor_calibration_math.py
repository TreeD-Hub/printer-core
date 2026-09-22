# ==========================================
# TEST: TreeD motor measurement math
# ==========================================
# Назначение: геометрия, синхронный анализ и критерии приёмки.
# Контур: локальный, без принтера и внешних сервисов.
import importlib.util
import math
from pathlib import Path
import unittest


SOURCE = (Path(__file__).resolve().parents[2] / 'klipper-host'
          / 'treed_motor_math.py')
SPEC = importlib.util.spec_from_file_location('treed_motor_math', SOURCE)
motor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(motor)


class MotorMathTest(unittest.TestCase):
    def test_corexy_motor_isolation_and_bounds(self):
        center = (120., 120., 25.)
        for name in ('stepper_x', 'stepper_y'):
            for direction in (-1, 1):
                start, end = motor.diagonal(name, center, 40., direction)
                self.assertTrue(all(25 <= p[0] <= 215 and 25 <= p[1] <= 215
                                    for p in (start, end)))
                dx, dy = end[0] - start[0], end[1] - start[1]
                other = 'stepper_y' if name == 'stepper_x' else 'stepper_x'
                self.assertEqual(motor.motor_component(other, dx, dy), 0)
                self.assertNotEqual(motor.motor_component(name, dx, dy), 0)
        self.assertAlmostEqual(motor.path_motor_speed(
            'stepper_x', 35., (0., 0., 0.), (10., 10., 0.)),
            35. * math.sqrt(2.))
        self.assertEqual(motor.path_motor_speed(
            'stepper_x', 35., (0., 0., 0.), (10., 0., 0.)), 35.)
        with self.assertRaises(motor.MeasurementError):
            motor.cruise_window(5., 40., 100., .6)

    def test_synchronous_signal_and_quality_rejection(self):
        rate, base = 3200., 20.
        idle = [(i / rate, 0., 0., 0.) for i in range(4800)]
        samples = []
        for i in range(3200):
            t = 2. + i / rate
            signal = (30. * math.sin(2. * math.pi * 2 * base * t)
                      + 12. * math.sin(2. * math.pi * 4 * base * t))
            samples.append((t, signal, 0., 0., 2. * math.pi * base * t))
        result = motor.measure_harmonics(samples, idle, 2., 2.9, base)
        self.assertAlmostEqual(result['H2']['amplitude_mm_s2'], 30., delta=1.)
        self.assertAlmostEqual(result['H4']['amplitude_mm_s2'], 12., delta=1.)
        one_missing = motor.measure_harmonics(
            samples[:1500] + samples[1501:], idle, 2., 2.9, base)
        self.assertEqual(one_missing['H2']['estimated_missing_samples'], 1)
        self.assertAlmostEqual(one_missing['H2']['amplitude_mm_s2'], 30., delta=1.)
        with self.assertRaisesRegex(
                motor.MeasurementError, 'dropped_samples: estimated_missing=100'):
            motor.measure_harmonics(samples[:100] + samples[200:], idle,
                                    2., 2.9, base)
        quiet = [s + (2. * math.pi * base * s[0],) for s in idle]
        weak = motor.measure_harmonics(quiet, idle, 0., 1., base)
        self.assertTrue(all(h['quality'] == 'insufficient_signal'
                            for h in weak.values()))
        fast = motor.measure_harmonics(quiet, idle, 0., 1., 250.)
        self.assertEqual(fast['H2']['quality'], 'insufficient_signal')
        self.assertEqual(fast['H4']['quality'], 'unmeasurable')
        self.assertEqual(fast['H4']['reason'], 'sensor_bandwidth')
        diagonal_motor_hz = motor.electrical_frequency(
            350. * math.sqrt(2.), 40., 200.)
        self.assertGreater(8. * diagonal_motor_hz, rate)

    def test_independent_verification_rejects_single_motor_regression(self):
        def row(name, amplitude):
            return {'motor': name, 'direction': 'positive',
                    'speed_mm_s': 15., 'trajectory': 'joint_x',
                    'harmonics': {
                        'H2': {'quality': 'valid',
                               'amplitude_mm_s2': amplitude,
                               'noise_floor_mm_s2': .1},
                        'H4': {'quality': 'unmeasurable'}}}

        baseline = [row(name, 20.) for _ in range(2)
                    for name in ('stepper_x', 'stepper_y') for _ in range(4)]
        corrected = [row(name, 15. if name == 'stepper_x' else 20.)
                     for _ in range(2)
                     for name in ('stepper_x', 'stepper_y') for _ in range(4)]
        self.assertTrue(motor.compare_verification(baseline, corrected)['accepted'])
        corrected[4] = row('stepper_y', 24.)
        self.assertEqual(motor.compare_verification(baseline, corrected)['reason'],
                         'individual_condition_regressed')

    def test_verification_rejects_unmeasurable_high_speed(self):
        def row():
            return {'motor': 'stepper_x', 'direction': 'positive',
                    'speed_mm_s': 350., 'trajectory': 'isolated_diagonal',
                    'harmonics': {
                        'H2': {'quality': 'unmeasurable'},
                        'H4': {'quality': 'unmeasurable'}}}

        verdict = motor.compare_verification([row(), row()], [row(), row()])
        self.assertFalse(verdict['accepted'])
        self.assertEqual(verdict['reason'],
                         'unmeasurable_verification_condition')


if __name__ == '__main__':
    unittest.main()
