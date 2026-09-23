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
                        'H4': {'quality': 'valid',
                               'amplitude_mm_s2': amplitude,
                               'noise_floor_mm_s2': .1}}}

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

    def test_baseline_repeatability_and_regression_conditions(self):
        def row(name, direction, speed, trajectory, h2, h4):
            return {'motor': name, 'direction': direction,
                    'speed_mm_s': speed, 'trajectory': trajectory,
                    'sample_gap_retries': 0,
                    'harmonics': {'H2': {'quality': 'valid',
                                         'amplitude_mm_s2': h2,
                                         'noise_floor_mm_s2': .1},
                                  'H4': {'quality': 'valid',
                                         'amplitude_mm_s2': h4,
                                         'noise_floor_mm_s2': .1}}}

        baseline = [row(motor_name, direction, speed, trajectory, 20., 20.)
                    for _ in range(3)
                    for motor_name in ('stepper_x', 'stepper_y')
                    for direction in ('positive', 'negative')
                    for speed, trajectory in ((200., 'isolated_diagonal'),
                                              (100., 'joint_x'))]
        stats = motor.baseline_summary(
            [r for r in baseline if r['motor'] == 'stepper_x' and
             r['speed_mm_s'] == 100.], 4)
        self.assertEqual(stats['positive']['cv'], 0.)
        self.assertEqual(stats['negative']['retry_count'], 0)
        corrected = [row(r['motor'], r['direction'], r['speed_mm_s'],
                         r['trajectory'], 15., 15.) for r in baseline]
        self.assertTrue(motor.compare_verification(baseline, corrected)['accepted'])
        missing = [dict(r, harmonics={key: dict(value)
                                       for key, value in r['harmonics'].items()})
                   for r in baseline]
        for r in missing:
            if r['speed_mm_s'] == 100. and r['trajectory'] == 'joint_x':
                r['harmonics']['H4']['quality'] = 'unmeasurable'
        self.assertEqual(motor.compare_verification(missing, corrected)['reason'],
                         'unmeasurable_verification_condition')
        for speed, trajectory, harmonic in ((200., 'isolated_diagonal', 'H2'),
                                            (100., 'joint_x', 'H4')):
            changed = [dict(r, harmonics={key: dict(value)
                                             for key, value in r['harmonics'].items()})
                       for r in corrected]
            for r in changed:
                if (r['motor'] == 'stepper_x' and r['speed_mm_s'] == speed and
                        r['trajectory'] == trajectory):
                    r['harmonics'][harmonic]['amplitude_mm_s2'] = 24.
            verdict = motor.compare_verification(baseline, changed)
            self.assertEqual(verdict['reason'], 'individual_condition_regressed')
            self.assertEqual(verdict['condition']['harmonic'], harmonic)

    def test_circle_geometry_sectors_motor_velocities_and_phase_analysis(self):
        center = (120., 100., 25.)
        clockwise = motor.circle_points(center, 30., True)
        counterclockwise = motor.circle_points(center, 30., False)
        self.assertEqual(len(clockwise), 129)
        self.assertEqual(clockwise[0], clockwise[-1])
        self.assertEqual(counterclockwise[0], counterclockwise[-1])
        self.assertLess(clockwise[1][1], center[1])
        self.assertGreater(counterclockwise[1][1], center[1])
        self.assertTrue(all(25. <= x <= 215. and 25. <= y <= 195.
                            for x, y, _ in clockwise + counterclockwise))
        self.assertEqual(motor.circle_sector(150., 100., center), 0)
        self.assertEqual(motor.circle_sector(120., 130., center), 4)
        self.assertAlmostEqual(motor.circle_motor_velocities(
            50., 0., False)['stepper_x'], 50.)
        self.assertAlmostEqual(motor.circle_motor_velocities(
            50., math.pi / 4., False)['stepper_y'], -50. * math.sqrt(2.))

        rate, speed, radius = 1600., 50., 30.
        duration = 2. * math.pi * radius / speed
        samples = []
        for index in range(int(rate * duration)):
            t = index / rate
            angle = speed * t / radius
            x, y = center[0] + radius * math.cos(angle), center[1] + radius * math.sin(angle)
            phase_a = 2. * math.pi * 1.25 * (x + y)
            phase_b = 2. * math.pi * 1.25 * (x - y)
            samples.append((t, 20. * math.sin(2. * phase_a), 0., 0.,
                            phase_a, phase_b, x, y))
        result = motor.circle_analysis(
            samples, center, radius,
            {'stepper_x': 50. * math.sqrt(2.) * 1.25,
             'stepper_y': 50. * math.sqrt(2.) * 1.25}, False)
        self.assertEqual(len(result['sectors']), 16)
        self.assertAlmostEqual(result['total']['actual_speed_mm_s'], speed, delta=1.)
        self.assertGreater(result['total']['rms_accel_mm_s2'], 10.)
        self.assertTrue(any(sector['harmonics']['stepper_x_H2']['quality'] == 'valid'
                            for sector in result['sectors']))
        with self.assertRaisesRegex(motor.MeasurementError, 'dropped_samples'):
            motor.circle_analysis(samples[:100] + samples[120:], center, radius,
                                  {'stepper_x': 90., 'stepper_y': 90.}, False)

    def test_circle_paired_delta_requires_repeatability(self):
        def row(value):
            return {'rms_accel_mm_s2': value,
                    'peak_accel_mm_s2': value * 2.,
                    'vibration_energy_mm2_s4': value * value,
                    'harmonics': {'stepper_x_H2': {
                        'quality': 'valid', 'amplitude_mm_s2': value / 2.}}}

        improved = motor.circle_compare([(row(20.), row(15.)) for _ in range(3)])
        self.assertEqual(improved['rms_accel_mm_s2']['absolute_delta'], -5.)
        self.assertEqual(improved['rms_accel_mm_s2']['relative_delta_percent'], -25.)
        self.assertEqual(improved['rms_accel_mm_s2']['verdict'], 'vibration_reduced')
        self.assertIn('stepper_x_H2', improved)
        noisy = motor.circle_compare([(row(20.), row(v))
                                      for v in (15., 20., 22.)])
        self.assertEqual(noisy['rms_accel_mm_s2']['verdict'],
                         'no_statistically_meaningful_change')
        single = motor.circle_compare([(row(20.), row(10.))])
        self.assertIsNone(single['rms_accel_mm_s2']['paired_ci95_half_width'])


if __name__ == '__main__':
    unittest.main()
