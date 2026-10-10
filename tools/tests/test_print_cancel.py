# Назначение: проверить прерывание температурных ожиданий и остатка макроса
# через штатный CANCEL_PRINT, включая очистку SD-задачи и повторный запуск.
# Контур: read-only, без устройства; PRINT_CANCEL_GCODE_SOURCE подключает
# настоящий gcode.py закреплённого Klipper вместо локальной модели диспетчера.
import ast
import bisect
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

    def get(self, key, default=None):
        if key not in self.params:
            if default is not None:
                return default
            raise self.error('Missing ' + key)
        return self.params[key]

    def get_float(self, key, default=None, above=None):
        value = float(self.get(key, default))
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

    def respond_info(self, _message):
        pass


class Calibration:
    def __init__(self, config):
        self.printer = config.get_printer()
        self._load_calibration([(2., 100.), (1., 200.), (0., 300.)])

    def verify_calibrated(self):
        if len(self.cal_freqs) <= 2:
            raise self.printer.command_error('Must calibrate probe_eddy_current first')

    def get_calibration(self):
        return list(self.cal_freqs), list(self.cal_zpos)

    def _load_calibration(self, pairs):
        ordered = sorted((freq, height) for height, freq in pairs)
        self.cal_freqs = [freq for freq, _height in ordered]
        self.cal_zpos = [height for _freq, height in ordered]


# Проверка тех же команд и интерполяции на классах закреплённого Klipper.
eddy_source = os.environ.get('PRINT_CANCEL_EDDY_SOURCE')
if eddy_source:
    tree = ast.parse(Path(eddy_source).read_text(encoding='utf-8'))
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)
               and node.name in ('DummyDriftCompensation', 'EddyCalibration', 'EddyCalibrationTool')]
    namespace = {'bisect': bisect, 'OUT_OF_RANGE': 99.9}
    exec(compile(ast.Module(body=classes, type_ignores=[]), eddy_source, 'exec'), namespace)
    Calibration = namespace['EddyCalibration']

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
    command_error = CommandError
    # Блок 2: Модель SD-печати с реальной конкуренцией за G-code mutex.
    def __init__(self):
        self.reactor = Reactor()
        self.commands = []
        self.shutdowns = []
        self.state = 'printing'
        self.from_sd = False
        self.cleanup_error = False
        self.cancel_reason = None
        self.staging_error = False
        self.pending_calibration = None
        self.move_waits = 0
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
        self.origin = SimpleNamespace(z=0.)
        config = SimpleNamespace(get_printer=lambda: self, error=ValueError,
                                 get=lambda _key, _default=None: '2:100,1:200,0:300')
        self.calibration = Calibration(config)
        self.objects = {'heaters': self.heaters, 'pause_resume': self.pause,
                        'virtual_sdcard': self.sd,
                        'toolhead': SimpleNamespace(get_last_move_time=lambda: 0., wait_moves=self.wait_moves),
                        'print_stats': SimpleNamespace(get_status=lambda _time: {'state': self.state}),
                        'gcode_move': SimpleNamespace(get_status=lambda: {'homing_origin': self.origin}),
                        'probe_eddy_current btt_eddy': SimpleNamespace(calibration=self.calibration),
                        'configfile': SimpleNamespace(set=self.stage_calibration)}
        self.gcode = GCode(self)
        self.command_error = self.gcode.error
        self.objects['gcode'] = self.gcode
        for name in ('M109', 'M190', 'TEMPERATURE_WAIT', 'CANCEL_PRINT',
                     'TURN_OFF_HEATERS', 'CANCEL_PRINT_BASE', 'RESET', 'PURGE',
                     'START_PRINT', 'BLOCK_MOVE', 'NOOP', 'FAIL', 'Z_OFFSET_APPLY_PROBE', 'SET_GCODE_OFFSET'):
            self.gcode.register_command(name, self.dispatch)
        if eddy_source:
            self.eddy_tool = namespace['EddyCalibrationTool'].__new__(namespace['EddyCalibrationTool'])
            self.eddy_tool.printer = self
            self.eddy_tool.name = 'probe_eddy_current btt_eddy'
            self.eddy_tool.calibration = self.calibration
        self.extra = module.TreedPrintCancel(config)
        self.extra._connect()
        if source:
            self.gcode._handle_ready()

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)

    def wait_moves(self):
        self.move_waits += 1

    def stage_calibration(self, _section, _option, value):
        if self.staging_error:
            raise self.gcode.error('Ошибка подготовки configfile')
        self.pending_calibration = value

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
            self.cancel_reason = command.get('REASON', 'operator')
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
        elif name == 'Z_OFFSET_APPLY_PROBE':
            if eddy_source:
                self.eddy_tool.cmd_Z_OFFSET_APPLY_PROBE(command)
            else:
                freqs, heights = self.calibration.get_calibration()
                self.stage_calibration('', '', ','.join('%.6f:%.3f' % (height - self.origin.z, freq)
                                                       for height, freq in zip(heights, freqs)))
        elif name == 'SET_GCODE_OFFSET':
            self.origin.z = command.get_float('Z')

    def cancel_wait(self, script, event=None, cancel_script='CANCEL_PRINT'):
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
                self.gcode.run_script(cancel_script)
            except BaseException as exc:
                errors.append(exc)
            finally:
                cancelled.set()

        cancel_thread = threading.Thread(target=cancel, daemon=True)
        cancel_thread.start()
        return worker, cancel_thread, cancelled, errors


class PrintCancelTests(unittest.TestCase):
    # Блок 3: Прерывание, валидация и восстановление после отмены.
    def test_cancel_with_reason_interrupts_wait_and_preserves_parameter(self):
        rig = Rig()
        worker, cancel, done, errors = rig.cancel_wait('M190\nPURGE',
                                                     cancel_script='CANCEL_PRINT REASON=spaghetti')
        worker.join(3.)
        cancel.join(3.)
        self.assertTrue(done.is_set())
        self.assertEqual(errors, [])
        self.assertEqual(rig.cancel_reason, 'spaghetti')
        self.assertEqual(rig.state, 'cancelled')
        self.assertNotIn('PURGE', rig.commands)

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

    # Блок 4: Следующий probe использует новую кривую, запись на диск отложена.
    def test_cancelled_offset_updates_live_and_pending_calibration_once(self):
        for offset in (-0.15, 0.15):
            with self.subTest(offset=offset):
                rig = Rig()
                rig.gcode.run_script('CANCEL_PRINT')
                rig.origin.z = 0.03
                rig.gcode.run_script('_TREED_EDDY_APPLY_LIVE_Z_OFFSET Z=%s' % offset)
                self.assertEqual(rig.origin.z, 0.03)
                self.assertEqual(rig.calibration.cal_freqs, [100., 200., 300.])
                self.assertEqual(rig.calibration.cal_zpos, [2. - offset, 1. - offset, -offset])
                pending = sorted([list(map(float, pair.split(':')))
                                  for pair in rig.pending_calibration.split(',')], key=lambda pair: pair[1])
                self.assertEqual(pending, [[2. - offset, 100.], [1. - offset, 200.], [-offset, 300.]])
                # Следующий G28 сбросит live offset, но сохранит изменённую кривую.
                rig.origin.z = 0.
                rig.gcode.run_script('_TREED_EDDY_APPLY_LIVE_Z_OFFSET Z=0')
                self.assertEqual(rig.commands.count('Z_OFFSET_APPLY_PROBE'), 1)
                self.assertEqual(rig.move_waits, 1)
                if eddy_source:
                    self.assertAlmostEqual(rig.calibration.freq_to_height(150.), 1.5 - offset)
                self.assertFalse(rig.shutdowns)

    def test_offsets_from_successive_prints_accumulate(self):
        rig = Rig()
        rig.gcode.run_script('CANCEL_PRINT')
        for offset in (0.15, -0.05):
            rig.gcode.run_script('_TREED_EDDY_APPLY_LIVE_Z_OFFSET Z=%s' % offset)
        for actual, expected in zip(rig.calibration.cal_zpos, (1.9, 0.9, -0.1)):
            self.assertAlmostEqual(actual, expected)
        pending = sorted([list(map(float, pair.split(':')))
                          for pair in rig.pending_calibration.split(',')], key=lambda pair: pair[1])
        self.assertEqual(pending, [[1.9, 100.], [0.9, 200.], [-0.1, 300.]])

    def test_live_offset_rejects_active_print_and_invalid_state_before_staging(self):
        for state, current_file in (('printing', None), ('paused', None), ('cancelled', object())):
            rig = Rig()
            rig.state, rig.sd.current_file = state, current_file
            rig.origin.z = 0.15
            with self.assertRaises(rig.gcode.error):
                rig.gcode.run_script('_TREED_EDDY_APPLY_LIVE_Z_OFFSET Z=0.15')
            self.assertIsNone(rig.pending_calibration)
            self.assertEqual(rig.calibration.cal_zpos, [2., 1., 0.])
        for invalid in ('nan', 'inf', '-inf', 'uncalibrated', 'incompatible'):
            rig = Rig()
            rig.gcode.run_script('CANCEL_PRINT')
            rig.origin.z = 0.15
            if invalid == 'uncalibrated':
                rig.calibration._load_calibration([])
            elif invalid == 'incompatible':
                rig.calibration._load_calibration = None
            else:
                rig.origin.z = float(invalid)
            before = rig.calibration.get_calibration()
            with self.assertRaises(rig.gcode.error):
                rig.gcode.run_script('_TREED_EDDY_APPLY_LIVE_Z_OFFSET Z=%s' % rig.origin.z)
            self.assertIsNone(rig.pending_calibration)
            self.assertEqual(rig.calibration.get_calibration(), before)

    def test_staging_failure_leaves_live_calibration_unchanged(self):
        rig = Rig()
        rig.gcode.run_script('CANCEL_PRINT')
        rig.origin.z = 0.03
        rig.staging_error = True
        with self.assertRaises(rig.gcode.error):
            rig.gcode.run_script('_TREED_EDDY_APPLY_LIVE_Z_OFFSET Z=0.15')
        self.assertEqual(rig.origin.z, 0.03)
        self.assertIsNone(rig.pending_calibration)
        self.assertEqual(rig.calibration.cal_zpos, [2., 1., 0.])


if __name__ == '__main__':
    unittest.main()
