# ==========================================
# TEST: TreeD motor calibration state
# ==========================================
# Назначение: профиль, отмена и атомарная запись.
# Контур: локальный, без принтера и внешних сервисов.
import importlib.util
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
            motor: wave.make_table() for motor in calibration.XY_MOTORS}

    def test_profile_invalidated_by_firmware_or_config_change(self):
        profile = {
            'schema': 1, 'algorithm': calibration.ALGORITHM,
            'build_id': self.subject.build_id,
            'config_fingerprint': self.subject.config_fingerprint,
            'coefficients': {motor: {'a2': 0., 'p2': 0.,
                                    'a4': 0., 'p4': 0.}
                             for motor in calibration.XY_MOTORS},
            'tables': self.subject.base_tables,
            'limits': {'max_velocity': 35., 'max_accel': 500.},
            'verified': {'accepted': True}}
        self.subject._validate_profile(profile)
        profile['build_id'] = 'older-mcu-build'
        with self.assertRaisesRegex(ValueError, 'stale'):
            self.subject._validate_profile(profile)
        profile['build_id'] = self.subject.build_id
        profile['config_fingerprint'] = 'older-config'
        with self.assertRaisesRegex(ValueError, 'stale'):
            self.subject._validate_profile(profile)

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


if __name__ == '__main__':
    unittest.main()
