"""Офлайн-проверки исполнителя SGT: без принтера, сети и движения.

Контур: модель API Klipper; реальный HomingMove дополнительно проверяется при
SGT_KLIPPER_SOURCE=<каталог extras закреплённого Klipper>.
"""
import importlib.util
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import treed_sgt_calibration as core

# Блок 1: Минимальный загрузчик extras и модель аппаратной границы.
package = ModuleType('_sgt_test_extras')
package.__path__ = [str(ROOT / 'klipper-host')]
sys.modules[package.__name__] = package
sys.modules[package.__name__ + '.treed_sgt_calibration'] = core
homing = ModuleType(package.__name__ + '.homing')
sys.modules[homing.__name__] = homing
spec = importlib.util.spec_from_file_location(
    package.__name__ + '.treed_sgt_executor', ROOT / 'klipper-host/treed_sgt_executor.py')
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


class Command:
    def __init__(self, **params):
        self.params = dict(AXIS='X', SGT_MIN='0', SGT_MAX='0',
                           TOLERANCE='0.5', Z_MIN='5', PAIRS='2')
        self.params.update(params)
        self.messages = []

    def get(self, key):
        return self.params[key]

    def get_int(self, key, default=None):
        return int(self.params.get(key, default))

    def get_float(self, key, default=None, above=None):
        value = float(self.params.get(key, default))
        if above is not None and value <= above:
            raise ValueError(key)
        return value

    def get_command_parameters(self):
        return self.params

    def respond_info(self, text):
        self.messages.append(text)

    error = ValueError


class Fields:
    def __init__(self):
        self.values = dict(sgt=7, en_pwm_mode=0, diag0_stall=0,
                           diag1_stall=0, tcoolthrs=0, thigh=0)

    def lookup_register(self, field):
        return field if field in self.values else None

    def get_field(self, field):
        return self.values[field]

    def set_field(self, field, value):
        self.values[field] = value
        return value


class Rig:
    def __init__(self, axis='X'):
        self.index = 'XY'.index(axis)
        self.stop = 0. if axis == 'X' else 245.
        self.start = 10. if axis == 'X' else 235.
        self.pos = [10., 235., 10., 0.]
        self.shutdown = False
        self.hits = []
        self.moves = []
        self.speeds = []
        self.homed = 'xyz'
        self.fields = Fields()
        self.driver = NS(fields=self.fields, mcu_tmc=Mock())
        self.hi = NS(position_endstop=self.stop, positive_dir=axis == 'Y',
                     speed=37., retract_speed=13.)
        self.endstop = NS(get_steppers=lambda: [
            NS(get_name=lambda: 'stepper_x'), NS(get_name=lambda: 'stepper_y')])
        self.rail = NS(get_homing_info=lambda: self.hi,
                       get_range=lambda: (0., 245.),
                       get_endstops=lambda: [(self.endstop, 'axis')])
        self.kin = NS(rails=[self.rail] * 3,
                      get_status=lambda _: {'homed_axes': self.homed},
                      clear_homing_state=Mock())
        self.toolhead = NS(
            get_kinematics=lambda: self.kin, get_position=lambda: list(self.pos),
            get_max_velocity=lambda: (200., 1000.),
            wait_moves=Mock(), dwell=Mock(), get_last_move_time=lambda: 123.,
            manual_move=self.move)
        self.settings = {'printer': {'kinematics': 'corexy'},
                         'stepper_x': {'endstop_pin': 'tmc5160_stepper_x:virtual_endstop'},
                         'stepper_y': {'endstop_pin': 'tmc5160_stepper_y:virtual_endstop'}}
        self.state = 'standby'
        self.paused = False
        self.active = False
        self.variables = dict(xy_backoff_mm=10., stallguard_pause_ms=2000.)
        self.objects = {
            'toolhead': self.toolhead, 'gcode': Mock(),
            'print_stats': NS(get_status=lambda _: {'state': self.state}),
            'pause_resume': NS(get_status=lambda _: {'is_paused': self.paused}),
            'virtual_sdcard': NS(is_active=lambda: self.active),
            'configfile': NS(get_status=lambda _: {'settings': self.settings}),
            'gcode_macro G28': NS(variables=self.variables),
            'tmc5160 stepper_x': self.driver, 'tmc5160 stepper_y': self.driver}
        self.printer = NS(lookup_object=self.objects.__getitem__,
                          get_reactor=lambda: NS(monotonic=lambda: 1.),
                          is_shutdown=lambda: self.shutdown,
                          invoke_shutdown=self.stop_printer)
        self.executor = adapter.TreedSgtExecutor(NS(get_printer=lambda: self.printer))
        self.command = Command(AXIS=axis)
        self.values = []
        self.no_movement = None

    def move(self, pos, speed):
        self.moves.append(list(pos))
        self.speeds.append(speed)
        self.pos[:] = pos

    def stop_printer(self, reason):
        self.shutdown = True

    def homing_move(self, target, speed, probe_pos=False):
        assert probe_pos is True
        assert target[self.index] == self.stop
        self.hits.append(list(target))
        self.speeds.append(speed)
        value = self.values.pop(0) if self.values else self.stop
        if isinstance(value, Exception):
            raise value
        self.pos[self.index] = value
        return list(self.pos)

    def run(self):
        move = NS(homing_move=self.homing_move,
                  check_no_movement=lambda: self.no_movement)
        with patch.object(homing, 'HomingMove', create=True, return_value=move) as factory:
            self.executor.cmd_calibrate(self.command)
            factory.assert_called_with(self.printer, [(self.endstop, 'axis')])


# Блок 2: Допуски, восстановление и запрет рекомендаций после отказов.
class ExecutorTests(unittest.TestCase):
    def test_both_axes_actual_coordinates_restore_live_sgt(self):
        for axis in ('X', 'Y'):
            with self.subTest(axis=axis):
                rig = Rig(axis)
                saved = dict(rig.fields.values)
                rig.run()
                self.assertEqual(rig.fields.values, saved)
                self.assertEqual(len(rig.hits), 4)
                self.assertEqual(rig.pos[rig.index], rig.start)
                self.assertTrue(all(s in (37., 13.) for s in rig.speeds))
                self.assertIn('рекомендован 0', ' '.join(rig.command.messages))
                rig.kin.clear_homing_state.assert_called_once_with('xy')

    def test_early_first_hit_skips_second_without_fabricated_pair(self):
        rig = Rig()
        rig.values = [2.]
        rig.run()
        self.assertEqual(len(rig.hits), 1)
        self.assertIn('early_trigger, пар=0', ' '.join(rig.command.messages))
        self.assertNotIn('рекомендован', ' '.join(rig.command.messages))

    def test_actual_trigger_pairs_are_not_target_coordinates(self):
        rig = Rig()
        rig.values = [.2, .1, .2, .1]
        rig.run()
        self.assertIn('DIAG 0.200000 / 0.100000', ' '.join(rig.command.messages))

    def test_fault_after_good_candidate_discards_result(self):
        rig = Rig()
        rig.command.params['SGT_MAX'] = '1'
        rig.values = [0.] * 4 + [ValueError('No trigger')]
        with self.assertRaisesRegex(ValueError, 'не восстановлен'):
            rig.run()
        self.assertTrue(rig.shutdown)
        self.assertEqual(rig.fields.get_field('sgt'), 7)
        self.assertEqual(len(rig.moves), 5)  # Нет отхода после сбоя пятой пробы.
        self.assertNotIn('рекомендован', ' '.join(rig.command.messages))

    def test_late_or_invalid_hit_aborts_before_second_probe(self):
        for value in (-1., float('nan'), float('inf')):
            rig = Rig()
            rig.values = [value]
            with self.subTest(value=value), self.assertRaises(ValueError):
                rig.run()
            self.assertEqual(len(rig.hits), 1)
            self.assertEqual(len(rig.moves), 1)
            self.assertEqual(rig.fields.get_field('sgt'), 7)

    def test_diag_without_motion_is_terminal(self):
        rig = Rig()
        rig.no_movement = 'axis'
        with self.assertRaisesRegex(ValueError, 'без движения'):
            rig.run()
        self.assertEqual(len(rig.hits), 1)

    def test_restore_failure_never_publishes_recommendation(self):
        rig = Rig()
        def write(reg, value, time):
            if reg == 'sgt' and value == 7:
                raise OSError('SPI lost')
        rig.driver.mcu_tmc.set_register.side_effect = write
        with self.assertLogs(level='ERROR'), self.assertRaisesRegex(ValueError, 'не восстановлен'):
            rig.run()
        self.assertTrue(rig.shutdown)
        self.assertFalse(rig.executor.running)
        self.assertNotIn('рекомендован', ' '.join(rig.command.messages))

    def test_emergency_between_hits_prevents_next_motion(self):
        rig = Rig()
        def report(text):
            rig.command.messages.append(text)
            if 'DIAG' in text:
                rig.shutdown = True
        rig.command.respond_info = report
        with self.assertRaises(ValueError):
            rig.run()
        self.assertEqual(len(rig.hits), 2)
        self.assertEqual(len(rig.moves), 2)
        self.assertNotIn('рекомендован', ' '.join(rig.command.messages))

    def test_reject_busy_and_unknown_home_before_writes(self):
        for state in ('printing', 'paused', 'unhomed', 'pause_resume', 'sd_active'):
            rig = Rig()
            if state == 'unhomed':
                rig.homed = 'xy'
            elif state == 'pause_resume':
                rig.paused = True
            elif state == 'sd_active':
                rig.active = True
            else:
                rig.state = state
            with self.subTest(state=state), self.assertRaises(ValueError):
                rig.run()
            self.assertEqual(rig.moves, [])
            rig.driver.mcu_tmc.set_register.assert_not_called()

    def test_reject_invalid_parameters_before_motion(self):
        for params in (dict(AXIS='Z'), dict(SGT_MIN='-65'), dict(SGT_MAX='64'),
                       dict(TOLERANCE='nan'), dict(TOLERANCE='5'),
                       dict(Z_MIN='nan'), dict(Z_MIN='20'), dict(PAIRS='1'),
                       dict(ERROR_BUDGET='inf'), dict(SPEED='100')):
            rig = Rig()
            rig.command.params.update(params)
            with self.subTest(params=params), self.assertRaises(ValueError):
                rig.run()
            self.assertEqual(rig.moves, [])

    def test_reject_unsupported_or_outside_corridor(self):
        for condition in ('kinematics', 'endstop', 'corridor', 'pause', 'backoff'):
            rig = Rig()
            if condition == 'kinematics':
                rig.settings['printer']['kinematics'] = 'cartesian'
            elif condition == 'endstop':
                rig.settings['stepper_x']['endstop_pin'] = '^PG9'
            elif condition == 'corridor':
                rig.pos[0] = 50.
            elif condition == 'pause':
                rig.variables['stallguard_pause_ms'] = float('nan')
            else:
                rig.variables['xy_backoff_mm'] = 300.
            with self.subTest(condition=condition), self.assertRaises(ValueError):
                rig.run()
            self.assertEqual(rig.moves, [])

    def test_partial_homing_setup_restores_cached_diag_fields(self):
        rig = Rig()
        def failed_move(*args, **kwargs):
            rig.fields.set_field('diag1_stall', 1)
            rig.fields.set_field('tcoolthrs', 0xfffff)
            raise OSError('home_start failed')
        rig.homing_move = failed_move
        with self.assertRaises(ValueError):
            rig.run()
        self.assertEqual(rig.fields.get_field('diag1_stall'), 0)
        self.assertEqual(rig.fields.get_field('tcoolthrs'), 0)
        self.assertTrue(rig.shutdown)

    def test_single_motor_diag_is_rejected(self):
        rig = Rig()
        rig.endstop.get_steppers = lambda: [NS(get_name=lambda: 'stepper_x')]
        with self.assertRaisesRegex(ValueError, 'оба двигателя'):
            rig.run()

    def test_velocity_cap_is_not_silently_used_as_homing_speed(self):
        rig = Rig()
        rig.toolhead.get_max_velocity = lambda: (10., 1000.)
        with self.assertRaisesRegex(ValueError, 'max_velocity'):
            rig.run()
        self.assertEqual(rig.moves, [])

    def test_early_trigger_cannot_hide_cancellation(self):
        flag = [False]
        def probe(sgt):
            flag[0] = True
            raise core.EarlyTrigger()
        result = core.calibrate_sgt(
            core.CalibrationConfig(0, 1, 0., .5, -1), probe, lambda: flag[0])
        self.assertEqual(result.status, 'aborted')
        self.assertIsNone(result.recommended_sgt)


# Блок 3: Доставка и совместимость с реальным закреплённым HomingMove.
class IntegrationTests(unittest.TestCase):
    def test_loader_delivers_core_and_executor_and_excludes_both(self):
        source = (ROOT / 'loader/steps/runtime-bootstrap.sh').read_text(encoding='utf-8')
        for path in ('tools/treed_sgt_calibration.py', 'klipper-host/treed_sgt_executor.py'):
            self.assertTrue((ROOT / path).is_file())
            self.assertIn(path, source)
            self.assertEqual(source.count("'/klippy/extras/" + Path(path).name + "'"), 2)
        self.assertIn('${sgt_source##*/}', source)
        self.assertIn('missing ${sgt_source}', source)
        self.assertNotIn('[treed_sgt_executor]', (ROOT / 'klipper/printer.cfg').read_text(encoding='utf-8'))

    @unittest.skipUnless(os.environ.get('SGT_KLIPPER_SOURCE'), 'Нет локальных исходников Klipper')
    def test_real_homing_move_uses_both_motor_counters_before_rebase(self):
        source = Path(os.environ['SGT_KLIPPER_SOURCE']) / 'homing.py'
        spec = importlib.util.spec_from_file_location('_pinned_homing', source)
        real = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(real)
        for axis in (0, 1):
            with self.subTest(axis=axis):
                self.check_real_homing(real, axis)

    def check_real_homing(self, real, axis):
        # A=X+Y, B=X-Y. Trigger и halt отличаются: измерять нужно trigger.
        start = [10., 235., 10., 0.]
        hit = [.125, 235., 10., 0.] if axis == 0 else [10., 244.875, 10., 0.]
        halt = list(hit)
        halt[axis] += -.025 if axis == 0 else .025
        def motors(pos):
            return [pos[0] + pos[1], pos[0] - pos[1], pos[2]]
        before, triggered, stopped = map(motors, (start, hit, halt))
        moving = [False]
        steppers = []
        for i, name in enumerate(('stepper_x', 'stepper_y', 'stepper_z')):
            def counter(cmd=None, i=i):
                return round((cmd if cmd is not None else (stopped[i] if moving[0] else before[i])) / .001)
            steppers.append(NS(
                get_name=lambda name=name: name, get_step_dist=lambda: .001,
                get_mcu_position=counter, mcu_to_commanded_position=lambda v: v * .001,
                get_commanded_position=lambda i=i: before[i],
                get_past_mcu_position=lambda t, i=i: round(triggered[i] / .001),
                calc_position_from_coord=lambda pos, i=i: motors(pos)[i]))
        completion = object()
        endstop = NS(get_steppers=lambda: steppers[:2],
                     home_start=Mock(return_value=completion), home_wait=lambda t: 1.)
        pos = list(start)
        kin = NS(get_steppers=lambda: steppers,
                 calc_position=lambda p: [(p['stepper_x'] + p['stepper_y']) / 2,
                                           (p['stepper_x'] - p['stepper_y']) / 2, p['stepper_z']])
        def drip(target, speed, cp):
            self.assertIs(cp, completion)
            moving[0] = True
            pos[:] = target
        th = NS(get_kinematics=lambda: kin, get_position=lambda: list(pos),
                flush_step_generation=lambda: None, get_last_move_time=lambda: 2.,
                dwell=lambda t: None, drip_move=drip,
                set_position=lambda value: pos.__setitem__(slice(None), value))
        events = []
        def event(name, hmove):
            self.assertIs(hmove.get_mcu_endstops()[0], endstop)
            events.append(name)
        printer = NS(lookup_object=lambda _: th, send_event=event,
                     command_error=ValueError, get_start_args=lambda: {})
        target = list(start)
        target[axis] = 0. if axis == 0 else 245.
        move = real.HomingMove(printer, [(endstop, 'axis')])
        actual = move.homing_move(target, 37., probe_pos=True)
        self.assertAlmostEqual(actual[axis], hit[axis])
        self.assertAlmostEqual(pos[axis], halt[axis])
        self.assertIsNone(move.check_no_movement())
        self.assertEqual(events, ['homing:homing_move_begin', 'homing:homing_move_end'])


if __name__ == '__main__':
    unittest.main()
