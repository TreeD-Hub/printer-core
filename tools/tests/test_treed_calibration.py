"""Офлайн-проверка калибровок UI; read-only для проекта, без Klipper/MCU."""
import ast
from contextlib import nullcontext
import importlib.util
from pathlib import Path
import shlex
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('treed_calibration', ROOT / 'klipper-host/treed_calibration.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
SECTION = 'probe_eddy_current btt_eddy'


# Блок 1: Модель реального configfile/save_variables и исполнитель команд.
class Command:
    def __init__(self, **params):
        self.params = {key: str(value) for key, value in params.items()}
        self.messages = []

    error = staticmethod(RuntimeError)

    def get_command_parameters(self):
        return self.params

    def get_float(self, name):
        return float(self.params[name])

    def get_int(self, name, default, minval, maxval):
        value = int(self.params.get(name, default))
        if not minval <= value <= maxval:
            raise self.error('integer range')
        return value

    def respond_info(self, value):
        self.messages.append(value)


class Printer:
    def __init__(self, directory):
        self.directory = directory
        self.state = 'ready'
        self.stats = 'standby'
        self.shutdown = False
        self.paused = False
        self.manual = False
        self.temp_calibration = False
        self.pending = False
        self.distance = 35.8
        self.target = 0.
        self.temperatures = [25., 25., 25.]
        self.fan_speed = .3
        self.timeout = 600.
        self.now = 1.
        self.errors = []
        self.saved = NS(allVariables={})
        self.scripts = []
        self.fail_on = None
        self.after_wait = lambda: None
        self.config = {'settings': {
            SECTION: {'x_offset': 0., 'y_offset': -30.},
            'screws_tilt_adjust': {'screw1': [25., 100.], 'screw2': [220., 235.]},
            'extruder': {'min_extrude_temp': 170., 'max_temp': 280.,
                         'max_extrude_only_distance': 120., 'rotation_distance': 35.8}},
            'config': {SECTION: {'x_offset': '0', 'y_offset': '-30', 'calibrate': '1,100\n2,200'}},
            'save_config_pending_items': {}}
        self.callbacks = []
        self.objects = {
            'gcode': NS(register_command=lambda *args: None, run_script_from_command=self.run,
                        get_mutex=nullcontext, respond_raw=self.errors.append),
            'toolhead': NS(wait_moves=lambda: self.after_wait(), get_status=lambda now:
                           dict(axis_minimum=[0., 0., 0., 0.], axis_maximum=[250., 257., 250., 0.])),
            'gcode_macro _TREED_OPERATION_STATE': NS(variables={'phase': 'idle'}),
            'gcode_macro _TREED_EDDY_MESH_CFG': NS(variables=dict(scan_min_x=25., scan_max_x=225.,
                                                                scan_min_y=25., scan_max_y=222.)),
            'gcode_macro _TREED_EDDY_TEMPERATURE_STATE': NS(variables=dict(original_idle_timeout=600., active=0, cooling=0)),
            'save_variables': self.saved,
            'configfile': NS(get_status=self.config_status),
            'print_stats': NS(get_status=lambda now: {'state': self.stats}),
            'pause_resume': NS(pause_command_sent=False, get_status=lambda now: {'is_paused': self.paused}),
            'virtual_sdcard': NS(is_active=lambda: False),
            'manual_probe': NS(get_status=lambda now: {'is_active': self.manual}),
            'temperature_probe btt_eddy': NS(get_status=lambda now: {'in_calibration': self.temp_calibration, 'temperature': self.temperatures[0]}),
            'heater_bed': NS(get_status=lambda now: {'temperature': self.temperatures[2]}),
            'fan': NS(get_status=lambda now: {'speed': self.fan_speed}),
            'idle_timeout': NS(get_status=lambda now: {'idle_timeout': self.timeout}),
            'extruder': NS(get_status=lambda now: {'target': self.target, 'temperature': self.temperatures[1]},
                           get_heater=lambda: NS(can_extrude=True),
                           extruder_stepper=NS(stepper=NS(get_rotation_distance=lambda: (self.distance, 400))))}

    def config_status(self, now):
        return {**self.config, 'save_config_pending': self.pending}

    def get_start_args(self):
        return {'config_file': str(Path(self.directory) / 'printer.cfg')}

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)

    def load_object(self, config, name):
        return self.objects[name]

    def get_reactor(self):
        return NS(monotonic=lambda: self.now, register_callback=self.callbacks.append, NEVER=float('inf'),
                  register_timer=lambda callback, wake: NS(callback=callback, wake=wake),
                  update_timer=lambda timer, wake: setattr(timer, 'wake', wake))

    def register_event_handler(self, *args):
        pass

    def is_shutdown(self):
        return self.shutdown

    def get_state_message(self):
        return ('', self.state)

    def invoke_shutdown(self, message):
        self.shutdown = True

    def run(self, script):
        self.scripts.append(script)
        if self.fail_on and self.fail_on in script:
            raise RuntimeError('mock failure')
        for line in script.splitlines():
            if line.startswith('SAVE_VARIABLE '):
                values = dict(part.split('=', 1) for part in shlex.split(line)[1:])
                self.saved.allVariables[values['VARIABLE']] = ast.literal_eval(values['VALUE'])
            elif line.startswith('SET_GCODE_VARIABLE '):
                values = dict(part.split('=', 1) for part in shlex.split(line)[1:])
                self.objects['gcode_macro ' + values['MACRO']].variables[values['VARIABLE']] = ast.literal_eval(values['VALUE'])
            elif line.startswith('SET_EXTRUDER_ROTATION_DISTANCE '):
                self.distance = float(line.split('DISTANCE=')[1])
            elif line.startswith('M106 S'):
                self.fan_speed = float(line[6:]) / 255.
            elif line.startswith('SET_IDLE_TIMEOUT TIMEOUT='):
                self.timeout = float(line.split('=')[1])
            elif line.startswith('TEMPERATURE_PROBE_CALIBRATE '):
                self.temp_calibration = self.manual = True


# Блок 2: Границы сохранения и совпадение загруженной высоты при новом ready.
class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.printer = Printer(self.directory.name)
        self.extra = module.TreedCalibration(NS(get_printer=lambda: self.printer))
        self.path = Path(self.extra.overrides_path)

    def test_override_preserves_text_bom_newlines_and_other_options(self):
        text = '\ufeff# Пользователь\r\n[extruder]\r\nrotation_distance: 35.8 # gear\r\npressure_advance: .08\r\n\r\n[bed_mesh]\r\nspeed: 120\r\n'
        result = module.update_overrides(text, 'extruder', {'rotation_distance': '36'})
        self.assertEqual(result, text.replace('35.8 # gear', '36 # gear'))
        self.assertIn('[bed_mesh]\r\nspeed: 120', result)

    def test_override_rejects_ambiguous_or_multiline_numeric_options(self):
        for text in ('[extruder]\n[extruder]\n',
                     '[extruder]\nrotation_distance: 35\nrotation_distance: 36\n',
                     '[extruder]\nrotation_distance:\n  35\n'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                module.update_overrides(text, 'extruder', {'rotation_distance': '36'})

    def test_atomic_failure_preserves_previous_file(self):
        self.path.write_text('[extruder]\nrotation_distance: 35.8\n', encoding='utf-8')
        original = self.path.read_bytes()
        with patch.object(module.os, 'replace', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(RuntimeError, 'не записана'):
                self.extra.cmd_rotation(Command(DISTANCE=36, SAVE=1))
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.glob('.treed-calibration-*')), [])
        self.assertNotIn('RESTART', self.printer.scripts)

    def test_concurrent_editor_change_is_preserved(self):
        self.path.write_text('[extruder]\nrotation_distance: 35.8\n', encoding='utf-8')
        chmod = module.os.chmod

        def edit_while_writing(path, mode):
            self.path.write_text('# external edit\n[extruder]\nrotation_distance: 37\n', encoding='utf-8')
            chmod(path, mode)

        with patch.object(module.os, 'chmod', side_effect=edit_while_writing):
            with self.assertRaisesRegex(RuntimeError, 'другим редактором'):
                self.extra.cmd_rotation(Command(DISTANCE=36, SAVE=1))
        self.assertIn('# external edit', self.path.read_text(encoding='utf-8'))
        self.assertNotIn('RESTART', self.printer.scripts)

    def test_offsets_invalidate_even_unchanged_geometry_and_require_fresh_stamp(self):
        self.printer.saved.allVariables.update(treed_eddy_height_hash=module.calibration_hash('1,100\n2,200'),
                                              treed_eddy_height_x=0., treed_eddy_height_y=-30.)
        self.extra.cmd_offsets(Command(X=0, Y=-30))
        self.extra.verify_height(1.)
        self.assertEqual(self.printer.saved.allVariables['treed_eddy_offsets_changed'], 1)
        self.assertEqual(self.printer.saved.allVariables['treed_eddy_primary_done'], 0)
        self.assertIn('y_offset: -30', self.path.read_text(encoding='utf-8'))
        self.assertEqual(self.printer.scripts[-1], 'RESTART')

    def test_geometry_rejects_scan_and_screw_outside_before_writes(self):
        for x, y in ((40, -30), (0, -50), (0, 24)):
            with self.subTest(x=x, y=y), self.assertRaises(RuntimeError):
                self.extra.cmd_offsets(Command(X=x, Y=y))
        self.assertEqual(self.printer.scripts, [])

    def test_mark_requires_pending_curve_and_ready_checks_hash_and_xy(self):
        with self.assertRaisesRegex(RuntimeError, 'новая кривая'):
            self.extra.cmd_height_mark(Command())
        self.printer.config['save_config_pending_items'] = {SECTION: {'calibrate': '1,101\n2,201'}}
        self.extra.cmd_height_mark(Command())
        self.assertEqual(self.printer.saved.allVariables['treed_eddy_offsets_changed'], 1)
        self.extra.verify_height(1.)
        self.assertEqual(self.printer.saved.allVariables['treed_eddy_offsets_changed'], 1)
        self.printer.config['config'][SECTION]['calibrate'] = '\n 1,101\n 2,201'
        self.printer.config['config'][SECTION]['y_offset'] = '-29'
        self.extra.verify_height(1.)
        self.assertEqual(self.printer.saved.allVariables['treed_eddy_offsets_changed'], 1)
        self.printer.config['config'][SECTION]['y_offset'] = '-30'
        self.extra.verify_height(1.)
        self.assertEqual(self.printer.saved.allVariables['treed_eddy_offsets_changed'], 0)

    def test_save_refuses_pending_config_and_post_queue_race(self):
        self.printer.pending = True
        with self.assertRaises(RuntimeError):
            self.extra.cmd_rotation(Command(DISTANCE=36, SAVE=1))
        self.printer.pending = False
        self.printer.after_wait = lambda: setattr(self.printer, 'paused', True)
        with self.assertRaises(RuntimeError):
            self.extra.cmd_offsets(Command(X=0, Y=-30))
        self.assertFalse(self.path.exists())

    def test_temperature_fit_failure_and_save_failure_never_mark_done(self):
        self.printer.objects['gcode_macro _TREED_EDDY_TEMPERATURE_STATE'].variables['active'] = 1
        with self.assertRaisesRegex(RuntimeError, 'fit/сохранение'):
            self.extra.cmd_temperature_mark(Command())
        self.assertEqual(self.printer.saved.allVariables['treed_eddy_temperature_done'], 0)
        self.assertIn('SET_IDLE_TIMEOUT TIMEOUT=600', self.printer.scripts)
        section = 'temperature_probe btt_eddy'
        self.printer.config['save_config_pending_items'][section] = dict(drift_calibration='1,2,3\n4,5,6',
                                                                        drift_calibration_min_temp='22.5')
        self.extra.cmd_temperature_mark(Command())
        self.printer.config['config'][section] = dict(drift_calibration='1,2,4\n4,5,6', drift_calibration_min_temp='22.5')
        self.extra.verify_height(1.)
        self.assertEqual(self.printer.saved.allVariables['treed_eddy_temperature_done'], 0)
        self.printer.config['config'][section]['drift_calibration'] = ' 1,2,3\n 4,5,6'
        self.extra.verify_height(1.)
        self.assertEqual(self.printer.saved.allVariables['treed_eddy_temperature_done'], 1)

    def test_ended_stock_temperature_series_still_blocks_other_calibrations(self):
        self.printer.objects['gcode_macro _TREED_EDDY_TEMPERATURE_STATE'].variables['active'] = 1
        self.assertFalse(self.printer.temp_calibration)
        for command in (
                lambda: self.extra.cmd_extrude(Command(LENGTH=100, SPEED=2, TEMP=200)),
                lambda: self.extra.cmd_rotation(Command(DISTANCE=36, SAVE=0)),
                lambda: self.extra.cmd_offsets(Command(X=0, Y=-30))):
            with self.assertRaises(RuntimeError):
                command()
        self.assertEqual(self.printer.scripts, [])
        self.assertFalse(self.path.exists())

    def test_manual_temperature_print_service_and_nonfinite_parameters_rejected(self):
        for field, value in (('stats', 'printing'), ('stats', 'paused'), ('manual', True),
                             ('temp_calibration', True), ('state', 'startup')):
            with self.subTest(field=field):
                previous = getattr(self.printer, field)
                setattr(self.printer, field, value)
                with self.assertRaises(RuntimeError):
                    self.extra.cmd_extrude(Command(LENGTH=100, SPEED=2, TEMP=200))
                setattr(self.printer, field, previous)
        for field in ('LENGTH', 'SPEED', 'TEMP'):
            values = dict(LENGTH=100, SPEED=2, TEMP=200)
            values[field] = 'nan'
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                self.extra.cmd_extrude(Command(**values))
        self.assertEqual(self.printer.scripts, [])

    def test_extrude_restores_e_mode_and_heater_after_motion_failure(self):
        self.printer.target = 185.
        self.printer.fail_on = 'G1 E'
        with self.assertRaisesRegex(RuntimeError, 'mock failure'):
            self.extra.cmd_extrude(Command(LENGTH=100, SPEED=2, TEMP=200))
        self.assertIn('M221 S100', self.printer.scripts[2])
        self.assertIn('M220 S100', self.printer.scripts[2])
        self.assertIn('RESTORE_GCODE_STATE NAME=_treed_extruder_calibration MOVE=0', self.printer.scripts)
        self.assertEqual(self.printer.scripts[-1], 'M104 S185')
        self.assertFalse(self.extra.running)

    def test_cancel_never_restores_nonzero_heat_or_forces_shutdown(self):
        self.printer.target = 185.
        cancel = NS(cancel_pending=False, command_script=self.printer.scripts.append)
        self.printer.objects['treed_print_cancel'] = cancel
        original = self.printer.run

        def run(script):
            if script.startswith('M109 '):
                cancel.cancel_pending = True
                raise RuntimeError('cancel')
            original(script)

        self.extra.gcode.run_script_from_command = run
        with self.assertRaisesRegex(RuntimeError, 'cancel'):
            self.extra.cmd_extrude(Command(LENGTH=100, SPEED=2, TEMP=200))
        self.assertEqual(self.printer.scripts[-1], 'M104 S0')
        self.assertFalse(self.printer.shutdown)

    def test_rotation_live_status_and_save_do_not_misuse_save_config(self):
        self.extra.cmd_rotation(Command(DISTANCE=36))
        self.assertEqual(self.extra.get_status(1.)['rotation_distance'], 36.)
        self.assertFalse(self.path.exists())
        with self.assertRaisesRegex(RuntimeError, 'более чем вдвое'):
            self.extra.cmd_rotation(Command(DISTANCE=100))
        self.extra.cmd_rotation(Command(DISTANCE=36, SAVE=1))
        self.assertIn('rotation_distance: 36', self.path.read_text(encoding='utf-8'))
        self.assertEqual(self.printer.scripts[-1], 'RESTART')
        self.assertNotIn('SAVE_CONFIG', self.printer.scripts)

    def test_required_delivery_and_profile_activation(self):
        delivery = (ROOT / 'loader/steps/runtime-bootstrap.sh').read_text(encoding='utf-8')
        self.assertEqual(delivery.count("'/klippy/extras/treed_calibration.py'"), 2)
        self.assertIn('klipper-host/treed_calibration.py', delivery)
        profile = (ROOT / 'klipper/profiles/treed_v2_corexy_v1/ui.cfg').read_text(encoding='utf-8')
        self.assertIn('[treed_calibration]', profile)

    def test_status_publishes_actual_stock_objects_without_get_status(self):
        self.printer.objects['input_shaper'] = NS(get_shapers=lambda: [NS(axis='x', params=NS(
            get_status=lambda: dict(shaper_type='mzv', shaper_freq='42.500')))])
        self.printer.objects['adxl345'] = NS()
        actual = self.extra.get_status(1.)
        self.assertEqual(actual['input_shaper']['shaper_freq_x'], '42.500')
        self.assertEqual(actual['input_shaper']['shaper_type_x'], 'mzv')
        self.assertTrue(actual['accelerometer_configured'])


# Блок 3: Охлаждение проверяется реальными обработчиками с виртуальным временем.
class CoolingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.printer = Printer(self.directory.name)
        self.printer.saved.allVariables['treed_eddy_primary_done'] = 1
        self.extra = module.TreedCalibration(NS(get_printer=lambda: self.printer))

    def begin(self, temperatures=(45., 100., 45.)):
        self.started = self.printer.now
        self.printer.temperatures = list(temperatures)
        self.extra.cmd_temperature_prepare(Command())

    def tick(self, seconds, temperatures):
        self.printer.now = self.started + seconds
        self.printer.temperatures = list(temperatures)
        return self.extra.cooling_tick(self.printer.now)

    def assert_cleaned(self):
        self.assertIsNone(self.extra.cooling)
        self.assertAlmostEqual(self.printer.fan_speed, .3)
        self.assertEqual(self.printer.timeout, 600.)
        self.assertEqual(self.printer.objects['gcode_macro _TREED_OPERATION_STATE'].variables['phase'], 'idle')
        self.assertEqual(self.printer.objects['gcode_macro _TREED_EDDY_TEMPERATURE_STATE'].variables['active'], 0)
        self.assertEqual(self.printer.saved.allVariables['treed_eddy_operator_prompt'], 'none')

    def test_room_temperature_up_to_40_starts_without_cooling(self):
        self.begin((39.9, 40., 35.))
        self.assertIsNone(self.extra.cooling)
        self.assertTrue(self.printer.temp_calibration)
        self.assertEqual(self.printer.fan_speed, .3)
        self.assertIn('TARGET=56 STEP=4', '\n'.join(self.printer.scripts))

    def test_hot_components_cool_before_series_and_restore_fan(self):
        self.begin()
        self.assertEqual(self.printer.fan_speed, 1.)
        self.assertEqual(self.printer.saved.allVariables['treed_eddy_operator_prompt'], 'cooling')
        self.assertFalse(self.printer.temp_calibration)
        self.assertIn('M104 S0\nM140 S0', '\n'.join(self.printer.scripts))
        self.assertEqual(self.tick(60, (44.5, 90., 44.5)), 66.)
        self.assertEqual(self.tick(65, (40., 39., 40.)), float('inf'))
        self.assertTrue(self.printer.temp_calibration)
        self.assertAlmostEqual(self.printer.fan_speed, .3)
        self.assertEqual(self.printer.timeout, 36000.)
        self.assertEqual(self.printer.saved.allVariables['treed_eddy_operator_prompt'], 'temperature_points')

    def test_no_progress_of_each_hot_component_reports_error(self):
        for index, name in enumerate(('датчик', 'сопло', 'стол')):
            with self.subTest(name=name):
                temperatures = [44., 44., 44.]
                self.begin(temperatures)
                temperatures = [43., 43., 43.]
                temperatures[index] = 44.
                self.assertEqual(self.tick(60, temperatures), float('inf'))
                self.assert_cleaned()
                self.assertIn(name, self.printer.errors[-1])
                self.assertFalse(self.printer.temp_calibration)

    def test_cooled_component_does_not_block_progress_of_hot_nozzle(self):
        self.begin((39., 100., 35.))
        self.assertEqual(self.tick(60, (39., 95., 35.)), 66.)
        self.extra.cmd_cooling_abort(Command())
        self.assert_cleaned()

    def test_slow_progress_still_has_ten_minute_deadline(self):
        self.begin((50., 50., 50.))
        for seconds in range(60, 600, 60):
            self.assertNotEqual(self.tick(seconds, [50. - seconds / 60. * .3] * 3), float('inf'))
        self.assertEqual(self.tick(600, (47., 47., 47.)), float('inf'))
        self.assert_cleaned()
        self.assertIn('10 минут', self.printer.errors[-1])

    def test_duplicate_start_does_not_change_cooling(self):
        self.begin()
        scripts = list(self.printer.scripts)
        with self.assertRaises(RuntimeError):
            self.extra.cmd_temperature_prepare(Command())
        self.assertEqual(self.printer.scripts, scripts)
        self.extra.cmd_cooling_abort(Command())
        self.assert_cleaned()
        self.assertEqual(self.tick(60, (30., 30., 30.)), float('inf'))
        self.assertFalse(self.printer.temp_calibration)

    def test_idle_and_primary_guards_precede_cooling(self):
        for attribute in ('pending', 'paused', 'manual', 'temp_calibration', 'shutdown'):
            with self.subTest(attribute=attribute):
                setattr(self.printer, attribute, True)
                with self.assertRaises(RuntimeError):
                    self.begin()
                self.assertEqual(self.printer.scripts, [])
                setattr(self.printer, attribute, False)
        self.printer.saved.allVariables['treed_eddy_primary_done'] = 0
        with self.assertRaises(RuntimeError):
            self.begin()
        self.assertEqual(self.printer.scripts, [])

    def test_lost_idle_or_temperature_cancels_without_motion(self):
        self.begin()
        self.printer.pending = True
        self.tick(5, (44., 95., 44.))
        self.assert_cleaned()
        self.assertFalse(self.printer.temp_calibration)
        self.printer.pending = False
        self.begin()
        self.tick(5, (float('nan'), 90., 44.))
        self.assert_cleaned()
        self.assertIn('неизвестны', self.printer.errors[-1])

    def test_shutdown_disarms_timer_without_sending_gcode(self):
        self.begin()
        scripts = list(self.printer.scripts)
        self.printer.shutdown = True
        self.extra.cooling_shutdown()
        self.assertIsNone(self.extra.cooling)
        self.assertEqual(self.extra.cooling_timer.wake, float('inf'))
        self.assertEqual(self.printer.scripts, scripts)

    def test_restore_failure_shuts_down_instead_of_leaving_a_background_attempt(self):
        self.begin()
        self.printer.fail_on = 'M106 S76.5'
        with self.assertLogs(level='ERROR'):
            self.tick(60, (45., 100., 45.))
        self.assertTrue(self.printer.shutdown)
        self.assertIsNone(self.extra.cooling)
        self.assertEqual(self.extra.cooling_timer.wake, float('inf'))

    def test_setup_and_series_failure_restore_state(self):
        self.printer.fail_on = 'M106 S255'
        with self.assertRaisesRegex(RuntimeError, 'mock failure'):
            self.begin()
        self.assert_cleaned()
        self.printer.fail_on = None
        self.begin()
        self.printer.fail_on = 'G28'
        self.tick(5, (39., 39., 39.))
        self.assert_cleaned()
        self.assertFalse(self.printer.temp_calibration)


if __name__ == '__main__':
    unittest.main()
