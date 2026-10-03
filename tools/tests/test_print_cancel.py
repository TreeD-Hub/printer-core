# Назначение: проверить прерывание температурных ожиданий и остатка макроса
# через штатный CANCEL_PRINT, включая очистку SD-задачи и повторный запуск.
# Контур: read-only, без устройства; PRINT_CANCEL_GCODE_SOURCE подключает
# настоящий gcode.py закреплённого Klipper вместо локальной модели диспетчера.
import importlib.util
import os
from pathlib import Path
import shlex
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch


# Блок 1: Диспетчер и параметры; аппаратные объекты заменены моделью.
class CommandError(Exception):
    pass


class Command:
    error = CommandError

    def __init__(self, line):
        tokens = shlex.split(line)
        self.name = tokens[0].upper()
        self.params = dict(token.split('=', 1) for token in tokens[1:] if '=' in token)

    def get_command(self):
        return self.name

    def get(self, key):
        if key not in self.params:
            raise self.error('Missing ' + key)
        return self.params[key]

    def get_float(self, key, default, above=None):
        value = float(self.params.get(key, default))
        if above is not None and value <= above:
            raise self.error('Invalid ' + key)
        return value


class GCode:
    error = CommandError

    def __init__(self, printer):
        self.ready_gcode_handlers = {}
        self.mutex = threading.Lock()

    def get_mutex(self):
        return self.mutex

    def get_command_help(self):
        return {'TEMPERATURE_WAIT': 'Wait for temperature'}

    def register_command(self, name, callback, desc=None):
        old = self.ready_gcode_handlers.pop(name, None)
        if callback is not None:
            self.ready_gcode_handlers[name] = callback
        return old

    def run_script(self, script):
        with self.mutex:
            self.run_script_from_command(script)

    def run_script_from_command(self, script):
        for line in script.split('\n'):
            if line.strip():
                command = Command(line)
                self.ready_gcode_handlers[command.name](command)

    def respond_raw(self, _message):
        pass


gcode_module = SimpleNamespace(CommandError=CommandError)
source = os.environ.get('PRINT_CANCEL_GCODE_SOURCE')
if source:
    spec = importlib.util.spec_from_file_location('gcode', source)
    gcode_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gcode_module)
    GCode = gcode_module.GCodeDispatch

root = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    'treed_print_cancel', root / 'klipper-host/treed_print_cancel.py')
module = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'gcode': gcode_module}):
    spec.loader.exec_module(module)


class Reactor:
    def __init__(self):
        self.entered = threading.Event()
        self.wake = threading.Event()
        self.on_pause = None

    def mutex(self):
        return threading.Lock()

    def monotonic(self):
        return 0.

    def pause(self, eventtime):
        self.entered.set()
        if self.on_pause is not None:
            self.on_pause()
        elif not self.wake.wait(2.):
            raise AssertionError('Отмена не освободила ожидание')
        return eventtime


class Heater:
    def __init__(self, temperature=25.):
        self.temperature = temperature
        self.target = 60.

    def check_busy(self, _eventtime):
        return self.temperature < self.target

    def get_temp(self, _eventtime):
        return self.temperature, self.target


class Rig:
    # Блок 2: Модель SD-печати с реальной конкуренцией за G-code mutex.
    def __init__(self):
        self.reactor = Reactor()
        self.commands = []
        self.shutdowns = []
        self.state = 'printing'
        self.from_sd = False
        self.cleanup_error = False
        self.move_entered = threading.Event()
        self.move_release = threading.Event()
        self.heater = Heater()
        self.sd = SimpleNamespace(current_file=object(), must_pause_work=False,
                                  is_cmd_from_sd=lambda: self.from_sd)
        self.pause = SimpleNamespace(sd_paused=False)
        self.heaters = SimpleNamespace(
            heaters={'heater_bed': self.heater}, available_sensors=['heater_bed'],
            turn_off_all_heaters=self.turn_off, _get_temp=lambda _time: 'T:25',
            _wait_for_temperature=lambda _heater: None)
        self.objects = {'heaters': self.heaters, 'pause_resume': self.pause,
                        'virtual_sdcard': self.sd,
                        'toolhead': SimpleNamespace(get_last_move_time=lambda: 0.)}
        self.gcode = GCode(self)
        self.objects['gcode'] = self.gcode
        for name in ('M109', 'M190', 'TEMPERATURE_WAIT', 'CANCEL_PRINT',
                     'TURN_OFF_HEATERS', 'CANCEL_PRINT_BASE', 'RESET', 'PURGE',
                     'START_PRINT', 'BLOCK_MOVE', 'NOOP', 'FAIL'):
            self.gcode.register_command(name, self.dispatch)
        config = SimpleNamespace(get_printer=lambda: self, error=ValueError)
        self.extra = module.TreedPrintCancel(config)
        self.extra._connect()
        if source:
            self.gcode._handle_ready()

    def lookup_object(self, name):
        return self.objects[name]

    def get_reactor(self):
        return self.reactor

    def register_event_handler(self, _name, _callback):
        pass

    def send_event(self, _name):
        return []

    def get_start_args(self):
        return {}

    def is_shutdown(self):
        return bool(self.shutdowns)

    def invoke_shutdown(self, message):
        self.shutdowns.append(message)

    def turn_off(self):
        self.heater.target = 0.
        self.reactor.wake.set()

    def dispatch(self, command):
        name = command.get_command()
        self.commands.append(name)
        if name in ('M109', 'M190'):
            self.heater.target = 220. if name == 'M109' else 60.
            self.heaters._wait_for_temperature(self.heater)
        elif name == 'START_PRINT':
            self.gcode.run_script_from_command('M190\nPURGE')
        elif name == 'CANCEL_PRINT':
            if self.cleanup_error:
                raise self.gcode.error('Ошибка cleanup')
            self.gcode.run_script_from_command('TURN_OFF_HEATERS\nCANCEL_PRINT_BASE\nRESET')
        elif name == 'TURN_OFF_HEATERS':
            self.turn_off()
        elif name == 'CANCEL_PRINT_BASE':
            if self.pause.sd_paused:
                self.sd.current_file = None
                self.state = 'cancelled'
        elif name == 'BLOCK_MOVE':
            self.move_entered.set()
            if not self.move_release.wait(2.):
                raise self.gcode.error('Движение не завершено')
        elif name == 'FAIL':
            raise self.gcode.error('Обычная ошибка команды')

    def cancel_wait(self, script, event=None):
        errors = []

        def run():
            self.from_sd = True
            try:
                self.gcode.run_script(script)
            except BaseException as exc:
                errors.append(exc)
            finally:
                self.from_sd = False

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        if not (event or self.reactor.entered).wait(2.):
            raise AssertionError('Команда не вошла в ожидание')
        cancelled = threading.Event()

        def cancel():
            try:
                self.gcode.run_script('CANCEL_PRINT')
            except BaseException as exc:
                errors.append(exc)
            finally:
                cancelled.set()

        cancel_thread = threading.Thread(target=cancel, daemon=True)
        cancel_thread.start()
        return worker, cancel_thread, cancelled, errors


class PrintCancelTests(unittest.TestCase):
    # Блок 3: Прерывание, валидация и восстановление после отмены.
    def test_cancel_interrupts_heating_cooling_and_nested_macro(self):
        scripts = ('M109\nPURGE', 'M190\nPURGE', 'START_PRINT\nPURGE',
                   'TEMPERATURE_WAIT SENSOR=heater_bed MINIMUM=200\nPURGE',
                   'TEMPERATURE_WAIT SENSOR=heater_bed MAXIMUM=40\nPURGE')
        for script in scripts:
            with self.subTest(script=script):
                rig = Rig()
                if 'MAXIMUM=40' in script:
                    rig.heater.temperature = 80.
                worker, cancel, done, errors = rig.cancel_wait(script)
                worker.join(2.)
                cancel.join(2.)
                self.assertTrue(done.is_set())
                self.assertFalse(worker.is_alive() or cancel.is_alive())
                self.assertEqual(errors, [])
                self.assertNotIn('PURGE', rig.commands)
                self.assertIn('RESET', rig.commands)
                self.assertEqual(rig.heater.target, 0.)
                self.assertEqual(rig.state, 'cancelled')
                self.assertIsNone(rig.sd.current_file)
                self.assertFalse(rig.extra.cancel_pending or rig.extra.cleanup_active)
                self.assertFalse(rig.shutdowns)
                rig.reactor.on_pause = lambda: setattr(rig.heater, 'temperature', 60.)
                rig.gcode.run_script('M190\nNOOP')
                self.assertEqual(rig.commands[-1], 'NOOP')

    def test_current_move_finishes_but_following_commands_are_dropped(self):
        rig = Rig()
        worker, cancel, done, errors = rig.cancel_wait('BLOCK_MOVE\nPURGE', rig.move_entered)
        self.assertTrue(rig.reactor.wake.wait(2.))
        self.assertEqual(rig.heater.target, 0.)
        self.assertFalse(done.is_set())
        rig.gcode.run_script('CANCEL_PRINT')
        self.assertTrue(rig.extra.cancel_pending)
        rig.move_release.set()
        worker.join(2.)
        cancel.join(2.)
        self.assertTrue(done.is_set())
        self.assertEqual(errors, [])
        self.assertNotIn('PURGE', rig.commands)
        self.assertEqual(rig.commands.count('CANCEL_PRINT_BASE'), 1)
        self.assertFalse(rig.shutdowns)

    def test_waits_keep_temperature_bounds_without_cancel(self):
        for parameter, start, finish in (('MINIMUM=60', 25., 60.), ('MAXIMUM=40', 80., 40.)):
            with self.subTest(parameter=parameter):
                rig = Rig()
                rig.heater.temperature = start
                rig.reactor.on_pause = lambda: setattr(rig.heater, 'temperature', finish)
                rig.gcode.run_script('TEMPERATURE_WAIT SENSOR=heater_bed ' + parameter + '\nNOOP')
                self.assertEqual(rig.commands[-1], 'NOOP')
                self.assertFalse(rig.shutdowns)

    def test_wait_rejects_invalid_parameters(self):
        for parameters in ('SENSOR=heater_bed', 'SENSOR=unknown MINIMUM=60',
                           'SENSOR=heater_bed MINIMUM=60 MAXIMUM=40'):
            rig = Rig()
            with self.assertRaises(rig.gcode.error):
                rig.gcode.run_script('TEMPERATURE_WAIT ' + parameters)

    def test_cleanup_failure_does_not_leave_cancel_latched(self):
        rig = Rig()
        rig.cleanup_error = True
        with self.assertRaises(rig.gcode.error):
            rig.gcode.run_script('CANCEL_PRINT')
        self.assertFalse(rig.extra.cancel_pending or rig.extra.cleanup_active)
        rig.cleanup_error = False
        rig.gcode.run_script('CANCEL_PRINT')
        self.assertEqual(rig.state, 'cancelled')

    def test_normal_command_error_is_not_swallowed(self):
        rig = Rig()
        with self.assertRaises(rig.gcode.error):
            rig.gcode.run_script('FAIL\nNOOP')
        self.assertNotIn('NOOP', rig.commands)


if __name__ == '__main__':
    unittest.main()
