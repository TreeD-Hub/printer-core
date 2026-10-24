"""Офлайн-проверка переключения XYZ и обдува; read-only, без MCU."""
import ast
import configparser
from contextlib import nullcontext
import importlib.util
import re
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
    FIELDS = dict(en_pwm_mode=('GCONF', 2, 1),
                  tpwmthrs=('TPWMTHRS', 0, 0xfffff),
                  mres=('CHOPCONF', 24, 15), intpol=('CHOPCONF', 28, 1))

    def __init__(self, microsteps=16, interpolate=True, quiet=False):
        self.values = dict(en_pwm_mode=1, tpwmthrs=0 if quiet else 0xfffff,
                           irun=17, mres=8-(microsteps.bit_length()-1),
                           intpol=int(interpolate))

    def get_field(self, field, bits=None):
        if bits is None:
            return self.values[field]
        _, shift, mask = self.FIELDS[field]
        return (bits >> shift) & mask

    def lookup_register(self, field, default=None):
        return self.FIELDS.get(field, (default,))[0]

    def set_field(self, field, value):
        self.values[field] = value
        return self.register(self.lookup_register(field))

    def register(self, register):
        return sum((self.values[field] & mask) << shift
                   for field, (name, shift, mask) in self.FIELDS.items()
                   if name == register)


class MCU:
    def __init__(self, fields):
        self.hardware = {name: fields.register(name)
                         for name in ('GCONF', 'TPWMTHRS', 'CHOPCONF')}
        self.failures = 0
        self.writes = []

    def set_register(self, reg, bits):
        if self.failures:
            self.failures -= 1
            raise RuntimeError('SPI failure')
        self.hardware[reg] = bits
        self.writes.append((reg, bits))

    def get_register(self, reg):
        return self.hardware[reg]


class Printer:
    def __init__(self, variables=None, overrides=None):
        self.shutdown = False
        self.state = 'ready'
        self.stats = 'standby'
        self.saved = NS(allVariables=dict(variables or {}))
        self.disk_variables = dict(self.saved.allVariables)
        self.save_fails = False
        self.save_failures = 0
        self.restart_fails = False
        self.restart_requested = False
        self.after_save = lambda: None
        self.scripts = []
        self.overrides = overrides or {}
        self.fileconfig = configparser.RawConfigParser(strict=False,
                                                     inline_comment_prefixes=('#', ';'))
        profile = ROOT / 'klipper/profiles/treed_v2_corexy_v1'
        self.fileconfig.read([profile / 'printer_base.cfg', profile / 'steppers.cfg',
                              profile / 'driver_mode.cfg'], encoding='utf-8')
        self.fileconfig.read_dict(self.overrides)
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
            'gcode_macro _TREED_OPERATION_STATE': NS(variables={'phase': 'idle'}),
            'print_stats': NS(get_status=lambda now: {'state': self.stats}),
            'pause_resume': NS(get_status=lambda now: {'is_paused': False}, pause_command_sent=False),
            'virtual_sdcard': NS(is_active=lambda: False),
        }
    def build_motion(self):
        cfg = self.fileconfig
        self.objects['toolhead'] = NS(
            wait_moves=lambda: None,
            max_velocity=cfg.getfloat('printer', 'max_velocity'),
            max_accel=cfg.getfloat('printer', 'max_accel'),
            square_corner_velocity=cfg.getfloat('printer', 'square_corner_velocity'),
            min_cruise_ratio=.5, set_max_velocities=self.set_max_velocities)
        for axis in 'xyz':
            section, tmc = 'stepper_' + axis, 'tmc5160 stepper_' + axis
            fields = Fields(cfg.getint(section, 'microsteps'),
                            cfg.getboolean(tmc, 'interpolate'),
                            cfg.getfloat(tmc, 'stealthchop_threshold') > 0.)
            self.objects[tmc] = NS(fields=fields, mcu_tmc=MCU(fields))

    def set_max_velocities(self, velocity, accel, scv, cruise_ratio):
        toolhead = self.objects['toolhead']
        names = ('max_velocity', 'max_accel', 'square_corner_velocity', 'min_cruise_ratio')
        for name, value in zip(names, (velocity, accel, scv, cruise_ratio)):
            if value is not None:
                setattr(toolhead, name, value)
        return tuple(getattr(toolhead, name) for name in names)

    def save(self, script):
        self.scripts.append(script)
        if script == 'FIRMWARE_RESTART':
            if self.restart_fails:
                raise RuntimeError('restart failure')
            self.restart_requested = True
            return
        if self.save_fails or self.save_failures:
            self.save_failures = max(0, self.save_failures-1)
            # Моделируем оборванную запись; следующий SAVE должен вернуть все данные.
            self.disk_variables = {}
            raise RuntimeError('disk failure')
        # Штатный extended parser снимает внешние shell-кавычки до literal_eval.
        params = dict(part.split('=', 1) for part in shlex.split(script)[1:])
        values = dict(self.saved.allVariables)
        values[params['VARIABLE']] = ast.literal_eval(params['VALUE'])
        self.disk_variables = dict(values)
        self.saved.allVariables = values
        self.after_save()

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
    def __init__(self, printer, section='treed_driver_mode', **values):
        self.printer, self.values = printer, values
        self.section, self.fileconfig = section, printer.fileconfig

    def get_printer(self):
        return self.printer

    def getsection(self, name):
        return Config(self.printer, name, **({'off_below': .15}
                      if name == 'controller_fan driver_fan' else {}))

    def get(self, name, default=None):
        if name in self.values:
            return self.values[name]
        return self.fileconfig.get(self.section, name, fallback=default)

    def getint(self, name, default=None, **kwargs):
        value = self.get(name, default)
        return None if value is None else int(value)

    def getfloat(self, name, default=None, **kwargs):
        value = self.get(name, default)
        return None if value is None else float(value)

    def getboolean(self, name, default=False):
        if name in self.values:
            return self.values[name]
        return self.fileconfig.getboolean(self.section, name, fallback=default)

    error = staticmethod(ValueError)


def boot_driver(printer, ready=True):
    driver = load('treed_driver_mode').load_config(Config(printer))
    printer.build_motion()
    driver.handle_connect()
    driver.handle_ready()
    if ready:
        printer.run_callbacks()
    return driver


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
    # Блок 2: Полный профиль, перезапуск, допуск и восстановление после отказа.
    def setUp(self):
        self.printer = Printer()
        self.driver = boot_driver(self.printer)

    def reboot(self):
        self.printer = Printer(self.printer.disk_variables, self.printer.overrides)
        self.driver = boot_driver(self.printer)

    def quiet(self):
        self.driver.cmd_set(Command())
        self.reboot()

    def test_startup_defers_spi_and_blocks_mode_change(self):
        printer = Printer()
        driver = boot_driver(printer, ready=False)
        self.assertTrue(all(not d.mcu_tmc.writes for d in driver.drivers))
        self.assertEqual(driver.state, 'applying')
        with self.assertRaises(ValueError):
            driver.cmd_set(Command())
        printer.run_callbacks()
        self.assertEqual(sum(len(d.mcu_tmc.writes) for d in driver.drivers), 6)
        self.assertEqual(driver.mode, 'normal')
        self.assertEqual(driver.state, 'ready')

    def test_startup_failure_shuts_down_after_successful_rollback(self):
        printer = Printer()
        driver = boot_driver(printer, ready=False)
        previous = [dict(d.mcu_tmc.hardware) for d in driver.drivers]
        driver.drivers[1].mcu_tmc.failures = 1
        with self.assertLogs(level='ERROR'):
            printer.run_callbacks()
        self.assertTrue(printer.shutdown)
        self.assertTrue(driver.needs_restart)
        self.assertEqual(driver.state, 'fault')
        self.assertEqual([d.mcu_tmc.hardware for d in driver.drivers], previous)

    def test_mode_change_saves_and_restarts_without_live_mres_change(self):
        before = [dict(d.mcu_tmc.hardware) for d in self.driver.drivers]
        self.driver.cmd_set(Command())
        self.assertEqual(self.printer.saved.allVariables['driver_mode'], 'quiet')
        self.assertTrue(self.printer.restart_requested)
        self.assertEqual(self.driver.state, 'applying')
        self.assertTrue(self.driver.needs_restart)
        self.assertEqual(self.driver.get_status(1)['effective_modes'],
                         dict(X='normal', Y='normal', Z='normal'))
        self.assertEqual([d.mcu_tmc.hardware for d in self.driver.drivers], before)
        self.assertEqual(self.driver.motion_limits(), (600., 25000., 9., .5))
        self.reboot()
        self.assertEqual(self.driver.get_status(1)['effective_modes'],
                         dict(X='quiet', Y='quiet', Z='quiet'))
        for driver in self.driver.drivers:
            self.assertEqual(driver.fields.values['irun'], 17)
            self.assertEqual(driver.fields.values['mres'], 1)
            self.assertEqual(driver.fields.values['intpol'], 1)
        self.assertEqual(self.driver.mode, 'quiet')
        self.assertFalse(self.driver.needs_restart)
        self.assertFalse(any('G28' in s or 'G1 ' in s for s in self.printer.scripts))

    def test_full_quiet_profile_is_selected_before_motion_objects(self):
        printer = Printer({'driver_mode': 'quiet'}, overrides={
            'tmc5160 stepper_x': {'run_current': '.8', 'driver_PWM_REG': '7'},
            'stepper_x': {'microsteps': '64'}})
        driver = load('treed_driver_mode').load_config(Config(printer))
        self.assertNotIn('toolhead', printer.objects)
        self.assertFalse(any(name.startswith('tmc5160 ') for name in printer.objects))
        cfg = printer.fileconfig
        for axis in 'xyz':
            self.assertEqual(cfg.getint('stepper_' + axis, 'microsteps'), 128)
            self.assertEqual(cfg.getfloat('stepper_' + axis, 'step_pulse_duration'), 1e-7)
            tmc = 'tmc5160 stepper_' + axis
            self.assertTrue(cfg.getboolean(tmc, 'interpolate'))
            self.assertEqual(cfg.getint(tmc, 'driver_HSTRT'), 5)
            self.assertEqual(cfg.getint(tmc, 'driver_TPFD'), 4)
            self.assertEqual(cfg.getint(tmc, 'driver_PWM_OFS'), 30)
            self.assertEqual(cfg.getint(tmc, 'driver_PWM_GRAD'), 0)
            self.assertEqual(cfg.getint(tmc, 'driver_PWM_REG'), 4)
            self.assertTrue(cfg.getboolean(tmc, 'driver_PWM_AUTOSCALE'))
            self.assertTrue(cfg.getboolean(tmc, 'driver_PWM_AUTOGRAD'))
        self.assertEqual(cfg.getfloat('tmc5160 stepper_x', 'run_current'), .8)
        self.assertEqual(cfg.getfloat('tmc5160 stepper_z', 'run_current'), .9)
        self.assertEqual(driver.microsteps, dict(x=128, y=128, z=128))
        # Новый normal читает исходный конфиг, а не изменённый объект quiet.
        normal = Printer(overrides=printer.overrides)
        boot_driver(normal)
        self.assertEqual(normal.fileconfig.getint('stepper_x', 'microsteps'), 64)
        self.assertEqual(normal.fileconfig.getint('tmc5160 stepper_x', 'driver_PWM_REG'), 7)
        self.assertFalse(normal.fileconfig.getboolean('tmc5160 stepper_z', 'interpolate'))

    def test_quiet_caps_commands_and_normal_reloads_original_config(self):
        self.quiet()
        toolhead = self.driver.toolhead
        self.assertEqual(self.driver.motion_limits(), (200., 50000., 9., .5))
        # SET_VELOCITY_LIMIT / сервис задают все поля, M204 — только ACCEL.
        toolhead.set_max_velocities(900., 70000., 500., None)
        self.assertEqual(self.driver.motion_limits(), (200., 50000., 500., .5))
        toolhead.set_max_velocities(None, 30000., None, None)
        self.assertEqual(toolhead.max_accel, 30000.)
        self.driver.cmd_set(Command())
        self.assertFalse(self.printer.restart_requested)
        self.assertEqual(self.driver.motion_limits(), (200., 30000., 500., .5))
        self.driver.cmd_set(Command('normal'))
        self.reboot()
        self.assertEqual(self.driver.motion_limits(), (600., 25000., 9., .5))
        self.assertEqual(self.driver.microsteps, dict(x=16, y=16, z=16))
        self.assertEqual(self.printer.fileconfig.getfloat('stepper_x', 'step_pulse_duration'), 2e-6)
        self.driver.toolhead.set_max_velocities(900., 70000., None, None)
        self.assertEqual(self.driver.motion_limits()[:2], (900., 70000.))

    def test_quiet_preserves_lower_limits(self):
        self.quiet()
        self.driver.toolhead.set_max_velocities(150., 4000., 5., None)
        self.driver.cmd_set(Command())
        self.assertEqual(self.driver.motion_limits(), (150., 4000., 5., .5))
        self.driver.toolhead.set_max_velocities(100., 2000., None, None)
        self.assertEqual(self.driver.motion_limits(), (100., 2000., 5., .5))

    def test_temporary_homing_limits_restore_quiet_on_success_and_failure(self):
        self.quiet()
        guard = load('treed_motor_guard').load_config(Config(self.printer))
        self.printer.objects['gcode_move'] = NS(absolute_coord=True)
        params = {'ACTION': 'HOME_XY', 'X': '1', 'ACCEL': '700'}
        cmd = NS(get=params.__getitem__, get_command_parameters=lambda: params,
                 get_float=lambda key, **kwargs: float(params[key]), error=ValueError)
        gcode = self.printer.objects['gcode']
        for fails in (False, True):
            with self.subTest(fails=fails):
                def homing(script):
                    self.assertEqual(self.driver.motion_limits(), (200., 700., 9., .5))
                    if fails:
                        raise RuntimeError('homing failed')
                gcode.run_script_from_command = homing
                try:
                    if fails:
                        with self.assertRaisesRegex(RuntimeError, 'homing failed'):
                            guard.cmd_run(cmd)
                    else:
                        guard.cmd_run(cmd)
                finally:
                    gcode.run_script_from_command = self.printer.save
                self.assertEqual(self.driver.motion_limits(), (200., 50000., 9., .5))

    def test_saved_quiet_on_startup_caps_motion(self):
        printer = Printer({'driver_mode': 'quiet'})
        driver = boot_driver(printer)
        self.assertEqual(driver.motion_limits(), (200., 50000., 9., .5))
        self.assertEqual(driver.get_status(1)['service_velocity_limit'], 50.)
        driver.cmd_set(Command('normal'))
        self.assertTrue(printer.restart_requested)
        printer = Printer(printer.disk_variables)
        driver = boot_driver(printer)
        self.assertEqual(driver.motion_limits(), (600., 25000., 9., .5))
        self.assertEqual(driver.get_status(1)['service_velocity_limit'], 350.)

    def test_save_failure_restores_previous_selection_and_other_variables(self):
        self.printer.saved.allVariables['user_value'] = {'keep': 42}
        self.printer.save_failures = 1
        with self.assertRaises(ValueError):
            self.driver.cmd_set(Command())
        self.assertEqual(self.driver.motion_limits(), (600., 25000., 9., .5))
        self.assertEqual(self.printer.disk_variables['user_value'], {'keep': 42})
        self.assertEqual(self.printer.disk_variables['driver_mode'], 'normal')
        self.assertFalse(self.printer.shutdown)
        self.assertFalse(self.printer.restart_requested)
        self.assertEqual(self.driver.mode, 'normal')

    def test_restart_failure_restores_selection_without_changing_geometry(self):
        for mode, target, mres in (('normal', 'quiet', 4), ('quiet', 'normal', 1)):
            with self.subTest(mode=mode):
                printer = Printer({'driver_mode': mode, 'user_value': 42})
                driver = boot_driver(printer)
                printer.restart_fails = True
                with self.assertRaises(ValueError):
                    driver.cmd_set(Command(target))
                self.assertEqual(printer.disk_variables,
                                 dict(driver_mode=mode, user_value=42))
                self.assertEqual(driver.mode, mode)
                self.assertEqual(driver.state, 'ready')
                self.assertFalse(driver.needs_restart)
                self.assertTrue(all(d.fields.get_field('mres') == mres for d in driver.drivers))

    def test_failed_restore_after_restart_error_stays_faulted(self):
        self.printer.restart_fails = True
        self.printer.after_save = lambda: setattr(self.printer, 'save_fails', True)
        with self.assertRaises(ValueError):
            self.driver.cmd_set(Command())
        self.assertTrue(self.printer.shutdown)
        self.assertEqual(self.driver.state, 'fault')
        self.assertTrue(self.driver.needs_restart)
        self.assertFalse(self.printer.restart_requested)

    def test_rollback_failure_requires_restart(self):
        self.driver.drivers[1].mcu_tmc.failures = 2
        self.driver.handle_ready()
        with self.assertLogs(level='ERROR'):
            self.printer.run_callbacks()
        self.assertTrue(self.printer.shutdown)
        self.assertTrue(self.driver.needs_restart)
        self.assertEqual(self.driver.state, 'fault')

    def test_persistent_save_failure_shuts_down_without_restart(self):
        self.printer.save_fails = True
        with self.assertRaises(ValueError):
            self.driver.cmd_set(Command())
        self.assertTrue(self.printer.shutdown)
        self.assertFalse(self.printer.restart_requested)
        self.assertEqual(self.driver.state, 'fault')

    def test_late_profile_loading_is_rejected(self):
        for mode in ('normal', 'quiet'):
            printer = Printer({'driver_mode': mode})
            printer.build_motion()
            with self.assertRaisesRegex(ValueError, 'до TMC'):
                load('treed_driver_mode').load_config(Config(printer))
            self.assertEqual(printer.fileconfig.getint('stepper_x', 'microsteps'), 16)

    def test_wrong_mres_readback_prevents_ready(self):
        printer = Printer({'driver_mode': 'quiet'})
        driver = boot_driver(printer, ready=False)
        driver.drivers[0].mcu_tmc.hardware['CHOPCONF'] = 4 << 24
        with self.assertLogs(level='ERROR'):
            printer.run_callbacks()
        self.assertTrue(printer.shutdown)
        self.assertEqual(driver.state, 'fault')

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

    def test_recheck_after_persist_does_not_restart_during_print(self):
        self.printer.after_save = lambda: setattr(self.printer, 'stats', 'printing')
        with self.assertRaises(ValueError):
            self.driver.cmd_set(Command())
        self.assertFalse(self.printer.restart_requested)
        self.assertEqual(self.printer.disk_variables['driver_mode'], 'normal')

    def test_strict_arguments(self):
        for cmd in (Command('bad'), Command(EXTRA='1')):
            with self.assertRaises(ValueError):
                self.driver.cmd_set(cmd)

    def test_homing_override_does_not_change_selected_mode(self):
        self.quiet()
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
        main_config = (ROOT / 'klipper/printer.cfg').read_text(encoding='utf-8')
        includes = re.findall(r'^\[include ([^]]+)\]', main_config, re.M)
        self.assertEqual(includes[0], 'profiles/treed_v2_corexy_v1/driver_mode.cfg')
        loader = (ROOT / 'loader/steps/klipper-core.sh').read_text(encoding='utf-8')
        self.assertLess(loader.index('cannot preserve treed_variables.cfg'), loader.index('# Блок 5:'))
        self.assertIn('cannot restore treed_variables.cfg', loader)


if __name__ == '__main__':
    unittest.main()
