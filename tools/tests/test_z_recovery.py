"""Офлайн-проверка нижней опоры Z; read-only, без устройства и сети.

Модель аппаратной границы не доказывает надёжность StallGuard на механике.
Z_RECOVERY_KLIPPER_SOURCE позволяет дополнительно проверить закреплённый API.
"""
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch


# Блок 1: Загрузка extra без установки Klipper и модель его аппаратной границы.
ROOT = Path(__file__).resolve().parents[2]
package = ModuleType('_z_recovery_extras')
package.__path__ = [str(ROOT / 'klipper-host')]
sys.modules[package.__name__] = package
homing = ModuleType(package.__name__ + '.homing')
sys.modules[homing.__name__] = homing
spec = importlib.util.spec_from_file_location(
    package.__name__ + '.treed_z_recovery', ROOT / 'klipper-host/treed_z_recovery.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Config:
    def __init__(self, rig, **values):
        self.rig, self.values = rig, values

    def get_printer(self):
        return self.rig.printer

    def get(self, key, default=None):
        return self.values.get(key, default)

    def getfloat(self, key, default=None, above=None, minval=None, maxval=None):
        value = float(self.get(key, default))
        if ((above is not None and value <= above)
                or (minval is not None and value < minval)
                or (maxval is not None and value > maxval)):
            raise ValueError(key)
        return value

    getint = getfloat
    getboolean = get
    error = ValueError

    def getsection(self, name):
        return Config(self.rig, **{
            'stepper_z': dict(position_min=-5., position_max=203.,
                              endstop_pin='probe:z_virtual_endstop'),
            'printer': dict(kinematics='corexy'),
        }[name])


class Fields:
    def __init__(self):
        self.values = dict(sgt=9, en_pwm_mode=1, diag0_stall=0,
                           diag1_stall=0, tcoolthrs=37, thigh=11,
                           globalscaler=45, irun=27, ihold=13)

    def lookup_register(self, field):
        return 'CURRENT' if field in ('irun', 'ihold') else field

    def get_field(self, field):
        return self.values[field]

    def set_field(self, field, value):
        self.values[field] = value
        return tuple(self.values.items())


class Rig:
    def __init__(self, **config):
        self.pos = [17., 29., 0., 4.]
        self.physical_z = 0.
        self.mcu_offset_steps = 1234567
        self.contact_z = None
        self.kin_pos = list(self.pos)
        self.gcode_pos = list(self.pos)
        self.homed = ''
        self.shutdown = False
        self.phase, self.stats, self.paused, self.sd_active = 'idle', 'standby', False, False
        self.manual_active = False
        self.hits = [(205., .025)]
        self.moves, self.move_speeds, self.seeks, self.pauses, self.writes = [], [], [], [], []
        self.fail_move = None
        self.fields = Fields()
        self.saved_fields = self.fields.values.copy()
        self.current = NS(req_hold_current=2., get_current=lambda: (.9, .4, 2., 3.),
                          set_current=self.set_current)

        class DriverStatus:
            def __init__(inner):
                inner.current_helper = self.current

            def get_status(inner, now=None):
                return {}

        self.driver = NS(fields=self.fields, get_status=DriverStatus().get_status,
                         mcu_tmc=NS(set_register=self.write))
        self.stepper = NS(get_name=lambda: 'stepper_z', get_step_dist=lambda: .001,
                          get_mcu_position=lambda: round(self.physical_z / .001) + self.mcu_offset_steps)
        self.rail = NS(position_min=-5., position_max=203., get_steppers=lambda: [self.stepper])
        self.rail.get_range = lambda: (self.rail.position_min, self.rail.position_max)
        self.kin = NS(rails=[None, None, self.rail],
                      limits=[(0., 245.), (0., 245.), (1., -1.)],
                      axes_max=NS(x=245., y=245., z=203.),
                      max_z_velocity=25., get_status=lambda _: dict(homed_axes=self.homed),
                      clear_homing_state=self.clear, get_position=lambda: list(self.kin_pos))
        self.toolhead = NS(max_velocity=300., max_accel=3000., square_corner_velocity=5.,
                           min_cruise_ratio=.5, get_kinematics=lambda: self.kin,
                           Coord=lambda xyz: NS(x=xyz[0], y=xyz[1], z=xyz[2]),
                           get_position=lambda: list(self.pos), set_position=self.set_position,
                           set_max_velocities=self.set_limits, get_last_move_time=lambda: 1.,
                           wait_moves=lambda: None, dwell=self.pauses.append, move=self.move)
        self.endstop = NS(add_stepper=Mock())
        self.enable = NS(motor_enable=Mock(), register_state_callback=Mock())
        self.objects = {
            'toolhead': self.toolhead,
            'gcode': NS(register_command=Mock()),
            'gcode_move': NS(get_status=lambda _: dict(position=list(self.gcode_pos),
                                                       gcode_position=list(self.gcode_pos))),
            'pins': NS(setup_pin=Mock(return_value=self.endstop)),
            'stepper_enable': NS(lookup_enable=lambda _: self.enable),
            'gcode_macro _TREED_OPERATION_STATE': NS(variables={}),
            'gcode_macro _TREED_UI_CONTRACT': NS(variables={'axis_z_max': 203.}),
            'print_stats': NS(get_status=lambda _: dict(state=self.stats)),
            'pause_resume': NS(get_status=lambda _: dict(is_paused=self.paused),
                               is_paused=False, pause_command_sent=False),
            'virtual_sdcard': NS(is_active=lambda: self.sd_active),
            'manual_probe': NS(get_status=lambda _: dict(is_active=self.manual_active)),
        }
        self.printer = NS(lookup_object=lambda name, default=None: self.objects.get(name, default),
                          load_object=lambda *_: self.driver, register_event_handler=Mock(),
                          get_reactor=lambda: NS(monotonic=lambda: 0.), is_shutdown=lambda: self.shutdown,
                          get_state_message=lambda: ('ready', 'ready'), invoke_shutdown=self.stop,
                          config_error=ValueError, command_error=ValueError)
        config.setdefault('max_seek', 210)
        self.extra = module.TreedZRecovery(Config(self, **config))
        self.extra._connect()
        self.command = NS(get_command_parameters=lambda: {}, respond_info=Mock(), error=ValueError)

    def set_current(self, run, hold, time):
        self.current.req_hold_current = hold
        self.fields.values.update(globalscaler=90, irun=31, ihold=31)

    def write(self, reg, value, time):
        self.writes.append((reg, value))

    def stop(self, reason):
        self.shutdown = True

    def clear(self, axes):
        self.homed = ''.join(a for a in self.homed if a not in axes)
        if 'z' in axes:
            self.kin.limits[2] = (1., -1.)

    def set_position(self, pos, homing_axes=''):
        self.pos[:] = pos
        self.kin_pos[:] = pos
        self.gcode_pos[:] = pos
        self.homed = ''.join(sorted(set(self.homed + homing_axes)))
        if 'z' in homing_axes:
            self.kin.limits[2] = self.rail.get_range()

    def set_limits(self, *values):
        for name, value in zip(('max_velocity', 'max_accel', 'square_corner_velocity',
                                'min_cruise_ratio'), values):
            if value is not None:
                setattr(self.toolhead, name, value)

    def move(self, pos, speed):
        self.moves.append(list(pos))
        self.move_speeds.append(speed)
        if len(self.moves) == self.fail_move:
            raise ValueError('ошибка отхода')
        assert pos[:2] == self.pos[:2] and pos[3] == self.pos[3]
        if not self.rail.position_min <= pos[2] <= self.rail.position_max:
            raise ValueError('Move out of range')
        self.physical_z += pos[2] - self.pos[2]
        self.pos[:] = pos
        self.kin_pos[:] = pos

    def run(self, auto_remove=False):
        self.objects['gcode_macro _TREED_OPERATION_STATE'].variables['phase'] = self.phase
        rig = self

        class Move:
            def __init__(self, printer, endstops):
                assert endstops == [(rig.endstop, 'z_bottom')]
                self.zero = False

            def homing_move(self, target, speed, probe_pos=False):
                assert probe_pos and target[:2] == rig.pos[:2] and target[3] == rig.pos[3]
                rig.seeks.append((rig.pos[2], target[2], speed))
                hit = rig.hits.pop(0)
                rig.fields.values.update(en_pwm_mode=0, diag1_stall=1, tcoolthrs=0xfffff, thigh=0)
                if isinstance(hit, BaseException):
                    raise hit
                travel, overshoot = hit
                self.zero = travel == 0
                trigger = list(rig.pos)
                trigger[2] += travel
                rig.contact_z = rig.physical_z + travel
                rig.physical_z = rig.contact_z + overshoot
                rig.pos[2] = trigger[2] + overshoot
                return trigger

            def check_no_movement(self):
                return 'z_bottom' if self.zero else None

        with patch.object(homing, 'HomingMove', Move, create=True):
            command = self.extra.cmd_park_bottom if auto_remove else self.extra.cmd_home
            command(self.command)

    def finish_eddy(self, z0_physical=0., corrected_z=.25):
        self.extra.cmd_travel_begin(self.command)
        # Движение до Eddy и две смены системы координат не обнуляют MCU-счётчик.
        self.physical_z = z0_physical + corrected_z
        self.extra._set_z(100.)
        self.extra._set_z(corrected_z)
        self.extra.cmd_travel_apply(self.command)


# Блок 2: Успех и отказы; проверяем состояние, а не только текст ошибки.
class RecoveryTests(unittest.TestCase):
    def restored(self, rig, homed=False):
        self.assertEqual(rig.fields.values, rig.saved_fields)
        self.assertEqual(rig.current.req_hold_current, 2.)
        self.assertEqual((rig.toolhead.max_velocity, rig.toolhead.max_accel,
                          rig.toolhead.square_corner_velocity, rig.toolhead.min_cruise_ratio),
                         (300., 3000., 5., .5))
        self.assertEqual('z' in rig.homed, homed)
        self.assertFalse(rig.extra.running)

    def test_one_contact_and_clearance(self):
        rig = Rig()
        rig.run()
        self.assertEqual(rig.pos, [17., 29., 198., 4.])
        self.assertEqual(rig.extra.last_run['probes'][0]['trigger_mm'], 198.)
        gcode = rig.objects['gcode_move'].get_status(0)
        self.assertEqual((rig.toolhead.get_position()[2], rig.kin.get_position()[2],
                          gcode['position'][2], gcode['gcode_position'][2]),
                         (rig.extra.bottom - rig.extra.clearance,) * 4)
        self.assertEqual(rig.seeks, [(-7., 203., 5.)])
        self.assertEqual([p[2] for p in rig.moves], [198.])
        self.assertAlmostEqual(rig.contact_z - rig.physical_z, 5.)
        self.assertEqual(rig.pauses, [2.])
        self.assertEqual(len(rig.extra.last_run['probes']), 1)
        self.assertNotIn('second_travel_mm', rig.extra.last_run)
        self.assertIsNone(rig.extra.last_run['probes'][0]['failure_reason'])
        rig.endstop.add_stepper.assert_called_once()
        self.restored(rig, True)

    def test_contact_at_200_retreats_five_mm(self):
        rig = Rig()
        rig.hits[0] = (207., .025)
        rig.run()
        self.assertEqual(rig.extra.last_run['probes'][0]['trigger_mm'], 200.)
        self.assertEqual([p[2] for p in rig.moves], [198.])
        self.assertAlmostEqual(rig.contact_z - rig.physical_z, 5.)
        self.assertEqual(rig.pos[2], 198.)
        self.restored(rig, True)

    def test_print_preparation_allowed(self):
        rig = Rig()
        rig.phase, rig.stats, rig.sd_active = 'preparing', 'printing', True
        rig.run()
        self.restored(rig, True)

    def test_known_z_skips_recovery(self):
        rig = Rig()
        rig.homed = 'xyz'
        rig.run()
        self.assertFalse(rig.seeks or rig.moves or rig.writes)

    def test_auto_remove_forces_bottom_and_runs_five_cycles(self):
        rig = Rig()
        rig.phase, rig.stats, rig.sd_active = 'auto_remove', 'printing', True
        rig.homed = 'xyz'
        rig.kin.max_z_velocity = 100.
        rig.run(auto_remove=True)
        self.assertEqual(len(rig.seeks), 1)
        self.assertEqual([move[2] for move in rig.moves], [198.] + [173., 198.] * 5)
        self.assertEqual(rig.move_speeds, [5.] + [50.] * 10)
        self.assertEqual(rig.pos[2], 198.)
        self.assertEqual(rig.extra.last_run['auto_remove']['cycles'], 5)
        self.restored(rig, True)

    def test_auto_remove_requires_phase_diag_and_available_travel(self):
        rig = Rig()
        rig.kin.max_z_velocity = 100.
        with self.assertRaises(ValueError):
            rig.run(auto_remove=True)
        self.assertFalse(rig.seeks or rig.moves)

        rig = Rig()
        rig.phase, rig.kin.max_z_velocity = 'auto_remove', 100.
        rig.hits[0] = ValueError('No trigger after full movement')
        with self.assertRaises(ValueError):
            rig.run(auto_remove=True)
        self.assertFalse(rig.moves)

        rig = Rig()
        rig.phase, rig.kin.max_z_velocity = 'auto_remove', 100.
        rig.rail.position_min = 180.
        with self.assertRaisesRegex(ValueError, 'недостаточно 25 мм'):
            rig.run(auto_remove=True)
        self.assertEqual([move[2] for move in rig.moves], [198.])
        self.restored(rig, True)

    def test_auto_remove_stops_on_move_error(self):
        rig = Rig()
        rig.phase, rig.kin.max_z_velocity = 'auto_remove', 100.
        rig.fail_move = 2
        with self.assertRaisesRegex(ValueError, 'ошибка отхода'):
            rig.run(auto_remove=True)
        self.assertTrue(rig.shutdown)
        self.assertEqual([move[2] for move in rig.moves], [198., 173.])
        self.assertEqual(rig.extra.last_run['auto_remove']['cycles'], 0)
        self.restored(rig, True)

    def test_measured_limit_uses_physical_contact_after_eddy(self):
        for contact, start, origin, expected in (
                (175., 0., 1234567, 169.5), (175., 100., -1234567, 169.5),
                (175., 170., 0, 169.5), (203., 100., 1234567, 197.5),
                (209., 100., 1234567, 203.)):
            with self.subTest(contact=contact, start=start, origin=origin):
                rig = Rig()
                rig.physical_z, rig.mcu_offset_steps = start, origin
                rig.hits = [(contact - start, .025)]
                rig.run()
                self.assertEqual(rig.pos[2], 198.)
                self.assertEqual(rig.extra.z_travel['state'], 'pending_eddy')
                self.assertEqual(rig.rail.position_max, 198.)
                rig.finish_eddy(z0_physical=.5)
                self.assertAlmostEqual(rig.extra.z_travel['contact_z'], contact - .5)
                self.assertAlmostEqual(rig.extra.z_travel['z_max'], expected)
                self.assertAlmostEqual(rig.kin.limits[2][1], expected)
                self.assertAlmostEqual(rig.kin.axes_max.z, expected)
                self.assertEqual((rig.kin.axes_max.x, rig.kin.axes_max.y), (245., 245.))
                self.assertAlmostEqual(rig.objects['gcode_macro _TREED_UI_CONTRACT'].variables[
                    'axis_z_max'], expected)
                rig.extra._set_z(.25)
                self.assertAlmostEqual(rig.kin.limits[2][1], expected)
                rig.move([17., 29., expected, 4.], 5.)
                self.assertGreaterEqual(contact - rig.physical_z + 1.e-6, 5.)
                with self.assertRaisesRegex(ValueError, 'out of range'):
                    rig.move([17., 29., expected + .01, 4.], 5.)

    def test_repeated_eddy_keeps_contact_reference_and_limit_on_move_error(self):
        rig = Rig()
        rig.run()
        rig.finish_eddy()
        contact = rig.extra.z_travel['contact_mcu_mm']
        rig.run()  # Уже известная Z не ищет DIAG заново.
        self.assertEqual(len(rig.seeks), 1)
        rig.finish_eddy(z0_physical=.1)
        self.assertEqual(rig.extra.z_travel['contact_mcu_mm'], contact)
        self.assertAlmostEqual(rig.rail.position_max, 199.9)
        rig.extra._command_error()  # Ошибка обычного G1 не открывает прежние 203 мм.
        self.assertAlmostEqual(rig.rail.position_max, 199.9)
        snapshot = rig.extra.get_status(0)
        snapshot['z_travel'].clear()
        self.assertEqual(rig.extra.z_travel['state'], 'measured')

    def test_lost_reference_requires_new_bottom_before_eddy(self):
        for failure in ('motor_off', 'eddy_error', 'shutdown'):
            with self.subTest(failure=failure):
                rig = Rig()
                rig.run()
                rig.finish_eddy()
                if failure == 'motor_off':
                    rig.extra._motor_state(0., False)
                elif failure == 'eddy_error':
                    rig.extra.cmd_travel_begin(rig.command)
                    rig.extra._command_error()
                else:
                    rig.extra._invalidate_travel()
                self.assertEqual(rig.extra.z_travel, {})
                self.assertNotIn('z', rig.homed)
                self.assertEqual(rig.kin.limits[2], (1., -1.))
                self.assertEqual(rig.rail.position_max, 203.)
                with self.assertRaisesRegex(ValueError, 'опора DIAG'):
                    rig.extra.cmd_travel_begin(rig.command)
                rig.hits = [(10., .025)]
                rig.run()
                self.assertEqual(rig.seeks[-1], (-7., 203., 5.))
                self.assertEqual(rig.extra.z_travel['state'], 'pending_eddy')

    def test_invalid_travel_clears_z_without_fallback(self):
        for fault in ('no_begin', 'unknown', 'motor_epoch', 'step_distance', 'motor_during_wait',
                      'counter', 'nan_z', 'short', 'outside', 'shutdown', 'pause'):
            with self.subTest(fault=fault):
                rig = Rig()
                rig.run()
                if fault != 'no_begin':
                    rig.extra.cmd_travel_begin(rig.command)
                rig.physical_z = .25
                rig.extra._set_z(.25)
                if fault == 'unknown':
                    rig.clear('z')
                elif fault == 'motor_epoch':
                    rig.extra.motor_epoch += 1
                elif fault == 'motor_during_wait':
                    rig.toolhead.wait_moves = lambda: rig.extra._motor_state(0., False)
                elif fault == 'step_distance':
                    rig.stepper.get_step_dist = lambda: .002
                elif fault == 'counter':
                    rig.stepper.get_mcu_position = lambda: float('nan')
                elif fault == 'nan_z':
                    rig.pos[2] = float('nan')
                elif fault == 'short':
                    rig.physical_z = rig.contact_z
                elif fault == 'outside':
                    rig.pos[2], rig.physical_z = 10., rig.contact_z - 1.
                elif fault == 'shutdown':
                    rig.shutdown = True
                elif fault == 'pause':
                    rig.objects['pause_resume'].pause_command_sent = True
                with self.assertRaises(ValueError):
                    rig.extra.cmd_travel_apply(rig.command)
                self.assertEqual(rig.extra.z_travel, {})
                self.assertNotIn('z', rig.homed)
                self.assertEqual(rig.kin.limits[2], (1., -1.))

    def test_motor_off_during_retreat_or_restore_cannot_publish_reference(self):
        for phase in ('retreat', 'restore'):
            with self.subTest(phase=phase):
                rig = Rig()
                def wait():
                    ready = rig.moves if phase == 'retreat' else rig.extra.z_travel
                    if ready and not rig.extra.motor_epoch:
                        rig.extra._motor_state(0., False)
                rig.toolhead.wait_moves = wait
                with self.assertRaisesRegex(ValueError, 'мотор Z отключался'):
                    rig.run()
                self.assertEqual(rig.extra.z_travel, {})
                self.assertNotIn('z', rig.homed)
                self.restored(rig)

    def test_pause_transition_and_sgt_calibration(self):
        rig = Rig()
        rig.objects['pause_resume'].pause_command_sent = True
        with self.assertRaises(ValueError):
            rig.run()
        self.assertFalse(rig.seeks or rig.moves or rig.writes)
        rig = Rig()
        rig.objects['treed_sgt_executor'] = NS(running=True)
        with self.assertRaises(ValueError):
            rig.run()
        self.assertFalse(rig.seeks or rig.moves or rig.writes)
        rig = Rig()
        rig.toolhead.dwell = lambda _: setattr(rig.objects['pause_resume'], 'pause_command_sent', True)
        with self.assertRaises(ValueError):
            rig.run()
        self.assertFalse(rig.seeks or rig.moves)
        self.restored(rig)

    def test_busy_states_before_motion(self):
        for field, value in [('phase', 'printing'), ('phase', 'paused'), ('phase', 'calibrating'),
                             ('phase', 'auto_remove'), ('phase', 'invalid'), ('stats', 'printing'),
                             ('stats', 'paused'), ('stats', 'error'), ('paused', True),
                             ('sd_active', True), ('manual_active', True), ('shutdown', True)]:
            with self.subTest(field=field, value=value):
                rig = Rig()
                setattr(rig, field, value)
                with self.assertRaises(ValueError):
                    rig.run()
                self.assertFalse(rig.seeks or rig.moves or rig.writes)

    def test_no_trigger_fails_without_retry(self):
        rig = Rig()
        rig.hits[0] = ValueError('No trigger after full movement')
        with self.assertRaises(ValueError):
            rig.run()
        self.assertTrue(rig.shutdown)
        self.assertEqual(len(rig.seeks), 1)
        self.assertFalse(rig.moves)
        self.assertEqual(rig.extra.last_run['failure_reason'], 'homing_error')
        self.restored(rig)

    def test_invalid_first_probe_reasons(self):
        for travel, overshoot, reason in ((0., 0., 'no_movement'),
                                          (-1., 0., 'trigger_before_start'),
                                          (211., 0., 'trigger_after_bottom'),
                                          (120., -.1, 'halt_before_trigger'),
                                          (210., .1, 'halt_after_bottom'),
                                          (120., 6., 'overshoot_exceeds_clearance'),
                                          (float('nan'), 0., 'non_finite_trigger'),
                                          (float('inf'), 0., 'non_finite_trigger')):
            with self.subTest(reason=reason, travel=travel):
                rig = Rig()
                rig.hits[0] = (travel, overshoot)
                with self.assertRaisesRegex(ValueError, reason):
                    rig.run()
                self.assertEqual(len(rig.seeks), 1)
                self.assertFalse(rig.moves)
                probe = rig.extra.last_run['probes'][0]
                self.assertEqual(probe['failure_reason'], reason)
                self.assertEqual(rig.extra.last_run['failure_reason'], reason)
                json.dumps(rig.extra.last_run, allow_nan=False)
                self.restored(rig)

    def test_first_probe_boundary(self):
        for travel in (0.001, 210.):
            rig = Rig()
            rig.hits[0] = (travel, 0.)
            rig.run()
            self.restored(rig, True)

    def test_retreat_failure_and_shutdown(self):
        rig = Rig()
        rig.fail_move = 1
        with self.assertRaises(ValueError):
            rig.run()
        self.restored(rig)
        rig = Rig()
        rig.toolhead.dwell = lambda _: rig.stop('MCU disconnected')
        with self.assertRaises(ValueError):
            rig.run()
        self.assertFalse(rig.seeks)
        self.restored(rig)

    def test_final_position_sync_failure_clears_homing(self):
        rig = Rig()
        calls = 0

        def set_position(pos, homing_axes=''):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise ValueError('ошибка синхронизации Z')
            rig.set_position(pos, homing_axes)

        rig.toolhead.set_position = set_position
        with self.assertRaisesRegex(ValueError, 'ошибка синхронизации Z'):
            rig.run()
        self.restored(rig)

    def test_partial_write_and_restore_failure(self):
        rig = Rig()
        original = rig.current.set_current
        def fail_current(*args):
            original(*args)
            raise ValueError('SPI error')
        rig.current.set_current = fail_current
        with self.assertRaises(ValueError):
            rig.run()
        self.restored(rig)
        rig = Rig()
        rig.driver.mcu_tmc.set_register = Mock(side_effect=ValueError('SPI offline'))
        with self.assertRaises(ValueError), self.assertLogs(level='ERROR'):
            rig.run()
        self.assertTrue(rig.shutdown)
        self.restored(rig)

    def test_config_validation(self):
        for params in (dict(speed=float('nan')), dict(current=float('inf')),
                        dict(max_seek=211), dict(bottom_position=204),
                        dict(bottom_clearance_mm=208), dict(bottom_clearance_mm=210),
                       dict(sgt=64), dict(stallguard_pause=1), dict(current=4)):
            with self.subTest(params=params), self.assertRaises(ValueError):
                Rig(**params)

    def test_active_config_and_delivery(self):
        profile = ROOT / 'klipper/profiles/treed_v2_corexy_v1'
        core = (profile / 'macros_core.cfg').read_text(encoding='utf-8')
        hop = core.split('[gcode_macro _TREED_Z_HOP_BEFORE_XY]')[1].split('[gcode_macro')[0]
        self.assertIn('TREED_Z_HOME_BOTTOM', hop)
        self.assertNotIn('FORCE_MOVE', hop)
        self.assertNotIn('SET_KINEMATIC_POSITION', hop)
        self.assertIn('if target_z > current_z', hop)
        steppers = (profile / 'steppers.cfg').read_text(encoding='utf-8')
        self.assertIn('endstop_pin: probe:z_virtual_endstop', steppers)
        self.assertIn('position_max: 203', steppers)
        self.assertIn('[force_move]', (profile / 'probe_eddy_duo.cfg').read_text(encoding='utf-8'))
        recovery = (profile / 'z_recovery.cfg').read_text(encoding='utf-8')
        self.assertNotIn('enabled:', recovery)
        self.assertIn('bottom_position: 203', recovery)
        self.assertIn('max_seek: 210', recovery)
        self.assertNotIn('verify_backoff_mm', recovery)
        self.assertNotIn('tolerance:', recovery)
        printer = (profile / 'printer_base.cfg').read_text(encoding='utf-8')
        max_z_velocity = float(printer.split('max_z_velocity:')[1].split()[0])
        self.assertGreaterEqual(max_z_velocity, 50.)
        self.assertIn('variable_axis_z_max: 203.0', (profile / 'macros_ui_contract.cfg').read_text(encoding='utf-8'))
        eddy = (profile / 'probe_eddy_duo.cfg').read_text(encoding='utf-8')
        home = eddy.split('[gcode_macro _TREED_EDDY_HOME_Z]')[1].split('[gcode_macro')[0]
        correction = eddy.split('[gcode_macro SET_Z_FROM_PROBE]')[1].split('[gcode_macro')[0]
        self.assertLess(home.index('_TREED_Z_TRAVEL_BEGIN'), home.index('G28.1 Z'))
        self.assertLess(correction.index('_RELOAD_Z_OFFSET_FROM_PROBE'), correction.index('_TREED_Z_TRAVEL_APPLY'))
        self.assertLess(correction.index('_TREED_Z_TRAVEL_APPLY'), correction.index('G1 Z{cfg.z_hop'))
        self.assertIn('z_recovery.cfg]', (ROOT / 'klipper/printer.cfg').read_text(encoding='utf-8'))
        loader = (ROOT / 'loader/steps/runtime-bootstrap.sh').read_text(encoding='utf-8')
        self.assertIn('klipper-host/treed_z_recovery.py; do', loader)
        self.assertEqual(loader.count("'/klippy/extras/treed_z_recovery.py'"), 2)
        end_print = (profile / 'macros_print_flow.cfg').read_text(encoding='utf-8').split(
            '[gcode_macro END_PRINT]')[1]
        ordered = ["VALUE=\"'auto_remove'\"", 'TEMPERATURE_WAIT SENSOR=heater_bed MAXIMUM=40',
                   'TREED_Z_PARK_BOTTOM', 'M400', 'M84', "VALUE=\"'idle'\""]
        positions = [end_print.index(value) for value in ordered]
        self.assertEqual(positions, sorted(positions))
        cancel = (profile / 'macros_pause_resume.cfg').read_text(encoding='utf-8').split(
            '[gcode_macro CANCEL_PRINT]')[1].split('[gcode_macro', 1)[0]
        self.assertNotIn('TREED_Z_PARK_BOTTOM', cancel)

    # Блок 3: Реальный HomingMove закреплённого Klipper с моделируемыми MCU-счётчиками.
    @unittest.skipUnless(os.environ.get('Z_RECOVERY_KLIPPER_SOURCE'), 'upstream source not supplied')
    def test_upstream_corexy_enforces_limit_after_coordinate_reset(self):
        source = Path(os.environ['Z_RECOVERY_KLIPPER_SOURCE'])
        coord_spec = importlib.util.spec_from_file_location('_z_real_coord', source / 'gcode.py')
        coord_module = importlib.util.module_from_spec(coord_spec)
        coord_spec.loader.exec_module(coord_module)
        upstream_spec = importlib.util.spec_from_file_location(
            '_z_real_corexy', source / 'kinematics_corexy.py')
        real = importlib.util.module_from_spec(upstream_spec)
        with patch.dict(sys.modules, {'stepper': ModuleType('stepper')}):
            upstream_spec.loader.exec_module(real)
        rig = Rig()
        rig.toolhead.Coord = coord_module.Coord
        rig.kin.axes_max = coord_module.Coord([245., 245., 203.])
        rig.hits = [(180., .025)]
        rig.run()
        rig.finish_eddy()
        self.assertIsInstance(rig.kin.axes_max, coord_module.Coord)
        native = real.CoreXYKinematics.__new__(real.CoreXYKinematics)
        native.limits, native.axes_max = rig.kin.limits, rig.kin.axes_max
        native.axes_min = coord_module.Coord([0., 0., -5.])
        native.max_z_velocity, native.max_z_accel = 25., 100.
        rig.rail.set_position = lambda pos: None
        native.rails = [rig.rail] * 3
        native.set_position([17., 29., .25], 'z')
        self.assertEqual(native.get_status(0)['axis_maximum'].z, 175.)
        move = NS(end_pos=[17., 29., 175.], axes_d=[0., 0., 1.], move_d=1.,
                  limit_speed=Mock(), move_error=lambda *args: ValueError('out of range'))
        native.check_move(move)
        move.end_pos[2] = 175.01
        with self.assertRaisesRegex(ValueError, 'out of range'):
            native.check_move(move)

    @unittest.skipUnless(os.environ.get('Z_RECOVERY_KLIPPER_SOURCE'), 'upstream source not supplied')
    def test_upstream_trigger_differs_from_halt_and_target(self):
        source = Path(os.environ['Z_RECOVERY_KLIPPER_SOURCE']) / 'extras_homing.py'
        upstream_spec = importlib.util.spec_from_file_location('_z_real_homing', source)
        real = importlib.util.module_from_spec(upstream_spec)
        upstream_spec.loader.exec_module(real)
        for missing in (False, True):
            rig = Rig()
            rig._start = [17., 29., 197.5, 4.]
            rig.pos[:] = rig._start
            rig.moving = False
            # Ход 5 мм; остановка на 0.025 мм позже DIAG, цель ещё дальше.
            start = [46., -12., 197.5]
            trigger = [46., -12., 202.5]
            halt = [46., -12., 202.525]
            steppers = []
            for i, name in enumerate(('stepper_x', 'stepper_y', 'stepper_z')):
                def counter(cmd=None, i=i):
                    return round((cmd if cmd is not None else
                                  (halt[i] if rig.moving else start[i])) / .001)
                steppers.append(NS(
                    get_name=lambda name=name: name, get_step_dist=lambda: .001,
                    get_mcu_position=counter, mcu_to_commanded_position=lambda value: value * .001,
                    get_commanded_position=lambda i=i: start[i],
                    get_past_mcu_position=lambda t, i=i: round(trigger[i] / .001),
                    calc_position_from_coord=lambda p, i=i: (p[0]+p[1], p[0]-p[1], p[2])[i]))
            rig.endstop.get_steppers = lambda: steppers[2:]
            rig.endstop.home_start = Mock(return_value=object())
            rig.endstop.home_wait = lambda _: 0. if missing else 1.
            rig.kin.get_steppers = lambda: steppers
            rig.kin.calc_position = lambda p: [
                (p['stepper_x'] + p['stepper_y']) / 2.,
                (p['stepper_x'] - p['stepper_y']) / 2., p['stepper_z']]
            rig.toolhead.flush_step_generation = lambda: None
            def drip(target, speed, completion):
                rig.moving = True
                rig.pos[:] = target
            rig.toolhead.drip_move = drip
            rig.printer.send_event = Mock()
            rig.printer.get_start_args = lambda: {}
            with patch.object(module, 'homing', real):
                if missing:
                    with self.assertRaises(ValueError):
                        rig.extra._seek()
                    self.assertTrue(rig.shutdown)
                else:
                    travel, overshoot = rig.extra._seek()
                    self.assertAlmostEqual(travel, 5.)
                    self.assertAlmostEqual(overshoot, .025)
                    self.assertAlmostEqual(rig.pos[2], 202.525)
            self.assertEqual([call.args[0] for call in rig.printer.send_event.call_args_list],
                             ['homing:homing_move_begin', 'homing:homing_move_end'])


if __name__ == '__main__':
    result = unittest.main(exit=False)
    if not result.result.wasSuccessful():
        sys.exit(1)
    print('Z_RECOVERY_CHECKS_PASSED')
