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

    def test_circle_command_caps_defaults_and_rejects_explicit_overspeed(self):
        subject = self.subject
        subject.state = 'idle'
        subject.phase_enabled = False
        subject.profile = self._valid_profile()
        subject.profile_state = 'saved'
        subject.reactor = types.SimpleNamespace(
            monotonic=lambda: 0., NOW=0., register_timer=Mock(return_value=1))
        subject.get_status = Mock(return_value={'phase_supported': True})
        subject._ready = Mock()
        subject._check_base_tables = Mock()
        subject._verify_circle_tables = Mock()
        subject._circle_geometry = Mock(return_value=((120., 120., 25.), 30.))
        subject.mcu_builds = {}
        subject.xy_accel = 500.
        steppers = [types.SimpleNamespace(get_name=lambda name=name: name)
                    for name in calibration.XY_MOTORS]
        toolhead = types.SimpleNamespace(
            square_corner_velocity=5.,
            get_max_velocity=lambda: (100., 500.),
            set_max_velocities=Mock(),
            get_kinematics=lambda: types.SimpleNamespace(get_steppers=lambda: steppers))
        subject.printer = types.SimpleNamespace(
            get_start_args=lambda: {'software_version': 'test'},
            lookup_object=lambda name, default=None: {
                'toolhead': toolhead,
                'adxl345': types.SimpleNamespace(start_internal_client=Mock())
            }.get(name, default))

        def command(speed=None):
            return types.SimpleNamespace(
                get=lambda key, default=None: speed if key == 'SPEEDS' else default,
                get_int=lambda key, default=None, **kwargs: default,
                get_float=lambda key, default=None, **kwargs: default,
                error=ValueError, respond_info=Mock())

        with self.assertRaisesRegex(ValueError, 'verified profile'):
            subject.cmd_circle_compare(command('50'))
        with self.assertRaisesRegex(ValueError, 'step history'):
            subject.cmd_circle_compare(command('1'))
        toolhead.set_max_velocities.assert_not_called()
        subject.cmd_circle_compare(command())
        self.assertEqual(subject.circle_speeds, (35.,))
        self.assertEqual(subject.circle_directions, ('CW', 'CCW'))
        self.assertEqual(subject.circle_repeats, 3)
        self.assertEqual(subject.circle_report['segments'], 128)
        self.assertEqual(subject.circle_report['profile_source'], 'saved')
        self.assertEqual(subject.circle_report['sector_count'], 16)
        toolhead.set_max_velocities.assert_called_once_with(35., 500., None, None)
        subject.reactor.register_timer.assert_called_once()
        with tempfile.TemporaryDirectory() as directory:
            subject.circle_report_path = str(Path(directory) / 'circle.json')
            subject._write_circle_report()
            report = json.loads(Path(subject.circle_report_path).read_text(
                encoding='utf-8'))
            self.assertEqual(report['profile_fingerprint'],
                             subject.circle_report['profile_fingerprint'])
            self.assertEqual(report['config_fingerprint'], 'runtime-config')
            self.assertEqual(report['passes'], [])

    def test_circle_geometry_uses_live_limits_and_never_crosses_margin(self):
        subject = self.subject
        subject.xy_margin = 12.
        subject.z_low, subject.z_high = 25., 50.
        subject._ready = Mock(return_value={
            'axis_minimum': (10., -20., -5.),
            'axis_maximum': (210., 160., 255.)})
        center, radius = subject._circle_geometry(None)
        self.assertEqual(center, (110., 70., 25.))
        self.assertAlmostEqual(radius, .7 * 78.)
        for direction in (True, False):
            self.assertTrue(all(22. <= x <= 198. and -8. <= y <= 148.
                                for x, y, _ in calibration.motor_math.circle_points(
                                    center, radius, direction)))
        with self.assertRaisesRegex(ValueError, 'circle_radius_out_of_bounds'):
            subject._circle_geometry(79.)

    def test_circle_off_on_uses_exact_same_queued_points(self):
        subject = self.subject
        subject._ready = Mock()
        subject._verify_circle_tables = Mock()
        subject.circle_profile = self._valid_profile()
        subject.z_speeds = (2.,)
        subject.circle_center = (100., 100., 25.)
        subject.circle_radius = 30.
        points = calibration.motor_math.circle_points(
            subject.circle_center, subject.circle_radius, True)
        subject.circle_paths = {'CW': points}
        subject.cancel_requested = False
        subject.printer = types.SimpleNamespace(is_shutdown=lambda: False)
        section = types.SimpleNamespace(
            getint=lambda key, default=None: 16 if key == 'microsteps' else default)
        subject.config = types.SimpleNamespace(getsection=lambda name: section)
        stepper_a = types.SimpleNamespace(
            get_mcu_position=lambda: 0, get_step_dist=lambda: .01,
            get_dir_inverted=lambda: (True,),
            get_past_mcu_position=lambda time: 10,
            get_rotation_distance=lambda: (40., 3200.))
        stepper_b = types.SimpleNamespace(
            get_mcu_position=lambda: 0, get_step_dist=lambda: .01,
            get_dir_inverted=lambda: (False,),
            get_past_mcu_position=lambda time: 0,
            get_rotation_distance=lambda: (40., 3200.))
        subject.kin_steppers = {'stepper_x': stepper_a, 'stepper_y': stepper_b}
        subject.toolhead = types.SimpleNamespace(
            get_position=lambda: points[0], manual_move=Mock(), wait_moves=Mock(),
            dwell=Mock(), get_last_move_time=Mock(side_effect=(0., 1., 2., 3.)))
        client = types.SimpleNamespace(
            msgs=[], finish_measurements=Mock(), get_samples=Mock(side_effect=[
                [(0.5, 0., 0., 0.)], [(2.5, 0., 0., 0.)]]))
        subject.chip = types.SimpleNamespace(start_internal_client=lambda: client)
        fake = {'total': {}, 'sectors': [
            {'actual_speed_mm_s': 50.} for _ in range(16)]}
        with patch.object(calibration.motor_math, 'circle_analysis', return_value=fake) as analysis:
            subject._run_circle_pass(50., 'CW', 1, False)
            subject._run_circle_pass(50., 'CW', 1, True)
        for call in analysis.call_args_list:
            self.assertEqual(call.args[0][0][6:8],
                             (points[0][0] + .05, points[0][1] + .05))
        actual = [call.args[0] for call in subject.toolhead.manual_move.call_args_list
                  if call.args[0] in points]
        self.assertEqual(actual, points[1:] + points[1:])
        self.assertIs(subject.circle_paths['CW'], points)

    def test_circle_flow_balances_order_and_compares_each_sector(self):
        subject = self.subject
        subject.circle_profile = self._valid_profile()
        subject.circle_speeds = (50.,)
        subject.circle_directions = ('CW', 'CCW')
        subject.circle_repeats = 3

        def measurement(value):
            metric = {'rms_accel_mm_s2': value,
                      'peak_accel_mm_s2': value * 2.,
                      'vibration_energy_mm2_s4': value * value,
                      'harmonics': {}}
            return {'total': metric, 'sectors': [metric] * 16}

        sequence = {'CW': [], 'CCW': []}
        flow = subject._circle_flow()
        action = next(flow)
        while True:
            try:
                if action[0] == 'tables':
                    action = flow.send(None)
                else:
                    _, speed, direction, repeat, enabled = action
                    self.assertEqual(speed, 50.)
                    sequence[direction].append(enabled)
                    action = flow.send(measurement(15. if enabled else 20.))
            except StopIteration as done:
                comparisons = done.value
                break
        self.assertEqual(sequence['CW'], [False, True, True, False, False, True])
        self.assertEqual(sequence['CW'], sequence['CCW'])
        self.assertEqual(len(comparisons), 2)
        self.assertEqual(len(comparisons[0]['sectors']), 16)
        self.assertEqual(comparisons[0]['total']['rms_accel_mm_s2']
                         ['relative_delta_percent'], -25.)

    def test_circle_cancel_restores_stock_and_unknown_state_fails(self):
        subject = self.subject
        subject.mode = 'circle'
        subject.state = 'running'
        subject.stage = 'comparing'
        subject.timer = None
        subject.old_accel = 500.
        subject.circle_old_velocity = 100.
        subject.current_tables = {'stepper_x': {'changed': 1},
                                  'stepper_y': self.subject.base_tables['stepper_y']}
        subject.phase_enabled = True
        subject.circle_report = {'comparisons': []}
        subject._write_circle_report = Mock()
        subject._verify_circle_tables = Mock()
        subject._switch_tables = Mock(side_effect=lambda tables: setattr(
            subject, 'current_tables', tables))
        subject.toolhead = types.SimpleNamespace(set_max_velocities=Mock(),
                                                 wait_moves=Mock())
        shutdown = [False]
        subject.printer = types.SimpleNamespace(
            is_shutdown=lambda: shutdown[0],
            invoke_shutdown=Mock(side_effect=lambda reason: shutdown.__setitem__(0, True)))
        subject.gcode = types.SimpleNamespace(respond_info=Mock())
        subject._finish('cancelled')
        self.assertEqual(subject.state, 'cancelled')
        self.assertFalse(subject.phase_enabled)
        self.assertEqual(subject.current_tables, subject.base_tables)
        subject.toolhead.wait_moves.assert_called_once()
        subject.toolhead.set_max_velocities.assert_called_once_with(
            100., 500., None, None)
        self.assertEqual(subject.circle_report['state'], 'cancelled')
        subject.old_accel = 500.
        subject.circle_old_velocity = 100.
        subject.phase_enabled = True
        subject._verify_circle_tables.side_effect = ValueError('readback')
        subject._finish('cancelled')
        self.assertEqual(subject.state, 'failed')
        self.assertEqual(subject.phase_reason, 'tmc_state_unknown')
        subject.printer.invoke_shutdown.assert_called_once()

    def test_circle_switch_never_jogs_to_find_safe_phase(self):
        subject = self.subject
        subject.current_tables = subject.base_tables
        subject.drivers = {'stepper_x': types.SimpleNamespace(
            get_register_raw=lambda name: {'spi_status': 0, 'data': 760})}
        subject.toolhead = types.SimpleNamespace(
            wait_moves=Mock(), manual_move=Mock())
        with self.assertRaisesRegex(ValueError, 'requires_motion'):
            subject._align_motor('stepper_x', wave.make_table(), allow_jog=False)
        subject.toolhead.manual_move.assert_not_called()

    def test_circle_state_machine_switches_only_without_jog(self):
        subject = self.subject
        subject.mode = 'circle'
        subject.state = 'running'
        subject.stage = 'comparing'
        subject.phase_enabled = False
        subject.cancel_requested = False
        subject.circle_profile = self._valid_profile()
        subject.flow = (action for action in [
            ('tables', subject.circle_profile['tables'], True)])
        subject.flow_result = None
        subject._switch_tables = Mock()
        subject._verify_circle_tables = Mock()
        subject.reactor = types.SimpleNamespace(monotonic=lambda: 1.)
        result = subject._next(1.)
        self.assertEqual(result, 1.05)
        subject._switch_tables.assert_called_once_with(
            subject.circle_profile['tables'], allow_jog=False)
        subject._verify_circle_tables.assert_called_once()
        self.assertTrue(subject.phase_enabled)

    def test_circle_retry_records_invalid_attempt(self):
        subject = self.subject
        subject.circle_report = {'invalid_passes': []}
        subject.gcode = types.SimpleNamespace(respond_info=Mock())
        subject._run_circle_pass = Mock(side_effect=[
            calibration.motor_math.MeasurementError('dropped_samples: gap'),
            {'total': {}, 'sectors': []}])
        row = subject._run_circle_with_retry(50., 'CW', 1, False)
        self.assertEqual(row['retry_count'], 1)
        self.assertEqual(len(subject.circle_report['invalid_passes']), 1)
        self.assertEqual(subject.circle_report['invalid_passes'][0]['attempt'], 1)


if __name__ == '__main__':
    unittest.main()
