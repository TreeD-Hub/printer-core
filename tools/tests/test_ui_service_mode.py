"""Read-only: рендер сервисной парковки и допуска; без подключения к устройству."""
import configparser
from copy import deepcopy
from pathlib import Path
import unittest

from jinja2 import Environment, StrictUndefined


# Блок 1: Настоящие макросы и состояние, достаточное для проверки допуска.
PROFILE = Path(__file__).resolve().parents[2] / "klipper/profiles/treed_v2_corexy_v1"
CFG = configparser.ConfigParser(interpolation=None, strict=False)
for filename in ["macros_ui_motion.cfg", "macros_core.cfg", "steppers.cfg"]:
    CFG.read(PROFILE / filename, encoding="utf-8")
ENV = Environment(variable_start_string="{", variable_end_string="}", undefined=StrictUndefined)


def fail(message):
    raise RuntimeError(message)


class ServiceModeTests(unittest.TestCase):
    def setUp(self):
        self.printer = {
            "toolhead": {
                "homed_axes": "",
                "axis_minimum": {axis: CFG.getfloat("stepper_" + axis, "position_min") for axis in "xyz"},
                "axis_maximum": {axis: CFG.getfloat("stepper_" + axis, "position_max") for axis in "xyz"},
            },
            "gcode_macro _TREED_OPERATION_STATE": {"phase": "idle"},
            "gcode_macro _TREED_CLOG_RECOVERY_STATE": {"active": 0},
            "print_stats": {"state": "standby"},
            "pause_resume": {"is_paused": False},
        }
        self.commands = []

    def render(self, name, params):
        script = ENV.from_string(CFG["gcode_macro " + name]["gcode"]).render(
            printer=self.printer, params=params, action_raise_error=fail)
        return [line.split("#", 1)[0].strip() for line in script.splitlines()
                if line.split("#", 1)[0].strip()]

    def run_service(self, params=None):
        for line in self.render("TREED_UI_SERVICE_MODE", params or {}):
            if line == "_TREED_OPERATION_REQUIRE OP=service_mode":
                self.render("_TREED_OPERATION_REQUIRE", {"OP": "service_mode"})
            self.commands.append(line)

    # Блок 2: Центр сопла вычисляется из полного хода; нижняя опора предшествует XY.
    def test_service_parks_bottom_then_centers_nozzle_without_eddy_or_heat(self):
        self.run_service()
        self.assertEqual(self.commands, [
            "_TREED_OPERATION_REQUIRE OP=service_mode",
            "TREED_Z_PARK_BOTTOM_MANUAL", "G28 X Y",
            "SAVE_GCODE_STATE NAME=TREED_UI_SERVICE_MODE_STATE", "G90",
            "G1 X125.0 Y128.5 F6000", "M400",
            "RESTORE_GCODE_STATE NAME=TREED_UI_SERVICE_MODE_STATE MOVE=0",
        ])

    def test_center_uses_runtime_axis_limits(self):
        self.printer["toolhead"]["axis_minimum"].update(x=-10, y=20)
        self.printer["toolhead"]["axis_maximum"].update(x=240, y=220)
        self.run_service()
        self.assertIn("G1 X115.0 Y120.0 F6000", self.commands)

    # Блок 3: Запреты проверяются до первой команды движения.
    def test_busy_or_unknown_operation_has_no_side_effects(self):
        initial = deepcopy(self.printer)
        cases = [("gcode_macro _TREED_OPERATION_STATE", "phase", state)
                 for state in ["preparing", "printing", "paused", "calibrating", "auto_remove", "unknown"]]
        cases += [("print_stats", "state", state) for state in ["printing", "paused"]]
        cases += [("pause_resume", "is_paused", True),
                  ("gcode_macro _TREED_CLOG_RECOVERY_STATE", "active", 1)]
        for section, key, value in cases:
            with self.subTest(section=section, value=value):
                self.printer = deepcopy(initial)
                self.printer[section][key] = value
                self.commands = []
                with self.assertRaises(RuntimeError):
                    self.run_service()
                self.assertEqual(self.commands, [])

    def test_bad_limits_and_parameters_fail_before_commands(self):
        for low, high in [(0, 0), (10, 5), (0, float("nan")), (0, float("inf"))]:
            with self.subTest(low=low, high=high):
                self.printer["toolhead"]["axis_minimum"]["x"] = low
                self.printer["toolhead"]["axis_maximum"]["x"] = high
                with self.assertRaises(RuntimeError):
                    self.run_service()
                self.assertEqual(self.commands, [])
        with self.assertRaisesRegex(RuntimeError, "параметры не поддерживаются"):
            self.run_service({"Y": "0"})
        self.assertEqual(self.commands, [])


if __name__ == "__main__":
    unittest.main()
