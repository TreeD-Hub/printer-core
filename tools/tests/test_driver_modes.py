"""Офлайн-проверка переключения XYZ и обдува; read-only, без MCU."""
import ast
from contextlib import nullcontext
import importlib.util
import shlex
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

ROOT = Path(__file__).resolve().parents[2]

# Блок 1: Модель cached fields, MCU и штатного разбора SAVE_VARIABLE.


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'klipper-host' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Fields:
    def __init__(self):
        self.values = dict(en_pwm_mode=1, tpwmthrs=0xfffff, irun=17, mres=4)

    def get_field(self, field, bits=None):
        return self.values[field] if bits is None else bits

    def lookup_register(self, field):
        return 'GCONF' if field == 'en_pwm_mode' else 'TPWMTHRS'

    def set_field(self, field, value):
        self.values[field] = value
        return value


class MCU:
    def __init__(self):
        self.hardware = {'GCONF': 1, 'TPWMTHRS': 0xfffff}
        self.failures = 0

    def set_register(self, reg, bits):
        if self.failures:
            self.failures -= 1
            raise RuntimeError('SPI failure')
        self.hardware[reg] = bits

    def get_register(self, reg):
        return self.hardware[reg]


class Printer:
    def __init__(self):
        self.shutdown = False
        self.state = 'ready'
        self.stats = 'standby'
        self.saved = NS(allVariables={})
        self.save_fails = False
        self.commands = {}
        self.callbacks = []
        self.controller = NS(fan_speed=1., idle_speed=1., last_on=99,
                             get_status=lambda now: {'speed': 1.})
        self.motors_enabled = False
        self.fan_output = []
        self.controller.stepper_names = ['stepper_x', 'stepper_y', 'stepper_z']
        self.controller.stepper_enable = NS(lookup_enable=lambda name:
            NS(is_motor_enabled=lambda: self.motors_enabled))
        self.controller.idle_timeout = 120
        self.controller.last_speed = 0.
        self.controller.fan = NS(set_speed=self.fan_output.append)
        self.objects = {
            'gcode': NS(register_command=lambda name, fn: self.commands.update({name: fn}),
                        get_mutex=nullcontext,
                        run_script_from_command=self.save),
            'save_variables': self.saved,
            'controller_fan driver_fan': self.controller,
            'toolhead': NS(wait_moves=lambda: None, max_velocity=600., max_accel=25000.,
                           square_corner_velocity=9., min_cruise_ratio=.5,
                           set_max_velocities=self.set_max_velocities),
            'gcode_macro _TREED_OPERATION_STATE': NS(variables={'phase': 'idle'}),
            'print_stats': NS(get_status=lambda now: {'state': self.stats}),
            'pause_resume': NS(get_status=lambda now: {'is_paused': False}, pause_command_sent=False),
            'virtual_sdcard': NS(is_active=lambda: False),
        }
        for axis in 'xyz':
            self.objects['tmc5160 stepper_' + axis] = NS(fields=Fields(), mcu_tmc=MCU())

    def set_max_velocities(self, velocity, accel, scv, cruise_ratio):
        toolhead = self.objects['toolhead']
        names = ('max_velocity', 'max_accel', 'square_corner_velocity', 'min_cruise_ratio')
        for name, value in zip(names, (velocity, accel, scv, cruise_ratio)):
            if value is not None:
                setattr(toolhead, name, value)
        return tuple(getattr(toolhead, name) for name in names)

    def save(self, script):
        if self.save_fails:
            raise RuntimeError('disk failure')
        # Штатный extended parser снимает внешние shell-кавычки до literal_eval.
        params = dict(part.split('=', 1) for part in shlex.split(script)[1:])
        self.saved.allVariables[params['VARIABLE']] = ast.literal_eval(params['VALUE'])

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)

    def load_object(self, config, name):
        return self.objects[name]

    def register_event_handler(self, *args):
        pass

    def get_reactor(self):
        return NS(monotonic=lambda: 1., register_callback=self.callbacks.append)

    def run_callbacks(self):
        callbacks, self.callbacks = self.callbacks, []
        for callback in callbacks:
            callback(1.)

    def is_shutdown(self):
        return self.shutdown

    def get_state_message(self):
        return ('', self.state)

    def invoke_shutdown(self, message):
        self.shutdown = True


class Config:
    def __init__(self, printer, **values):
        self.printer, self.values = printer, values

    def get_printer(self):
        return self.printer

    def getsection(self, name):
        return Config(self.printer, off_below=.15)

    def getfloat(self, name, default=None, **kwargs):
        return self.values.get(name, default)

    def getboolean(self, name, default=False):
        return self.values.get(name, default)

    error = staticmethod(ValueError)


class Command:
    def __init__(self, mode='quiet', **extra):
        self.params = dict(MODE=mode, **extra)

    def get_command_parameters(self):
        return self.params

    def get(self, key):
        return self.params[key]

    def get_float(self, key):
        return float(self.params[key])

    error = staticmethod(ValueError)


class DriverModesTest(unittest.TestCase):
    # Блок 2: Допуск, групповая запись, отказ и независимый профиль обдува.
    def setUp(self):
        self.printer = Printer()
        self.driver = load('treed_driver_mode').load_config(Config(self.printer))
        self.driver.handle_connect()
        self.driver.handle_ready()
        self.printer.run_callbacks()

    def test_startup_defers_spi_and_blocks_mode_change(self):
        driver = load('treed_driver_mode').load_config(Config(self.printer))
        driver.handle_connect()
        writes = []
        for item in driver.drivers:
            original = item.mcu_tmc.set_register
            def record(reg, bits, original=original):
                writes.append((reg, bits))
                original(reg, bits)
            item.mcu_tmc.set_register = record
        driver.handle_ready()
        self.assertEqual(writes, [])
        self.assertEqual(driver.state, 'applying')
        with self.assertRaises(ValueError):
            driver.cmd_set(Command())
        self.printer.run_callbacks()
        self.assertEqual(len(writes), 6)
        self.assertEqual(driver.mode, 'normal')
        self.assertEqual(driver.state, 'ready')

    def test_startup_failure_shuts_down_after_successful_rollback(self):
        self.driver.drivers[0].mcu_tmc.failures = 1
        self.driver.handle_ready()
        with self.assertLogs(level='ERROR'):
            self.printer.run_callbacks()
        self.assertTrue(self.printer.shutdown)
        self.assertTrue(self.driver.needs_restart)
        self.assertEqual(self.driver.state, 'fault')

    def test_group_apply_and_restore_saved(self):
        self.driver.cmd_set(Command())
        self.assertEqual(self.printer.saved.allVariables['driver_mode'], 'quiet')
        self.assertEqual(self.driver.get_status(1)['effective_modes'], dict(X='quiet', Y='quiet', Z='quiet'))
        for driver in self.driver.drivers:
            self.assertEqual(driver.fields.values['irun'], 17)
            self.assertEqual(driver.fields.values['mres'], 4)
        self.driver.handle_ready()
        self.printer.run_callbacks()
        self.assertEqual(self.driver.mode, 'quiet')

    def test_partial_failure_rolls_back(self):
        self.driver.drivers[1].mcu_tmc.failures = 1
        with self.assertRaises(ValueError):
            self.driver.cmd_set(Command())
        self.assertEqual(self.driver.mode, 'normal')
        self.assertFalse(self.printer.shutdown)
        self.assertTrue(all(d.mcu_tmc.hardware['GCONF'] == 0 for d in self.driver.drivers))
        self.assertEqual(self.driver.motion_limits(), (600., 25000., 9., .5))
        self.assertIsNone(self.driver.normal_limits)

    def test_quiet_caps_commands_and_normal_restores_snapshot(self):
        toolhead = self.driver.toolhead
        toolhead.set_max_velocities(550., 22000., 8., .4)
        self.driver.cmd_set(Command())
        self.assertEqual(self.driver.motion_limits(), (350., 15000., 8., .4))
        # SET_VELOCITY_LIMIT / сервис задают все поля, M204 — только ACCEL.
        toolhead.set_max_velocities(900., 40000., 500., None)
        self.assertEqual(self.driver.motion_limits(), (350., 15000., 350., .4))
        toolhead.set_max_velocities(None, 30000., None, None)
        self.assertEqual(toolhead.max_accel, 15000.)
        self.driver.cmd_set(Command())
        self.driver.cmd_set(Command('normal'))
        self.assertEqual(self.driver.motion_limits(), (550., 22000., 8., .4))
        toolhead.set_max_velocities(600., 25000., None, None)
        self.assertEqual(toolhead.max_velocity, 600.)

    def test_quiet_preserves_lower_limits(self):
        self.driver.toolhead.set_max_velocities(200., 4000., 5., None)
        self.driver.cmd_set(Command())
        self.assertEqual(self.driver.motion_limits(), (200., 4000., 5., .5))
        self.driver.toolhead.set_max_velocities(100., 2000., None, None)
        self.assertEqual(self.driver.motion_limits(), (100., 2000., 5., .5))

    def test_saved_quiet_on_startup_caps_motion(self):
        printer = Printer()
        printer.saved.allVariables['driver_mode'] = 'quiet'
        driver = load('treed_driver_mode').load_config(Config(printer))
        driver.handle_connect()
        driver.handle_ready()
        printer.run_callbacks()
        self.assertEqual(driver.motion_limits(), (350., 15000., 9., .5))
        driver.cmd_set(Command('normal'))
        self.assertEqual(driver.motion_limits(), (600., 25000., 9., .5))

    def test_save_failure_restores_limits_in_both_directions(self):
        self.printer.save_fails = True
        with self.assertRaises(ValueError):
            self.driver.cmd_set(Command())
        self.assertEqual(self.driver.motion_limits(), (600., 25000., 9., .5))
        self.assertIsNone(self.driver.normal_limits)
        self.printer.save_fails = False
        self.driver.cmd_set(Command())
        self.printer.save_fails = True
        with self.assertRaises(ValueError):
            self.driver.cmd_set(Command('normal'))
        self.assertEqual(self.driver.mode, 'quiet')
        self.assertEqual(self.driver.motion_limits(), (350., 15000., 9., .5))
        self.driver.toolhead.set_max_velocities(900., 40000., None, None)
        self.assertEqual(self.driver.motion_limits(), (350., 15000., 9., .5))

    def test_rollback_failure_requires_restart(self):
        self.driver.drivers[1].mcu_tmc.failures = 2
        with self.assertRaises(ValueError):
            self.driver.cmd_set(Command())
        self.assertTrue(self.printer.shutdown)
        self.assertTrue(self.driver.needs_restart)
        self.assertEqual(self.driver.state, 'fault')

    def test_save_failure_rolls_back(self):
        self.printer.save_fails = True
        with self.assertRaises(ValueError):
            self.driver.cmd_set(Command())
        self.assertEqual(self.driver.mode, 'normal')

    def test_printing_paused_and_service_are_blocked(self):
        for state in ('printing', 'paused', 'error'):
            self.printer.stats = state
            with self.assertRaises(ValueError):
                self.driver.cmd_set(Command())
        self.printer.stats = 'standby'
        self.printer.objects['gcode_macro _TREED_OPERATION_STATE'].variables['phase'] = 'preparing'
        with self.assertRaises(ValueError):
            self.driver.cmd_set(Command())

    def test_recheck_after_queue(self):
        self.printer.objects['toolhead'].wait_moves = lambda: setattr(self.printer, 'stats', 'printing')
        with self.assertRaises(ValueError):
            self.driver.cmd_set(Command())
        self.assertEqual(self.driver.mode, 'normal')

    def test_strict_arguments(self):
        for cmd in (Command('bad'), Command(EXTRA='1')):
            with self.assertRaises(ValueError):
                self.driver.cmd_set(cmd)

    def test_homing_override_does_not_change_selected_mode(self):
        self.driver.cmd_set(Command())
        self.driver.drivers[0].fields.set_field('en_pwm_mode', 0)
        self.assertEqual(self.driver.get_status(1)['effective_modes']['X'], 'normal')
        self.assertEqual(self.driver.mode, 'quiet')

    def fan(self, **values):
        return load('treed_driver_fan_mode').load_config(Config(self.printer, **values))

    def test_fan_runs_only_at_full_power_and_resets_saved_quiet(self):
        fan = self.fan()
        self.assertEqual(fan.available_modes(), ['normal'])
        self.assertFalse(fan.get_status(1)['power_control'])
        self.assertEqual(fan.get_status(1)['min_power'], 1.)
        self.assertIsNone(fan.get_status(1)['message'])
        self.printer.saved.allVariables['driver_fan_mode'] = 'quiet'
        fan.handle_ready()
        self.assertEqual(fan.mode, 'normal')
        self.assertEqual(self.printer.controller.fan_speed, 1.)
        self.assertEqual(self.printer.controller.idle_speed, 1.)
        self.assertIsNotNone(fan.get_status(1)['message'])
        self.printer.saved.allVariables['driver_fan_mode'] = {'mode': 'normal', 'power': .9}
        fan.handle_ready()
        self.assertEqual(self.printer.controller.fan_speed, 1.)

    def test_fan_preserves_automation_and_restores_on_save_failure(self):
        fan = self.fan()
        fan.cmd_set(Command('normal'))
        self.assertEqual(self.printer.controller.last_on, 99)
        self.assertEqual(self.printer.controller.fan_speed, 1.)
        self.assertEqual(self.printer.saved.allVariables['driver_fan_mode'], 'normal')
        self.printer.save_fails = True
        with self.assertRaises(ValueError):
            fan.cmd_set(Command('normal'))
        self.assertEqual(fan.mode, 'normal')
        self.assertEqual(self.printer.controller.idle_speed, 1.)

    def test_legacy_fan_overrides_cannot_lower_power(self):
        for active, idle in ((.8, .4), (.1, .1)):
            self.printer.controller.fan_speed = active
            self.printer.controller.idle_speed = idle
            fan = self.fan(quiet_active_speed=active, quiet_idle_speed=idle)
            self.assertEqual((fan.controller.fan_speed, fan.controller.idle_speed), (1., 1.))
        for mode in ('quiet', 'bad'):
            with self.assertRaises(ValueError):
                fan.cmd_set(Command(mode))
        with self.assertRaises(ValueError):
            fan.cmd_set(Command('normal', POWER='90'))

    def test_manual_fan_delay_and_hold_protection(self):
        fan = self.fan()
        self.printer.motors_enabled = True
        fan.callback(0.)
        fan.callback(29.)
        self.assertEqual(self.printer.fan_output, [])
        self.assertEqual(fan.load_reason, 'manual_delay')
        fan.callback(30.)
        self.assertEqual(self.printer.fan_output, [1.])
        self.assertEqual(fan.load_reason, 'motor_timeout')

    def test_nonfinite_fan_delay_is_rejected(self):
        for delay in (float('nan'), float('inf'), float('-inf')):
            with self.assertRaises(ValueError):
                self.fan(load_delay=delay)

    def test_short_manual_move_does_not_start_cooldown(self):
        fan = self.fan()
        self.printer.motors_enabled = True
        fan.callback(0.)
        self.printer.motors_enabled = False
        fan.callback(5.)
        fan.callback(130.)
        self.assertEqual(self.printer.fan_output, [])
        self.assertEqual(fan.load_reason, 'idle')

    def test_operation_load_and_cooldown(self):
        for phase in ('preparing', 'printing', 'paused', 'calibrating', 'auto_remove'):
            with self.subTest(phase=phase):
                self.printer.controller.last_speed = 0.
                self.printer.fan_output.clear()
                fan = self.fan()
                self.printer.objects['gcode_macro _TREED_OPERATION_STATE'].variables['phase'] = phase
                self.printer.motors_enabled = False
                fan.callback(0.)
                self.assertEqual(self.printer.fan_output, [])
                self.printer.motors_enabled = True
                fan.callback(1.)
                self.assertEqual(self.printer.fan_output, [1.])
                self.printer.motors_enabled = False
                fan.callback(2.)
                self.assertEqual(fan.load_reason, 'cooldown')
                self.assertEqual(self.printer.fan_output, [1.])
                fan.callback(121.)
                self.assertEqual(self.printer.fan_output, [1., 0.])

    def test_print_stats_and_unknown_phase_start_fan(self):
        fan = self.fan()
        self.printer.motors_enabled = True
        self.printer.stats = 'printing'
        fan.callback(0.)
        self.assertEqual(fan.load_reason, 'printing')
        self.printer.stats = 'standby'
        self.printer.objects['gcode_macro _TREED_OPERATION_STATE'].variables['phase'] = 'unknown'
        fan.callback(1.)
        self.assertEqual(fan.load_reason, 'unknown_state')
        self.assertEqual(self.printer.controller.last_speed, 1.)

    def test_sgt_calibration_starts_fan_without_phase_marker(self):
        fan = self.fan()
        self.printer.motors_enabled = True
        self.printer.objects['treed_sgt_executor'] = NS(running=True)
        fan.callback(0.)
        self.assertEqual(fan.load_reason, 'calibrating')
        self.assertEqual(self.printer.fan_output, [1.])

    def test_fan_old_power_setting_is_not_restored(self):
        fan = self.fan()
        self.printer.saved.allVariables['driver_fan_mode'] = {'mode': 'normal', 'power': .15}
        fan.handle_ready()
        self.assertEqual(self.printer.controller.fan_speed, 1.)
        self.assertEqual(self.printer.controller.idle_speed, 1.)
        fan.cmd_set(Command('normal'))
        self.assertEqual(self.printer.saved.allVariables['driver_fan_mode'], 'normal')

    def test_delivery_contract(self):
        bootstrap = (ROOT / 'loader/steps/runtime-bootstrap.sh').read_text(encoding='utf-8')
        for name in ('treed_driver_mode.py', 'treed_driver_fan_mode.py'):
            self.assertIn(name, bootstrap)
        self.assertIn('driver_mode.cfg', (ROOT / 'klipper/printer.cfg').read_text(encoding='utf-8'))
        loader = (ROOT / 'loader/steps/klipper-core.sh').read_text(encoding='utf-8')
        self.assertLess(loader.index('cannot preserve treed_variables.cfg'), loader.index('# Блок 5:'))
        self.assertIn('cannot restore treed_variables.cfg', loader)


if __name__ == '__main__':
    unittest.main()
