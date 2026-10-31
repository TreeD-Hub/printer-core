# Назначение: проверить границу целей хотэнда, PID и доставку расширения.
# Контур: read-only, без устройства; HOTEND_KLIPPER_SOURCE включает настоящие
# обработчики проверяемой версии Klipper с заменой аппаратных объектов.
import ast
import importlib.util
import logging
import os
from pathlib import Path
import re
import shlex
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


# Блок 1: Загрузка расширения и модели аппаратного нагревателя.
ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    'treed_hotend_limit', ROOT / 'klipper-host/treed_hotend_limit.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Heater:
    min_temp = 0.
    max_temp = 300.
    target_temp = 0.

    def set_temp(self, target):
        if target and not self.min_temp <= target <= self.max_temp:
            raise ValueError('Requested temperature out of range')
        self.target_temp = target

    def alter_target(self, target):
        self.target_temp = max(self.min_temp, min(self.max_temp, target)) if target else 0.


class HotendLimitTests(unittest.TestCase):
    def setUp(self):
        self.heater = Heater()
        self.pid = Mock()
        self.gcode = Mock()
        self.gcode.register_command.side_effect = lambda name, callback, **kw: self.pid if callback is None else None
        self.gcode.create_gcode_command.side_effect = lambda name, line, params: SimpleNamespace(
            get=lambda key: params[key], get_float=lambda key: float(params[key]),
            get_int=lambda key, default: int(params.get(key, default)),
            get_command_parameters=lambda: params, respond_info=Mock())
        self.heaters = SimpleNamespace(lookup_heater=lambda name: self.heater)
        self.objects = {'gcode': self.gcode, 'heaters': self.heaters}
        self.printer = SimpleNamespace(
            lookup_object=lambda name: self.objects[name],
            load_object=Mock(), register_event_handler=Mock(), command_error=ValueError)
        self.config = SimpleNamespace(get_printer=lambda: self.printer,
                                      getfloat=lambda *args, **kwargs: 280., error=ValueError)
        self.limit = module.load_config(self.config)
        self.limit._connect()

    # Блок 2: Нормализация, выключение и сохранение штатной валидации.
    def test_targets_and_internal_updates_are_bounded(self):
        for setter in (self.heater.set_temp, self.heater.alter_target):
            for requested, expected in ((250, 250), (280, 280), (280.1, 280), (390, 280), (0, 0)):
                with self.subTest(setter=setter.__name__, requested=requested):
                    setter(requested)
                    self.assertEqual(self.heater.target_temp, expected)
        self.assertEqual(self.heater.max_temp, 300)
        self.gcode.respond_info.assert_called()

    def test_invalid_target_leaves_existing_target_unchanged(self):
        self.heater.set_temp(250)
        for value in (-1, float('nan'), float('inf'), float('-inf')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.heater.set_temp(value)
            self.assertEqual(self.heater.target_temp, 250)

    def test_pid_target_is_normalized_before_stock_handler(self):
        for heater, expected in (('extruder', '280.0'), ('heater_bed', '390')):
            params = {'HEATER': heater, 'TARGET': '390', 'WRITE_FILE': '1'}
            command = SimpleNamespace(get=lambda name: params[name],
                                      get_float=lambda name: float(params[name]),
                                      get_command_parameters=lambda: params,
                                      respond_info=Mock())
            self.gcode.register_command.call_args.args[1](command)
            self.assertEqual(params['TARGET'], expected)
            self.assertEqual(params['WRITE_FILE'], '1')
            forwarded = self.pid.call_args.args[0]
            self.assertEqual(forwarded.get('TARGET'), expected)
            self.assertEqual(forwarded.get('WRITE_FILE'), '1')

    def test_late_pid_registration_completes_before_limit_connects(self):
        callbacks, commands = [], {}
        self.printer.register_event_handler.side_effect = lambda event, callback: callbacks.append(callback)

        def load_pid(config, name):
            if name not in self.objects:
                self.objects[name] = self.pid
                callbacks.append(lambda: commands.update(PID_CALIBRATE=self.pid))
            return self.objects[name]

        def register_command(name, callback, **kwargs):
            if callback is None:
                return commands.pop(name, None)
            commands[name] = callback

        self.printer.load_object = load_pid
        self.gcode.register_command.side_effect = register_command
        module.load_config(self.config)
        load_pid(self.config, 'pid_calibrate')
        for callback in callbacks:
            callback()
        self.assertTrue(callable(commands['PID_CALIBRATE']))

    def test_missing_pid_command_remains_a_configuration_error(self):
        self.gcode.register_command.side_effect = None
        self.gcode.register_command.return_value = None
        with self.assertRaisesRegex(ValueError, 'нет PID_CALIBRATE'):
            self.limit._connect()

    def test_invalid_pid_target_never_starts_calibration(self):
        command = SimpleNamespace(get=lambda name: 'extruder',
                                  get_float=lambda name: float('nan'))
        with self.assertRaises(ValueError):
            self.limit._pid_calibrate(command)
        self.pid.assert_not_called()

    def test_invalid_configuration_fails_before_hooks(self):
        for target in (301., float('nan'), float('inf')):
            self.limit.max_target = target
            self.gcode.register_command.reset_mock()
            with self.assertRaises(ValueError):
                self.limit._connect()
            self.gcode.register_command.assert_not_called()

    # Блок 3: Активный профиль, интерфейс и оба пути доставки.
    def test_profile_and_delivery(self):
        profile = ROOT / 'klipper/profiles/treed_v2_corexy_v1'
        ebb = (profile / 'ebb42_can.cfg').read_text(encoding='utf-8')
        contract = (profile / 'macros_ui_contract.cfg').read_text(encoding='utf-8')
        self.assertRegex(ebb, r'(?m)^sensor_type: Generic 3950$')
        self.assertRegex(ebb, r'(?m)^max_temp: 300$')
        self.assertRegex(ebb, r'(?m)^\[treed_hotend_limit\]\nmax_target: 280$')
        self.assertRegex(contract, r'(?m)^variable_nozzle_max_c: 280\.0$')
        loader = (ROOT / 'loader/steps/runtime-bootstrap.sh').read_text(encoding='utf-8')
        self.assertEqual(loader.count("'/klippy/extras/treed_hotend_limit.py'"), 2)
        self.assertEqual(loader.count("'/klippy/extras/__pycache__/treed_hotend_limit.*.pyc'"), 2)
        self.assertIn('klipper-host/treed_hotend_limit.py', loader.split('for sgt_source in ', 1)[1].split('; do', 1)[0])
        updater_spec = importlib.util.spec_from_loader('core_updater', loader=None)
        updater = importlib.util.module_from_spec(updater_spec)
        path = ROOT / 'runtime-scripts/treed-update/treed-core-update'
        exec(compile(path.read_text(encoding='utf-8'), str(path), 'exec'), updater.__dict__)
        self.assertIn('klipper/treed_hotend_limit.py', updater.sources(ROOT))


# Блок 4: Штатные G-code и PID обработчики; движение и ожидания заменены.
@unittest.skipUnless(os.environ.get('HOTEND_KLIPPER_SOURCE'), 'Нет исходников Klipper')
class StockKlipperTests(HotendLimitTests):
    def stock_class(self, filename, name, namespace=None):
        path = Path(os.environ['HOTEND_KLIPPER_SOURCE']) / filename
        tree = ast.parse(path.read_text(encoding='utf-8'))
        definition = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name)
        context = namespace or {}
        exec(compile(ast.Module(body=[definition], type_ignores=[]), str(path), 'exec'), context)
        return context[name]

    def setUp(self):
        super().setUp()
        stock = self.stock_class('heaters.py', 'Heater')
        self.heater = stock.__new__(stock)
        self.heater.printer = self.printer
        self.heater.min_temp, self.heater.max_temp = 0., 300.
        self.heater.lock = threading.Lock()
        self.heater.target_temp = 0.
        heaters_class = self.stock_class('heaters.py', 'PrinterHeaters')
        self.heaters = heaters_class.__new__(heaters_class)
        self.heaters.printer = self.printer
        self.heaters.heaters = {'extruder': self.heater}
        self.heaters._wait_for_temperature = Mock()
        self.objects['heaters'] = self.heaters
        self.limit._connect()

    def test_stock_heat_commands_and_shutdown(self):
        extruder_class = self.stock_class('extruder.py', 'PrinterExtruder')
        extruder = extruder_class.__new__(extruder_class)
        extruder.printer, extruder.heater = self.printer, self.heater
        self.objects['toolhead'] = SimpleNamespace(get_extruder=lambda: extruder,
                                                 register_lookahead_callback=Mock())
        command = SimpleNamespace(get_float=lambda name, default=0.: 390.,
                                  get_int=lambda *args, **kwargs: None)
        for handler in (extruder.cmd_M104, extruder.cmd_M109,
                        self.heater.cmd_SET_HEATER_TEMPERATURE):
            with self.subTest(handler=handler.__name__):
                handler(command)
                self.assertEqual(self.heater.target_temp, 280)
        self.heaters._wait_for_temperature.assert_called_once_with(self.heater)
        self.heaters.turn_off_all_heaters()
        self.assertEqual(self.heater.target_temp, 0)

    def test_stock_pid_creates_autotune_with_bounded_target(self):
        tune = Mock()
        tune.return_value.check_busy.return_value = False
        tune.return_value.calc_final_pid.return_value = (10., 1., 80.)
        pid_class = self.stock_class('pid_calibrate.py', 'PIDCalibrate',
                                     {'ControlAutoTune': tune, 'logging': logging})
        context = {'CommandError': ValueError, 'Coord': tuple,
                   're': re, 'shlex': shlex, 'logging': logging}
        context['GCodeCommand'] = self.stock_class('gcode.py', 'GCodeCommand', context)
        dispatch_class = self.stock_class('gcode.py', 'GCodeDispatch', context)
        events, objects = {}, {}
        printer = SimpleNamespace(
            lookup_object=lambda name: objects[name], command_error=ValueError,
            config_error=ValueError, get_start_args=lambda: {}, get_reactor=Mock(),
            register_event_handler=lambda event, callback: events.setdefault(event, []).append(callback),
            send_event=Mock(), invoke_shutdown=Mock())
        config = SimpleNamespace(get_printer=lambda: printer,
                                 getfloat=lambda *args, **kwargs: 280., error=ValueError)
        gcode = objects['gcode'] = dispatch_class(printer)
        heaters = objects['heaters'] = self.heaters
        heaters.printer = printer
        heaters.heaters = {}
        heaters.available_heaters = ['extruder', 'heater_bed']
        for name in heaters.available_heaters:
            heater = type(self.heater).__new__(type(self.heater))
            heater.printer, heater.name = printer, name
            heater.min_temp, heater.max_temp = 0., 300.
            heater.lock, heater.control = threading.Lock(), object()
            heater.target_temp = 0.
            heaters.heaters[name] = heater
        objects['toolhead'] = SimpleNamespace(get_last_move_time=Mock(),
                                              register_lookahead_callback=Mock())
        objects['configfile'] = Mock()

        def load_pid(config, name):
            if name not in objects:
                objects[name] = pid_class(config)
            return objects[name]

        printer.load_object = load_pid
        module.load_config(config)
        load_pid(config, 'pid_calibrate')
        for callback in events['klippy:connect']:
            callback()
        gcode._handle_ready()
        for name, target, expected in (('extruder', 390, 280), ('extruder', 250, 250),
                                        ('heater_bed', 95, 95)):
            tune.reset_mock()
            heaters._wait_for_temperature.reset_mock()
            gcode.run_script_from_command(
                f'PID_CALIBRATE HEATER={name} TARGET={target} WRITE_FILE=0')
            tune.assert_called_once_with(heaters.heaters[name], expected)
            heaters._wait_for_temperature.assert_called_once_with(heaters.heaters[name])
            objects['configfile'].set.assert_any_call(name, 'control', 'pid')
        for params in ('HEATER=missing TARGET=200', 'HEATER=extruder TARGET=nan'):
            tune.reset_mock()
            with self.assertRaises(ValueError):
                gcode.run_script_from_command('PID_CALIBRATE ' + params)
            tune.assert_not_called()
        printer.invoke_shutdown.assert_not_called()


if __name__ == '__main__':
    unittest.main()
