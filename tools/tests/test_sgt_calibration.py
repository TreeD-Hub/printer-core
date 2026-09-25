"""Офлайн-тесты поиска SGT. Контур: read-only относительно принтера."""
import dataclasses
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from treed_sgt_calibration import (  # noqa: E402
    CalibrationConfig, ProbeAborted, ProbePair, calibrate_sgt,
)


# Блок 1: Синтетический исполнитель; никакого доступа к Klipper или MCU.
def config(**changes):
    values = dict(sgt_min=-3, sgt_max=3, reference_mm=0.0,
                  position_tolerance_mm=1.0, home_direction=1)
    values.update(changes)
    return CalibrationConfig(**values)


def pair(error):
    return ProbePair(-error / 2, error / 2)


class FakeProbe:
    def __init__(self, errors):
        self.errors = errors
        self.calls = []

    def __call__(self, sgt):
        self.calls.append(sgt)
        error = self.errors[sgt]
        if isinstance(error, Exception):
            raise error
        return pair(error)

    @property
    def order(self):
        return list(dict.fromkeys(self.calls))


# Блок 2: Поведение поиска и выбора непрерывного лучшего участка.
class SearchTests(unittest.TestCase):
    def test_middle_upper_then_lower_order(self):
        probe = FakeProbe(dict.fromkeys(range(-3, 4), 0.01))
        result = calibrate_sgt(config(), probe)
        self.assertEqual(probe.order, [0, 1, 2, 3, -1, -2, -3])
        self.assertEqual(result.recommended_sgt, 0)
        self.assertEqual(result.best_interval, (-3, 3))
        self.assertEqual(len(probe.calls), 7 * 4)

    def test_negative_middle_matches_cpp_truncation(self):
        probe = FakeProbe(dict.fromkeys(range(-4, 0), 0.01))
        calibrate_sgt(config(sgt_min=-4, sgt_max=-1), probe)
        self.assertEqual(probe.order, [-2, -1, -3, -4])

    def test_middle_of_best_plateau_not_entire_valid_range(self):
        probe = FakeProbe({-3: 0.12, -2: 0.08, -1: 0.03, 0: 0.01,
                           1: 0.01, 2: 0.01, 3: 0.08})
        result = calibrate_sgt(config(), probe)
        self.assertEqual(result.recommended_sgt, 1)
        self.assertEqual(result.best_interval, (0, 2))

    def test_even_plateau_rounds_to_right_middle(self):
        probe = FakeProbe({0: 0.1, 1: 0.01, 2: 0.01, 3: 0.1})
        result = calibrate_sgt(config(sgt_min=0), probe)
        self.assertEqual(result.recommended_sgt, 2)
        self.assertEqual(result.best_interval, (1, 2))

    def test_disconnected_equal_best_does_not_include_gap(self):
        errors = {-3: 0.01, -2: 0.01, -1: 0.1, 0: 0.1,
                  1: 0.1, 2: 0.01, 3: 0.01}
        result = calibrate_sgt(config(), FakeProbe(errors))
        self.assertEqual(result.best_interval, (2, 3))
        self.assertEqual(result.recommended_sgt, 3)

    def test_unique_best_is_not_replaced_by_window_middle(self):
        errors = dict.fromkeys(range(-3, 4), 0.1)
        errors[-2] = 0.0
        result = calibrate_sgt(config(), FakeProbe(errors))
        self.assertEqual(result.recommended_sgt, -2)
        self.assertEqual(result.best_interval, (-2, -2))

    def test_float_noise_uses_explicit_epsilon(self):
        probe = FakeProbe({0: 0.01, 1: 0.0100000001})
        result = calibrate_sgt(config(sgt_min=0, sgt_max=1), probe)
        self.assertEqual(result.best_interval, (0, 1))

    def test_bad_after_good_stops_direction(self):
        probe = FakeProbe({0: 0.01, 1: 0.7, -1: 0.7})
        result = calibrate_sgt(config(), probe)
        self.assertEqual(probe.order, [0, 1, -1])
        self.assertEqual(result.recommended_sgt, 0)
        self.assertEqual(probe.calls.count(1), 1)
        self.assertEqual(probe.calls.count(-1), 1)

    def test_bad_middle_good_upper_skips_lower_half(self):
        probe = FakeProbe({0: 0.7, 1: 0.01, 2: 0.7})
        result = calibrate_sgt(config(), probe)
        self.assertEqual(probe.order, [0, 1, 2])
        self.assertEqual(result.recommended_sgt, 1)

    def test_no_good_upper_still_searches_lower_half(self):
        probe = FakeProbe({0: 0.7, 1: 0.7, 2: 0.7, 3: 0.7,
                           -1: 0.01, -2: 0.7})
        result = calibrate_sgt(config(), probe)
        self.assertEqual(probe.order, [0, 1, 2, 3, -1, -2])
        self.assertEqual(result.recommended_sgt, -1)

    def test_all_bad_returns_no_recommendation(self):
        result = calibrate_sgt(config(), FakeProbe(dict.fromkeys(range(-3, 4), 0.7)))
        self.assertEqual(result.status, 'no_reliable_result')
        self.assertIsNone(result.recommended_sgt)
        self.assertIsNone(result.best_interval)

    def test_single_explicit_setting_can_be_evaluated(self):
        result = calibrate_sgt(config(sgt_min=2, sgt_max=2), FakeProbe({2: 0.01}))
        self.assertEqual(result.recommended_sgt, 2)

    def test_budget_boundary_and_complete_sample_count(self):
        result = calibrate_sgt(config(sgt_min=0, sgt_max=0), FakeProbe({0: 0.15}))
        candidate = result.candidates[0]
        self.assertEqual(candidate.status, 'valid')
        self.assertEqual(len(candidate.pairs), 4)
        self.assertAlmostEqual(candidate.total_abs_error_mm, 0.6)

    def test_cumulative_budget_rejects_before_last_sample(self):
        probe = FakeProbe({0: 0.21})
        result = calibrate_sgt(config(sgt_min=0, sgt_max=0), probe)
        self.assertEqual(len(probe.calls), 3)
        self.assertEqual(result.candidates[0].status, 'unstable')

    def test_full_range_is_finite_and_never_widens(self):
        probe = FakeProbe(dict.fromkeys(range(-64, 64), 0.01))
        result = calibrate_sgt(config(sgt_min=-64, sgt_max=63), probe)
        self.assertEqual(len(result.candidates), 128)
        self.assertEqual(len(probe.calls), 512)
        self.assertEqual(set(probe.calls), set(range(-64, 64)))


# Блок 3: Отказы не должны превращаться в успешную калибровку.
class FailureTests(unittest.TestCase):
    def test_repeatable_early_trigger_is_not_valid(self):
        for direction in (-1, 1):
            with self.subTest(direction=direction):
                result = calibrate_sgt(
                    config(sgt_min=0, sgt_max=0, home_direction=direction),
                    lambda sgt: ProbePair(-2 * direction, -2 * direction))
                self.assertEqual(result.candidates[0].status, 'early_trigger')
                self.assertIsNone(result.recommended_sgt)

    def test_late_trigger_aborts_both_directions(self):
        for direction in (-1, 1):
            with self.subTest(direction=direction):
                result = calibrate_sgt(
                    config(home_direction=direction),
                    lambda sgt: ProbePair(2 * direction, 2 * direction))
                self.assertEqual(result.status, 'aborted')
                self.assertEqual(result.reason, 'late_trigger_or_invalid_reference')

    def test_second_hit_is_also_checked_against_reference(self):
        result = calibrate_sgt(config(sgt_min=0, sgt_max=0),
                               lambda sgt: ProbePair(0, -2))
        self.assertEqual(result.candidates[0].status, 'early_trigger')

    def test_nonzero_reference_and_y_max(self):
        result = calibrate_sgt(
            config(sgt_min=0, sgt_max=0, reference_mm=245, home_direction=1),
            lambda sgt: ProbePair(245.0, 244.99))
        self.assertEqual(result.status, 'recommended')

    def test_no_trigger_after_good_candidate_clears_recommendation(self):
        probe = FakeProbe({0: 0.01, 1: ProbeAborted('no_trigger')})
        result = calibrate_sgt(config(), probe)
        self.assertEqual(result.status, 'aborted')
        self.assertEqual(result.reason, 'no_trigger')
        self.assertIsNone(result.recommended_sgt)
        self.assertEqual(probe.order, [0, 1])

    def test_unexpected_exception_is_terminal(self):
        probe = FakeProbe({0: OSError('MCU disconnected')})
        result = calibrate_sgt(config(), probe)
        self.assertEqual(result.status, 'aborted')
        self.assertIn('OSError', result.reason)
        self.assertEqual(len(probe.calls), 1)

    def test_nan_and_infinity_are_not_valid_samples(self):
        for bad in (math.nan, math.inf, -math.inf, True, '0'):
            with self.subTest(bad=bad):
                result = calibrate_sgt(config(), lambda sgt: ProbePair(0, bad))
                self.assertEqual(result.status, 'aborted')
                self.assertEqual(result.reason, 'non_finite_measurement')

    def test_finite_samples_with_overflowing_sum_abort(self):
        result = calibrate_sgt(
            config(sgt_min=0, sgt_max=0, position_tolerance_mm=1e308,
                   error_budget_mm=1e308),
            lambda sgt: ProbePair(-5e307, 5e307))
        self.assertEqual(result.status, 'aborted')
        self.assertEqual(result.reason, 'measurement_overflow')

    def test_missing_measurement_is_terminal(self):
        result = calibrate_sgt(config(), lambda sgt: None)
        self.assertEqual(result.reason, 'invalid_measurement_type')

    def test_cancel_before_first_probe_does_not_call_executor(self):
        probe = FakeProbe({})
        result = calibrate_sgt(config(), probe, cancelled=lambda: True)
        self.assertEqual(result.reason, 'cancelled')
        self.assertEqual(probe.calls, [])

    def test_cancel_during_probe_does_not_accept_its_result(self):
        flag = [False]
        def probe(sgt):
            flag[0] = True
            return pair(0)
        result = calibrate_sgt(config(), probe, cancelled=lambda: flag[0])
        self.assertEqual(result.status, 'aborted')
        self.assertIsNone(result.recommended_sgt)

    def test_config_and_measurement_are_immutable(self):
        with self.assertRaises(dataclasses.FrozenInstanceError):
            config().reference_mm = 5
        with self.assertRaises(dataclasses.FrozenInstanceError):
            pair(0).first_mm = 5

    def test_invalid_config_rejected_without_motion(self):
        for changes in (
            dict(sgt_min=-65), dict(sgt_max=64), dict(sgt_min=4),
            dict(sgt_min=True), dict(sgt_min=0.5), dict(home_direction=0),
            dict(samples_per_sgt=1), dict(samples_per_sgt=17),
            dict(reference_mm=math.nan), dict(reference_mm='0'),
            dict(position_tolerance_mm=0), dict(position_tolerance_mm=math.inf),
            dict(error_budget_mm=-1), dict(error_budget_mm=False),
            dict(score_epsilon_mm=-1),
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                config(**changes)

    def test_unknown_coordinate_frame_abort_is_not_swallowed(self):
        result = calibrate_sgt(config(), FakeProbe({0: ProbeAborted('reference_unknown')}))
        self.assertEqual(result.reason, 'reference_unknown')
        self.assertIsNone(result.recommended_sgt)


if __name__ == '__main__':
    unittest.main()
