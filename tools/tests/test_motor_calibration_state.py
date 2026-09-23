# ==========================================
# TEST: TreeD motor calibration state
# ==========================================
# Назначение: профиль, отмена и атомарная запись.
# Контур: локальный, без принтера и внешних сервисов.
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch


HOST = Path(__file__).resolve().parents[2] / 'klipper-host'
PACKAGE = types.ModuleType('treed_motor_test_extras')
PACKAGE.__path__ = [str(HOST)]
sys.modules[PACKAGE.__name__] = PACKAGE
HOMING = types.ModuleType(PACKAGE.__name__ + '.homing')
HOMING.HomingMove = type('HomingMove', (), {'homing_move': lambda self: None})
sys.modules[HOMING.__name__] = HOMING
SPEC = importlib.util.spec_from_file_location(
    PACKAGE.__name__ + '.treed_motor_calibration',
    HOST / 'treed_motor_calibration.py')
calibration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(calibration)
from treed_motor_test_extras import treed_motor_wave as wave


class MotorStateTest(unittest.TestCase):
    def setUp(self):
        self.subject = calibration.TreedMotorCalibration.__new__(
            calibration.TreedMotorCalibration)
        self.subject.build_id = 'host-and-mcu-build'
        self.subject.config_fingerprint = 'runtime-config'
        self.subject.xy_speeds = (15., 25., 35.)
        self.subject.xy_accel = 500.
        self.subject.base_tables = {
            motor: dict(wave.DEFAULT_TABLE) for motor in calibration.XY_MOTORS}
        self.subject.candidate = None
        self.subject.verifying_saved_profile = False

    def _valid_profile(self):
        return {
            'schema': 3, 'algorithm': calibration.ALGORITHM,
            'phase_model': calibration.PHASE_MODEL,
            'build_id': self.subject.build_id,
            'config_fingerprint': self.subject.config_fingerprint,
            'coefficients': {motor: {'s4': 0., 'c4': 0.}
                             for motor in calibration.XY_MOTORS},
            'projection': {motor: {'rms_error_lsb': 0.,
                                  'rms_signal_lsb': 0.}
                           for motor in calibration.XY_MOTORS},
            'tables': self.subject.base_tables,
            'limits': {'max_velocity': 35., 'max_accel': 500.},
            'verified': {'accepted': True}}

    def test_profile_invalidated_by_firmware_or_config_change(self):
        profile = self._valid_profile()
        self.subject._validate_profile(profile)
        profile['schema'] = 1
        with self.assertRaisesRegex(ValueError, 'stale'):
            self.subject._validate_profile(profile)
        profile['schema'] = 2
        with self.assertRaisesRegex(ValueError, 'stale'):
            self.subject._validate_profile(profile)
        profile['schema'] = 3
        profile['build_id'] = 'older-mcu-build'
        with self.assertRaisesRegex(ValueError, 'stale'):
            self.subject._validate_profile(profile)
        profile['build_id'] = self.subject.build_id
        profile['config_fingerprint'] = 'older-config'
        with self.assertRaisesRegex(ValueError, 'stale'):
            self.subject._validate_profile(profile)

    def test_tune_uses_predeclared_attenuation_and_final_verification(self):
        subject = self.subject
        subject.xy_speeds = (100., 150., 200.)
        subject.old_accel = subject.xy_accel = 15000.
        subject.mcu_builds = {}
        jobs = [(motor, speed, direction, None, None)
                for motor in calibration.XY_MOTORS
                for speed in subject.xy_speeds
                for direction in ('positive', 'negative')]
        subject._geometry = lambda motors: jobs

        def collect(selected, label):
            if False:
                yield
            return [{'motor': job[0], 'direction': job[2],
                     'speed_mm_s': job[1],
                     'harmonics': {name: {'quality': 'valid',
                                          'amplitude_mm_s2': 20.,
                                          'noise_floor_mm_s2': .1}
                                   for name in ('H2', 'H4')}}
                    for job in selected]

        searched_harmonics = []

        def search(motor, harmonic, jobs, reference, score, best, trials, stage):
            if False:
                yield
            searched_harmonics.append(harmonic)
            if motor == 'stepper_x' and harmonic == 4 and stage == 'approximate_magnitude':
                best = dict(best, s4=.04)
            return score, best

        calls = []

        def verify(tables, speeds):
            if False:
                yield
            calls.append(tables)
            return {'accepted': len(calls) > 1, 'reason': 'regressed'
                    if len(calls) == 1 else ''}

        subject._collect = collect
        subject._search_phase_trials = search
        subject._verify_flow = verify
        flow = subject._tune_flow()
        while True:
            try:
                next(flow)
            except StopIteration as done:
                profile = done.value
                break
        self.assertEqual(len(calls), 3)
        self.assertEqual(set(searched_harmonics), {4})
        self.assertIn('stepper_x:H2', subject.characterization)
        self.assertEqual(profile['attenuation'], .75)
        self.assertAlmostEqual(profile['coefficients']['stepper_x']['s4'], .03)
        subject._validate_profile(profile)

    def test_profile_rejects_phase_table_mismatch(self):
        profile = self._valid_profile()
        profile['coefficients']['stepper_x']['s4'] = .02
        table, metrics = wave.phase_table(
            profile['coefficients']['stepper_x'],
            self.subject.base_tables['stepper_x'])
        profile['tables'] = dict(profile['tables'], stepper_x=table)
        profile['projection']['stepper_x'] = metrics
        self.subject._validate_profile(profile)
        profile['coefficients']['stepper_x']['s4'] = .01
        with self.assertRaisesRegex(ValueError, 'profile_table_mismatch'):
            self.subject._validate_profile(profile)

    def test_failed_saved_verification_blocks_enable_across_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            self.subject.profile_path = str(Path(directory) / 'active_profile.json')
            self.subject.profile = self._valid_profile()
            self.subject.profile_state = 'saved'
            self.subject.mode = 'verify'
            self.subject.verifying_saved_profile = True
            self.subject.timer = self.subject.old_accel = None
            self.subject.current_tables = self.subject.base_tables
            self.subject.printer = types.SimpleNamespace(
                is_shutdown=lambda: False, invoke_shutdown=Mock(),
                lookup_object=Mock())
            self.subject.gcode = types.SimpleNamespace(respond_info=Mock())
            self.subject._write_report = Mock()

            self.subject._finish('failed', 'individual_condition_regressed')
            self.assertEqual(self.subject.profile_state, 'rejected')
            self.assertEqual(self.subject.profile['verification_failure'],
                             'individual_condition_regressed')
            with open(self.subject.profile_path, encoding='utf-8') as saved:
                self.assertEqual(json.load(saved)['verification_failure'],
                                 'individual_condition_regressed')

            self.subject._load_profile()
            self.assertEqual(self.subject.profile_state, 'rejected')
            gcmd = types.SimpleNamespace(get_int=lambda *args, **kwargs: 1,
                                         error=ValueError)
            with self.assertRaisesRegex(ValueError, 'individual_condition_regressed'):
                self.subject.cmd_phase(gcmd)
            self.subject.printer.lookup_object.assert_not_called()

            self.subject._finish('verified')
            self.subject._load_profile()
            self.assertEqual(self.subject.profile_state, 'saved')
            self.assertNotIn('verification_failure', self.subject.profile)

    def test_new_calibration_replaces_rejected_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            self.subject.profile_path = str(Path(directory) / 'active_profile.json')
            self.subject.previous_path = str(Path(directory) / 'previous_profile.json')
            self.subject.profile = self._valid_profile()
            self.subject.profile['verification_failure'] = 'regressed'
            self.subject.profile_state = 'rejected'
            self.subject.candidate = self._valid_profile()
            self.subject.state = 'candidate'
            self.subject.phase_enabled = False
            gcmd = types.SimpleNamespace(error=ValueError, respond_info=Mock())

            self.subject.cmd_save(gcmd)
            self.subject._load_profile()
            self.assertEqual(self.subject.profile_state, 'saved')
            self.assertNotIn('verification_failure', self.subject.profile)
            self.assertIsNone(self.subject.candidate)

    def test_verify_tracks_saved_profile_even_with_candidate_alias(self):
        self.subject.profile = self._valid_profile()
        self.subject.candidate = self.subject.profile
        self.subject.state = 'idle'
        self.subject.phase_enabled = False
        self.subject.reactor = types.SimpleNamespace(
            monotonic=lambda: 0., NOW=0., register_timer=Mock())
        self.subject.get_status = Mock(return_value={'phase_supported': True})
        self.subject._check_base_tables = Mock()
        self.subject._ready_before_home = Mock()
        steppers = [types.SimpleNamespace(get_name=lambda name=name: name)
                    for name in calibration.XY_MOTORS]
        toolhead = types.SimpleNamespace(
            get_max_velocity=lambda: (35., 500.),
            get_kinematics=lambda: types.SimpleNamespace(
                get_steppers=lambda: steppers))
        chip = types.SimpleNamespace(start_internal_client=lambda: None)
        self.subject.printer = types.SimpleNamespace(
            lookup_object=lambda name, default=None: {
                'toolhead': toolhead, 'adxl345': chip}.get(name, default))
        gcmd = types.SimpleNamespace(
            get=lambda key, default=None: {'MODE': 'verify'}.get(key, default),
            error=ValueError, respond_info=Mock())

        self.subject.cmd_calibrate(gcmd)
        self.assertTrue(self.subject.verifying_saved_profile)

    def test_quiet_mode_rejects_limit_changes_before_application(self):
        applied = []
        toolhead = types.SimpleNamespace(max_velocity=180., max_accel=400.)

        def apply(velocity, accel, scv, ratio):
            applied.append((velocity, accel, scv, ratio))
            if velocity is not None:
                toolhead.max_velocity = velocity
            if accel is not None:
                toolhead.max_accel = accel

        toolhead.set_max_velocities = apply
        self.subject.printer = types.SimpleNamespace(command_error=ValueError)
        self.subject.profile = {'limits': {'max_velocity': 200.,
                                           'max_accel': 500.}}
        self.subject.phase_enabled = True
        self.subject._install_limit_guard(toolhead)

        with self.assertRaisesRegex(ValueError, 'TREED_MOTOR_PHASE ENABLE=0'):
            toolhead.set_max_velocities(600., 450., None, None)
        with self.assertRaisesRegex(ValueError, '500'):
            toolhead.set_max_velocities(None, 25000., None, None)
        self.assertEqual(applied, [])
        self.assertEqual((toolhead.max_velocity, toolhead.max_accel),
                         (180., 400.))

        toolhead.set_max_velocities(None, 450., None, None)
        self.assertEqual(toolhead.max_accel, 450.)
        self.subject.phase_enabled = False
        toolhead.set_max_velocities(600., 25000., None, None)
        self.assertEqual((toolhead.max_velocity, toolhead.max_accel),
                         (600., 25000.))

    def test_quiet_mode_keeps_cancel_and_end_commands_available(self):
        original = Mock()
        self.subject.gcode = types.SimpleNamespace(_process_commands=original)
        self.subject.phase_enabled = True
        self.subject.state = 'idle'
        self.subject._internal_dispatch = False
        self.subject._install_gcode_gate()

        commands = ['CANCEL_PRINT', 'END_PRINT',
                    'TREED_MOTOR_PHASE ENABLE=0']
        self.subject.gcode._process_commands(commands, False)
        original.assert_called_once_with(commands, False)

    def test_cancel_restores_table_and_temporary_acceleration(self):
        self.subject.printer = types.SimpleNamespace(
            is_shutdown=lambda: False, invoke_shutdown=Mock())
        self.subject.reactor = types.SimpleNamespace(unregister_timer=Mock())
        self.subject.gcode = types.SimpleNamespace(respond_info=Mock())
        self.subject.toolhead = types.SimpleNamespace(
            set_max_velocities=Mock())
        self.subject._write_report = Mock()
        self.subject.timer = None
        self.subject.mode = 'tune'
        self.subject.state = 'running'
        self.subject.stage = 'phase_search'
        self.subject.error = None
        self.subject.motor = 'stepper_x'
        self.subject.old_accel = 500.
        self.subject.candidate = {'verified': {'accepted': True}}
        changed = dict(self.subject.base_tables['stepper_x'])
        changed['MSLUT0'] ^= 1
        self.subject.current_tables = {'stepper_x': changed,
                                       'stepper_y': self.subject.base_tables['stepper_y']}
        self.subject._switch_tables = Mock(side_effect=lambda tables: setattr(
            self.subject, 'current_tables', tables))
        self.subject._finish('cancelled')
        self.assertEqual(self.subject.state, 'cancelled')
        self.assertIsNone(self.subject.candidate)
        self.assertEqual(self.subject.current_tables, self.subject.base_tables)
        self.subject.toolhead.set_max_velocities.assert_called_once_with(
            None, 500., None, None)

    def test_table_alignment_uses_reachable_phase_after_homing(self):
        phase = [760]
        self.subject.current_tables = self.subject.base_tables
        self.subject.drivers = {'stepper_x': types.SimpleNamespace(
            get_register_raw=lambda name: {'spi_status': 0, 'data': phase[0]})}
        self.subject.toolhead = types.SimpleNamespace(
            wait_moves=Mock(), manual_move=Mock())
        self.subject._ready = Mock(return_value={
            'axis_minimum': (0., 0., -5.),
            'axis_maximum': (245., 245., 255.)})

        def jog(motor, steps):
            phase[0] = (phase[0] + steps * 16) & 1023

        self.subject._phase_jog = Mock(side_effect=jog)
        target = self.subject._align_motor('stepper_x', wave.make_table())
        self.assertEqual(target, phase[0])
        self.assertLessEqual(wave.transition_scores(
            wave.DEFAULT_TABLE, wave.make_table())[target % 256],
            calibration.MAX_TABLE_VECTOR_DELTA)
        self.subject.toolhead.manual_move.assert_called_once_with(
            (122.5, 122.5, None), 50.)

    def test_isolated_sensor_gap_repeats_only_the_affected_pass(self):
        self.subject.gcode = types.SimpleNamespace(respond_info=Mock())
        self.subject._run_pass = Mock(side_effect=[
            calibration.motor_math.MeasurementError('dropped_samples: gap'),
            {'quality': 'valid'}])
        result = self.subject._run_pass_with_retry('same_job')
        self.assertEqual(result['sample_gap_retries'], 1)
        self.assertEqual(self.subject._run_pass.call_count, 2)
        self.subject._run_pass.assert_called_with('same_job')

    def test_failed_atomic_replace_keeps_previous_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'active_profile.json'
            target.write_text('{"old": true}', encoding='utf-8')
            with patch.object(calibration.os, 'replace', side_effect=OSError('disk')):
                with self.assertRaises(OSError):
                    self.subject._atomic_json(str(target), {'new': True})
            self.assertEqual(target.read_text(encoding='utf-8'), '{"old": true}')
            self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_homing_is_blocked_before_move_when_profile_active(self):
        self.subject.printer = types.SimpleNamespace(command_error=ValueError)
        self.subject.phase_enabled = True
        self.subject._install_homing_guard()
        try:
            with self.assertRaisesRegex(ValueError, 'disable TreeD motor phase'):
                HOMING.HomingMove().homing_move()
        finally:
            HOMING.HomingMove.homing_move = calibration.HOMING_MOVE_ORIGINAL

    def test_tuning_passes_have_bounded_cruise_windows(self):
        self.subject.xy_speeds = (100., 150., 200.)
        self.subject.xy_accel = self.subject.old_accel = 15000.
        self.subject.xy_margin = 25.
        self.subject.xy_settle = .15
        self.subject.min_cruise = .2
        self.subject.z_low, self.subject.z_high = 25., 50.
        self.subject.z_speeds = (2., 3.)
        self.subject._ready = Mock(return_value={
            'axis_minimum': (0., 0., -5.),
            'axis_maximum': (245., 245., 255.)})
        self.subject.toolhead = types.SimpleNamespace(
            get_max_velocity=lambda: (600., 15000.),
            get_kinematics=lambda: types.SimpleNamespace(
                max_z_velocity=15., max_z_accel=500.))

        jobs = self.subject._geometry(calibration.XY_MOTORS)
        self.assertEqual(len(jobs), 12)
        self.assertTrue(all(accel == 15000. and
                            all(25. <= point[0] <= 220. and
                                25. <= point[1] <= 220.
                                for point in (start, end))
                            for _, _, accel, start, end in jobs))
        joint = list(self.subject._joint_jobs((100., 200.)))
        self.assertEqual(len(joint), 8)
        self.assertEqual({job[1] for job in joint}, {100., 200.})

    def test_unmeasurable_tune_rejected_before_homing(self):
        self.subject.state = 'idle'
        self.subject.phase_enabled = False
        self.subject.xy_speeds = (100., 200., 350.)
        self.subject.xy_accel = 15000.
        self.subject.reactor = types.SimpleNamespace(monotonic=lambda: 0.)
        self.subject.get_status = Mock(return_value={'phase_supported': True})
        self.subject._check_base_tables = Mock()
        self.subject._stepper_frequency = Mock(return_value=619.)
        self.subject._ready_before_home = Mock()
        steppers = [types.SimpleNamespace(get_name=lambda name=name: name)
                    for name in calibration.XY_MOTORS]
        toolhead = types.SimpleNamespace(
            get_max_velocity=lambda: (600., 15000.),
            get_kinematics=lambda: types.SimpleNamespace(
                get_steppers=lambda: steppers))
        chip = types.SimpleNamespace(
            start_internal_client=lambda: None, data_rate=3200.)
        self.subject.printer = types.SimpleNamespace(
            lookup_object=lambda name, default=None: {
                'toolhead': toolhead, 'adxl345': chip}.get(name, default))
        gcmd = types.SimpleNamespace(
            get=lambda key, default=None: {
                'MODE': 'tune', 'MOTORS': 'XY'}.get(key, default),
            error=ValueError)

        with self.assertRaisesRegex(ValueError, 'sensor_bandwidth'):
            self.subject.cmd_calibrate(gcmd)
        self.subject._ready_before_home.assert_not_called()


if __name__ == '__main__':
    unittest.main()
