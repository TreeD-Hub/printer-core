"""Подсветка: API, ready и единая очередь; read-only, без оборудования.

TREED_LIGHT_KLIPPER_SOURCE указывает каталог с output_pin.py, webhooks.py и
gcode.py из закреплённого Klipper для дополнительной проверки настоящего API.
"""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]


# Блок 1: Аппаратная граница изолирована; host-модуль загружается целиком.
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


light = load('treed_light_control', ROOT / 'klipper-host/treed_light_control.py')


class Request:
    error = ValueError

    def __init__(self, **params):
        self.params, self.response = params, None

    def get_int(self, key):
        value = self.params.get(key)
        if type(value) is not int:
            raise self.error('Invalid Argument Type')
        return value

    def send(self, response):
        self.response = response


class LightControlTests(unittest.TestCase):
    def setUp(self):
        self.pin = NS(gcrq=Mock())
        self.state = 'ready'
        self.webhooks, self.gcode = Mock(), Mock()
        objects = {'webhooks': self.webhooks, 'gcode': self.gcode}
        self.printer = NS(
            command_error=ValueError,
            get_state_message=lambda: ('state', self.state),
            lookup_object=lambda name: objects[name],
            load_object=lambda config, name: self.pin if name == 'output_pin chamber_light' else None)
        self.control = light.load_config(NS(get_printer=lambda: self.printer))

    def test_endpoint_and_legacy_command_use_the_same_pin(self):
        self.webhooks.register_endpoint.assert_called_once_with('treed/light/set', self.control.handle_set)
        self.gcode.register_command.assert_called_once_with('TREED_LIGHT_SET', self.control.cmd_set)
        for value in (1, 0, 1, 0):
            request = Request(value=value)
            self.control.handle_set(request)
            self.assertEqual(request.response, {'requested_value': value})
            self.pin.gcrq.send_async_request.assert_called_with(float(value))
        self.gcode.get_mutex.assert_not_called()

    def test_invalid_requests_do_not_schedule_mcu_commands(self):
        for params in ({}, {'value': True}, {'value': '1'}, {'value': 1.0},
                       {'value': None}, {'value': -1}, {'value': 2}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                self.control.handle_set(Request(**params))
        self.pin.gcrq.send_async_request.assert_not_called()

    def test_not_ready_rejects_api_and_legacy_command(self):
        for self.state in ('startup', 'shutdown', 'error'):
            with self.subTest(state=self.state):
                with self.assertRaisesRegex(ValueError, 'не готов'):
                    self.control.handle_set(Request(value=1))
                with self.assertRaisesRegex(ValueError, 'не готов'):
                    self.control.cmd_set(NS(get_int=lambda *a, **kw: 0))
        self.pin.gcrq.send_async_request.assert_not_called()

    def test_mcu_error_is_not_acknowledged(self):
        self.pin.gcrq.send_async_request.side_effect = ValueError('MCU unavailable')
        request = Request(value=1)
        with self.assertRaisesRegex(ValueError, 'MCU unavailable'):
            self.control.handle_set(request)
        self.assertIsNone(request.response)

    def test_loader_delivery_is_required_and_idempotent(self):
        bash = shutil.which('bash')
        if os.name == 'nt' and shutil.which('git'):
            bash = str(Path(shutil.which('git')).parent.parent / 'usr/bin/bash.exe')
        if not bash or not Path(bash).is_file():
            self.skipTest('Bash недоступен для fixture доставки')
        source = (ROOT / 'loader/steps/runtime-bootstrap.sh').read_text(encoding='utf-8')
        start = source.index('for sgt_source in ')
        loop = source[start:source.index('\ndone', start) + len('\ndone')]
        paths = loop.splitlines()[0].split(' in ', 1)[1].removesuffix('; do').split()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            extras = root / 'klipper/klippy/extras'
            extras.mkdir(parents=True)
            for relative in paths:
                path = root / 'repo' / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / relative, path)
            env = dict(os.environ, REPO_DIR=(root / 'repo').as_posix(),
                       KLIPPER_DIR=(root / 'klipper').as_posix())
            script = 'set -eu\nPI_USER="$(id -u)"\nPI_GROUP="$(id -g)"\nlog_error(){ echo "$*" >&2; }\n' + loop
            for _ in range(2):
                result = subprocess.run([bash, '-c', script], env=env, capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((extras / 'treed_light_control.py').read_bytes(),
                                 (ROOT / 'klipper-host/treed_light_control.py').read_bytes())
            (root / 'repo/klipper-host/treed_light_control.py').unlink()
            result = subprocess.run([bash, '-c', script], env=env, capture_output=True, text=True, timeout=15)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('missing klipper-host/treed_light_control.py', result.stderr)

    # Блок 2: Настоящие output_pin и WebRequest, mock только MCU/реактор/конфиг.
    @unittest.skipUnless(os.environ.get('TREED_LIGHT_KLIPPER_SOURCE'), 'Нужен исходник закреплённого Klipper')
    def test_pinned_klipper_api_bypasses_busy_gcode_and_preserves_request_order(self):
        upstream = Path(os.environ['TREED_LIGHT_KLIPPER_SOURCE'])
        package = ModuleType('light_test_extras')
        package.__path__ = []
        display = ModuleType('light_test_extras.display')
        display.display = NS()
        gcode = load('light_test_gcode', upstream / 'gcode.py')
        with patch.dict(sys.modules, {'light_test_extras': package,
                                    'light_test_extras.display': display, 'gcode': gcode}):
            output_pin = load('light_test_extras.output_pin', upstream / 'output_pin.py')
            webhooks = load('light_test_webhooks', upstream / 'webhooks.py')
        mcu = NS(min_schedule_time=lambda: .1, estimated_print_time=lambda t: t)
        hardware = Mock()
        hardware.get_mcu.return_value = mcu
        objects = {'pins': NS(setup_pin=lambda *args: hardware),
                   'gcode': self.gcode, 'template_evaluator': NS(),
                   'toolhead': Mock()}
        self.printer.lookup_object = lambda name, default=None: objects.get(name, default)
        self.printer.load_object = lambda config, name: Mock()
        self.printer.register_event_handler = Mock()
        self.printer.get_reactor = lambda: NS(monotonic=lambda: 100.)
        config = NS(get_printer=lambda: self.printer,
                    getboolean=lambda key, default: default,
                    getfloat=lambda key, default, **kw: default,
                    get=lambda key: 'PD15', get_name=lambda: 'output_pin chamber_light')
        self.control.pin = pin = output_pin.PrinterOutputPin(config)
        self.assertIsInstance(pin.gcrq, output_pin.GCodeRequestQueue)
        self.gcode.get_mutex.side_effect = AssertionError('G-code mutex is busy')
        for _ in range(3):
            # Автоматический LIGHT_ON уже исполнен, ручной off должен остаться последним.
            self.control.cmd_set(NS(get_int=lambda *a, **kw: 1))
            request = webhooks.WebRequest(None, json.dumps({
                'id': 1, 'method': 'treed/light/set', 'params': {'value': 0}}).encode())
            self.control.handle_set(request)
            self.assertEqual(request.finish(), {'id': 1, 'result': {'requested_value': 0}})
            self.assertEqual(pin.get_status(0)['value'], 0.)
            self.assertEqual(pin.gcrq.rqueue, [])
        times = [c.args[0] for c in hardware.set_digital.call_args_list]
        self.assertEqual(len(times), 6)
        self.assertTrue(all(b - a >= .099 for a, b in zip(times, times[1:])))
        objects['toolhead'].register_lookahead_callback.assert_not_called()
        for value in (True, '1', 1.0, None):
            request = webhooks.WebRequest(None, json.dumps({
                'method': 'treed/light/set', 'params': {'value': value}}).encode())
            with self.assertRaises(webhooks.WebRequestError):
                self.control.handle_set(request)


if __name__ == '__main__':
    unittest.main()
