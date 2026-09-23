# ==========================================
# TEST: TreeD TMC5160 wave table
# ==========================================
# Назначение: кодирование и границы таблицы токов.
# Контур: локальный, без принтера и внешних сервисов.
import importlib.util
from pathlib import Path
import unittest


SOURCE = Path(__file__).resolve().parents[2] / 'klipper-host' / 'treed_motor_wave.py'
SPEC = importlib.util.spec_from_file_location('treed_motor_wave', SOURCE)
wave = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wave)


class MotorWaveTest(unittest.TestCase):
    def test_klipper_default_wave_is_accepted_without_relaxing_other_tables(self):
        points = wave.decode_table(wave.DEFAULT_TABLE)
        self.assertEqual((points[0], points[-1]), (0, 248))
        self.assertEqual((wave.DEFAULT_TABLE['MSLUTSTART'] >> 16) & 255, 247)
        invalid = dict(wave.DEFAULT_TABLE)
        invalid['MSLUT7'] ^= 1 << 31
        with self.assertRaisesRegex(ValueError, 'wave_shape_limit'):
            wave.decode_table(invalid)

    def test_stock_to_generated_table_has_reachable_bounded_switch_phase(self):
        scores = wave.transition_scores(wave.DEFAULT_TABLE, wave.make_table())
        self.assertEqual(scores[120], 8)
        self.assertGreater(scores[248], 16)

    def test_encoded_wave_is_bounded_and_continuous(self):
        for a2, p2, a4, p4 in ((0., 0., 0., 0.), (2., 0., 0., 0.),
                               (0., 0., 2., 0.)):
            table = wave.make_table(a2, p2, a4, p4)
            points = wave.decode_table(table)
            self.assertEqual((points[0], points[-1]), (0, 247))
            self.assertTrue(all(0 <= b - a <= 2 for a, b in
                                zip(points, points[1:])))
        with self.assertRaises(ValueError):
            wave.make_table(a2=9.)

    def test_phase_grid_has_encodable_trials_for_both_harmonics(self):
        for harmonic in (2, 4):
            valid = 0
            for phase in wave.PHASES:
                try:
                    table = wave.make_table(**{'a%d' % harmonic: 2.,
                                               'p%d' % harmonic: phase})
                except ValueError:
                    continue
                wave.decode_table(table)
                valid += 1
            self.assertGreater(valid, 0)

    def test_phase_projection_preserves_zero_and_rejects_unrepresentable_terms(self):
        self.assertEqual(wave.BACKEND_CAPABILITIES['phase_harmonics'], (4,))
        self.assertEqual(wave.BACKEND_CAPABILITIES['measurable_harmonics'],
                         (2, 4))
        self.assertFalse(wave.BACKEND_CAPABILITIES['direction_specific'])
        zero = dict.fromkeys(('s4', 'c4'), 0.)
        table, error = wave.phase_table(zero, wave.DEFAULT_TABLE)
        self.assertEqual(table, wave.DEFAULT_TABLE)
        self.assertEqual(error['rms_error_lsb'], 0.)
        correction = dict(zero, s4=.02)
        table, error = wave.phase_table(correction, wave.DEFAULT_TABLE)
        self.assertNotEqual(table, wave.DEFAULT_TABLE)
        self.assertLess(error['rms_error_lsb'],
                        .35 * error['rms_signal_lsb'])
        for coefficient in ('s2', 'c2'):
            with self.assertRaisesRegex(ValueError, 'phase_harmonic_unsupported'):
                wave.phase_table(dict(zero, **{coefficient: .02}),
                                 wave.DEFAULT_TABLE)
        with self.assertRaises(ValueError):
            wave.phase_table(dict(zero, c4=.02), wave.DEFAULT_TABLE)
        with self.assertRaisesRegex(ValueError, 'phase_coefficients_invalid'):
            wave.phase_table(None, wave.DEFAULT_TABLE)


if __name__ == '__main__':
    unittest.main()
