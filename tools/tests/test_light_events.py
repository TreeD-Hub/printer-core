"""Read-only: рендер контрактов света, событий, слоёв и допуска; без устройства."""
import configparser
from pathlib import Path
import unittest
from jinja2 import Environment, StrictUndefined

# Блок 1: Используем синтаксис шаблонов Klipper и настоящие cfg.
PROFILE = Path(__file__).resolve().parents[2] / "klipper/profiles/treed_v2_corexy_v1"
ENV = Environment(variable_start_string="{", variable_end_string="}", undefined=StrictUndefined)

def fail(message):
    raise RuntimeError(message)

def render(filename, section, printer, **params):
    cfg = configparser.ConfigParser(interpolation=None, strict=False)
    cfg.read(PROFILE / filename, encoding="utf-8")
    return ENV.from_string(cfg[section]["gcode"]).render(
        printer=printer, params=params, action_raise_error=fail)

# Блок 2: Дефолты, сохранение независимых флагов, проверка входа и guard.
class LightEventTests(unittest.TestCase):
    def render_start(self, total_layer=None, **params):
        printer = {
            "print_stats": {"info": {"total_layer": total_layer}},
            "configfile": {"settings": {"extruder": {
                "min_extrude_temp": 170, "max_extrude_cross_section": 5}}},
            "exclude_object": {"objects": [{"polygon": [[10, 10], [20, 20]]}]},
        }
        return render("macros_print_flow.cfg", "gcode_macro _TREED_START_PREP_STATE",
                      printer, BED_TEMP="60", EXTRUDER_TEMP="220", **params)

    def test_start_initializes_layers_and_preserves_native_slicer_total(self):
        for saved_total, params, expected in [
                (None, {"TOTAL_LAYER": "218"}, 218),
                (96, {}, 96), (None, {}, 0), (96, {"TOTAL_LAYER": "0"}, 0)]:
            with self.subTest(saved_total=saved_total, params=params):
                result = self.render_start(saved_total, **params)
                self.assertIn(f"SET_PRINT_STATS_INFO TOTAL_LAYER={expected} CURRENT_LAYER=0", result)

    def test_start_rejects_invalid_layer_count_before_commands(self):
        for value in ["-1", "1.5", "nan", "inf", "", "bad", "1\nG28"]:
            with self.subTest(value=value), self.assertRaisesRegex(RuntimeError, "TOTAL_LAYER"):
                self.render_start(TOTAL_LAYER=value)

    def test_changed_templates_compile(self):
        for filename in ["service_fans.cfg", "macros_events.cfg", "macros_print_flow.cfg",
                         "macros_pause_resume.cfg", "filament_sensor.cfg", "gcode_features.cfg", "macros_camera.cfg"]:
            cfg = configparser.ConfigParser(interpolation=None, strict=False)
            cfg.read(PROFILE / filename, encoding="utf-8")
            for section in cfg.sections():
                if cfg.has_option(section, "gcode"):
                    with self.subTest(filename=filename, section=section):
                        ENV.from_string(cfg[section]["gcode"])

    def test_light_defaults_and_saved_flags(self):
        for saved, startup, printing in [({}, False, True),
                ({"light_on_startup": 1, "light_on_print_start": 0}, True, False)]:
            printer = {"save_variables": {"variables": saved}}
            self.assertEqual("LIGHT_ON" in render("service_fans.cfg", "delayed_gcode _TREED_LIGHT_STARTUP", printer), startup)
            self.assertEqual("LIGHT_ON" in render("service_fans.cfg", "gcode_macro _TREED_LIGHT_PRINT_START", printer), printing)

    def test_settings_validate_before_saving(self):
        for key, variable in [("STARTUP", "light_on_startup"), ("PRINT_START", "light_on_print_start")]:
            result = render("service_fans.cfg", "gcode_macro TREED_LIGHT_SETTINGS", {}, **{key: "1"})
            self.assertIn(f"SAVE_VARIABLE VARIABLE={variable} VALUE=1", result)
            self.assertEqual(result.count("SAVE_VARIABLE"), 1)
        for params in [{"STARTUP": "2"}, {"STARTUP": "1", "PRINT_START": "bad"}, {"OTHER": "1"}]:
            with self.assertRaises(RuntimeError):
                render("service_fans.cfg", "gcode_macro TREED_LIGHT_SETTINGS", {}, **params)

    def test_event_wire_and_latest_status(self):
        printer = {"gcode_macro _TREED_EVENT": {"sequence": 6}}
        result = render("macros_events.cfg", "gcode_macro _TREED_EVENT", printer, CODE="clog_attempt", DETAIL="2/5")
        self.assertIn('MSG="v1|7|clog_attempt|2/5"', result)
        self.assertIn("VARIABLE=sequence VALUE=7", result)
        with self.assertRaises(RuntimeError):
            render("macros_events.cfg", "gcode_macro _TREED_EVENT", printer, CODE="paused", DETAIL='bad\"\nG28')

    def test_only_paused_or_idle_filament_and_no_clog(self):
        for phase, print_state, paused, clog, allowed in [
            ("idle", "standby", 0, 0, True), ("printing", "printing", 0, 0, False),
            ("paused", "paused", 1, 0, True), ("paused", "paused", 1, 1, False),
            ("preparing", "paused", 1, 0, False), ("auto_remove", "complete", 0, 0, False),
        ]:
            printer = {"gcode_macro _TREED_OPERATION_STATE": {"phase": phase},
                "print_stats": {"state": print_state}, "pause_resume": {"is_paused": paused},
                "gcode_macro _TREED_CLOG_RECOVERY_STATE": {"active": clog}}
            with self.subTest(phase=phase, clog=clog):
                if allowed:
                    render("macros_core.cfg", "gcode_macro _TREED_OPERATION_REQUIRE", printer, OP="filament")
                else:
                    with self.assertRaises(RuntimeError):
                        render("macros_core.cfg", "gcode_macro _TREED_OPERATION_REQUIRE", printer, OP="filament")

if __name__ == "__main__":
    unittest.main()
