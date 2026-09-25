"""Офлайн-проверка нижней опоры Z; read-only, без устройства и сети.

Модель аппаратной границы не доказывает надёжность StallGuard на механике.
Z_RECOVERY_KLIPPER_SOURCE позволяет дополнительно проверить закреплённый API.
"""
import importlib.util
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
            'stepper_z': dict(position_min=-5., position_max=255.,
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
        self.homed = ''
        self.shutdown = False
        self.phase, self.stats, self.paused, self.sd_active = 'idle', 'standby', False, False
        self.manual_active = False
        self.hits = [(120., .025), (5., .025)]
        self.moves, self.seeks, self.pauses, self.writes = [], [], [], []
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
        self.kin = NS(rails=[None, None, NS(get_steppers=lambda: [NS(get_name=lambda: 'stepper_z')])],
                      max_z_velocity=25., get_status=lambda _: dict(homed_axes=self.homed),
                      clear_homing_state=self.clear)
        self.toolhead = NS(max_velocity=300., max_accel=3000., square_corner_velocity=5.,
                           min_cruise_ratio=.5, get_kinematics=lambda: self.kin,
                           get_position=lambda: list(self.pos), set_position=self.set_position,
                           set_max_velocities=self.set_limits, get_last_move_time=lambda: 1.,
                           wait_moves=lambda: None, dwell=self.pauses.append, move=self.move)
        self.endstop = NS(add_stepper=Mock())
        self.enable = NS(motor_enable=Mock())
        self.objects = {
            'toolhead': self.toolhead,
            'gcode': NS(register_command=Mock()),
            'pins': NS(setup_pin=Mock(return_value=self.endstop)),
            'stepper_enable': NS(lookup_enable=lambda _: self.enable),
            'gcode_macro _TREED_OPERATION_STATE': NS(variables={}),
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
        self.extra = module.TreedZRecovery(Config(self, enabled=True, **config))
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

    def set_position(self, pos, homing_axes=''):
        self.pos[:] = pos
        self.homed = ''.join(sorted(set(self.homed + homing_axes)))

    def set_limits(self, *values):
        for name, value in zip(('max_velocity', 'max_accel', 'square_corner_velocity',
                                'min_cruise_ratio'), values):
            if value is not None:
                setattr(self.toolhead, name, value)

    def move(self, pos, speed):
        self.moves.append(list(pos))
        if len(self.moves) == self.fail_move:
            raise ValueError('ошибка отхода')
        assert pos[:2] == self.pos[:2] and pos[3] == self.pos[3]
        assert -5 <= pos[2] <= 255
        self.pos[:] = pos

    def run(self):
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
                rig.pos[2] = trigger[2] + overshoot
                return trigger

            def check_no_movement(self):
                return 'z_bottom' if self.zero else None

        with patch.object(homing, 'HomingMove', Move, create=True):
            self.extra.cmd_home(self.command)


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

    def test_two_contacts_and_clearance(self):
        rig = Rig()
        rig.run()
        self.assertEqual(rig.pos, [17., 29., 250., 4.])
        self.assertEqual(rig.seeks, [(-5., 255., 5.), (249.5, 255., 5.)])
        self.assertEqual([p[2] for p in rig.moves], [250., 250.])
        self.assertEqual(rig.pauses, [2., 2.])
        rig.endstop.add_stepper.assert_called_once()
        self.restored(rig, True)

    def test_print_preparation_allowed(self):
        rig = Rig()
        rig.phase, rig.stats, rig.sd_active = 'preparing', 'printing', True
        rig.run()
        self.restored(rig, True)

    def test_known_z_and_disabled_unknown(self):
        rig = Rig()
        rig.extra.enabled = False
        rig.homed = 'xyz'
        rig.run()
        self.assertFalse(rig.seeks or rig.moves or rig.writes)
        rig.homed = 'xy'
        with self.assertRaises(ValueError):
            rig.run()
        self.assertFalse(rig.seeks or rig.moves or rig.writes)

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

    def test_no_trigger_on_either_pass(self):
        for index in (0, 1):
            rig = Rig()
            rig.hits[index] = ValueError('No trigger after full movement')
            with self.assertRaises(ValueError):
                rig.run()
            self.assertTrue(rig.shutdown)
            self.assertEqual(len(rig.seeks), index + 1)
            self.assertEqual(len(rig.moves), index)
            self.restored(rig)

    def test_early_late_nonfinite_and_stuck(self):
        for travel in (0., 1., 4.49, 5.51, float('nan'), float('inf')):
            with self.subTest(travel=travel):
                rig = Rig()
                rig.hits[1] = (travel, 0.)
                with self.assertRaises(ValueError):
                    rig.run()
                self.assertEqual(len(rig.moves), 1)
                self.restored(rig)

    def test_tolerance_boundaries(self):
        for travel in (4.5, 5.5):
            rig = Rig()
            rig.hits[1] = (travel, 0.)
            rig.run()
            self.restored(rig, True)

    def test_retreat_failure_and_shutdown(self):
        for index in (1, 2):
            rig = Rig()
            rig.fail_move = index
            with self.assertRaises(ValueError):
                rig.run()
            self.restored(rig)
        rig = Rig()
        rig.toolhead.dwell = lambda _: rig.stop('MCU disconnected')
        with self.assertRaises(ValueError):
            rig.run()
        self.assertFalse(rig.seeks)
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
                       dict(max_seek=261), dict(tolerance=2.5), dict(bottom_position=256),
                       dict(verify_backoff_mm=260), dict(bottom_clearance_mm=260),
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
        self.assertIn('endstop_pin: probe:z_virtual_endstop', (profile / 'steppers.cfg').read_text(encoding='utf-8'))
        self.assertIn('[force_move]', (profile / 'probe_eddy_duo.cfg').read_text(encoding='utf-8'))
        self.assertIn('enabled: False', (profile / 'z_recovery.cfg').read_text(encoding='utf-8'))
        self.assertIn('z_recovery.cfg]', (ROOT / 'klipper/printer.cfg').read_text(encoding='utf-8'))
        loader = (ROOT / 'loader/steps/runtime-bootstrap.sh').read_text(encoding='utf-8')
        self.assertIn('klipper-host/treed_z_recovery.py; do', loader)
        self.assertEqual(loader.count("'/klippy/extras/treed_z_recovery.py'"), 2)

    # Блок 3: Реальный HomingMove закреплённого Klipper с моделируемыми MCU-счётчиками.
    @unittest.skipUnless(os.environ.get('Z_RECOVERY_KLIPPER_SOURCE'), 'upstream source not supplied')
    def test_upstream_trigger_differs_from_halt_and_target(self):
        source = Path(os.environ['Z_RECOVERY_KLIPPER_SOURCE']) / 'extras_homing.py'
        upstream_spec = importlib.util.spec_from_file_location('_z_real_homing', source)
        real = importlib.util.module_from_spec(upstream_spec)
        upstream_spec.loader.exec_module(real)
        for missing in (False, True):
            rig = Rig()
            rig._start = [17., 29., 249.5, 4.]
            rig.pos[:] = rig._start
            rig.moving = False
            # Ход 5 мм; остановка на 0.025 мм позже DIAG, цель ещё дальше.
            start = [46., -12., 249.5]
            trigger = [46., -12., 254.5]
            halt = [46., -12., 254.525]
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
                    self.assertAlmostEqual(rig.pos[2], 254.525)
            self.assertEqual([call.args[0] for call in rig.printer.send_event.call_args_list],
                             ['homing:homing_move_begin', 'homing:homing_move_end'])


if __name__ == '__main__':
    result = unittest.main(exit=False)
    if not result.result.wasSuccessful():
        sys.exit(1)
    print('Z_RECOVERY_CHECKS_PASSED')
