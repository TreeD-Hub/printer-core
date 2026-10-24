"""Read-only: рендер сервисной парковки и допуска; без подключения к устройству."""
import configparser
from copy import deepcopy
import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

from jinja2 import Environment, StrictUndefined


# Блок 1: Настоящие макросы и состояние, достаточное для проверки допуска.
PROFILE = Path(__file__).resolve().parents[2] / "klipper/profiles/treed_v2_corexy_v1"
GUARD_PATH = Path(__file__).resolve().parents[2] / "klipper-host/treed_motor_guard.py"
GUARD_SPEC = importlib.util.spec_from_file_location("treed_motor_guard", GUARD_PATH)
GUARD_MODULE = importlib.util.module_from_spec(GUARD_SPEC)
GUARD_SPEC.loader.exec_module(GUARD_MODULE)
CFG = configparser.ConfigParser(interpolation=None, strict=False)
for filename in ["macros_ui_motion.cfg", "macros_core.cfg", "steppers.cfg"]:
    CFG.read(PROFILE / filename, encoding="utf-8")
ENV = Environment(variable_start_string="{", variable_end_string="}", undefined=StrictUndefined)


def fail(message):
    raise RuntimeError(message)


class Gcmd:
    def __init__(self, params):
        self.params = params

    def get_command_parameters(self):
        return self.params

    def error(self, message):
        return RuntimeError(message)


class ServiceModeTests(unittest.TestCase):
    def setUp(self):
        self.printer = {
            "toolhead": {
                "homed_axes": "",
                "position": {"x": 125.0, "y": 128.5, "z": 10.0},
                "max_velocity": 600.0,
                "axis_minimum": {axis: CFG.getfloat("stepper_" + axis, "position_min") for axis in "xyz"},
                "axis_maximum": {axis: CFG.getfloat("stepper_" + axis, "position_max") for axis in "xyz"},
            },
            "treed_driver_mode": {"mode": "normal", "service_velocity_limit": 600.0},
            "gcode_move": {"speed_factor": 1.0},
            "configfile": {"settings": {
                "stepper_x": {"position_min": CFG.getfloat("stepper_x", "position_min"),
                              "position_max": CFG.getfloat("stepper_x", "position_max")},
                "stepper_y": {"position_min": CFG.getfloat("stepper_y", "position_min"),
                              "position_max": CFG.getfloat("stepper_y", "position_max")},
                "stepper_z": {"position_min": CFG.getfloat("stepper_z", "position_min"),
                              "position_max": CFG.getfloat("stepper_z", "position_max")},
            }},
            "gcode_macro _TREED_OPERATION_STATE": {"phase": "idle"},
            "gcode_macro _TREED_UPDATE_ADMISSION_STATE": {"active": 0},
            "gcode_macro _TREED_CLOG_RECOVERY_STATE": {"active": 0},
            "print_stats": {"state": "standby"},
            "pause_resume": {"is_paused": False},
        }
        self.commands = []
        self.driver_mode = NS(state="ready", mode="normal", needs_restart=False,
                              motion={"park_velocity": 100., "resume_wipe_velocity": 150.,
                                      "start_wipe_velocity": 300., "start_wipe_accel": 12500.,
                                      "travel_velocity": 200., "z_travel_velocity": 25.})
        self.host_objects = {"treed_driver_mode": self.driver_mode,
                             "toolhead": NS(max_velocity=600.),
                             "gcode_move": NS(speed_factor=1. / 60.)}
        self.host_printer = NS(lookup_object=lambda name, default=None:
                               self.host_objects.get(name, default), is_shutdown=lambda: False)
        self.host_gcode = NS(register_command=lambda *args: None,
                             run_script_from_command=self.run_host_script)
        self.host_objects["gcode"] = self.host_gcode
        self.guard = GUARD_MODULE.load_config(NS(get_printer=lambda: self.host_printer))

    def run_host_script(self, script):
        self.commands.append(script)

    def render(self, name, params):
        script = ENV.from_string(CFG["gcode_macro " + name]["gcode"]).render(
            printer=self.printer, params=params, action_raise_error=fail)
        return [line.split("#", 1)[0].strip() for line in script.splitlines()
                if line.split("#", 1)[0].strip()]

    def run_service(self, params=None):
        for line in self.render("TREED_UI_SERVICE_MODE", params or {}):
            if line == "_TREED_OPERATION_REQUIRE OP=service_mode":
                self.render("_TREED_OPERATION_REQUIRE", {"OP": "service_mode"})
            if line.startswith("TREED_SERVICE_MOVE "):
                self.dispatch_move(line)
            else:
                self.commands.append(line)

    def dispatch_move(self, line):
        fields = dict(token.split("=", 1) for token in line.split()[1:])
        mode = self.printer["treed_driver_mode"]["mode"]
        self.driver_mode.mode = mode
        self.host_objects["toolhead"].max_velocity = min(
            self.printer["toolhead"]["max_velocity"],
            180.0 if mode == "quiet" else self.printer["toolhead"]["max_velocity"])
        self.host_objects["gcode_move"].speed_factor = self.printer["gcode_move"]["speed_factor"] / 60.
        self.guard.cmd_service_move(Gcmd(fields))

    def run_move_axis(self, params):
        self.commands = self.render("TREED_UI_MOVE_AXIS", params)
        macro_commands, self.commands = self.commands, []
        for line in macro_commands:
            if line.startswith("TREED_SERVICE_MOVE "):
                self.dispatch_move(line)
            else:
                self.commands.append(line)
        return next(line for line in self.commands if line.startswith("G1 "))

    # Блок 2: Центр сопла вычисляется из полного хода; нижняя опора предшествует XY.
    def test_service_parks_bottom_then_centers_nozzle_without_eddy_or_heat(self):
        self.run_service()
        self.assertEqual(self.commands, [
            "_TREED_OPERATION_REQUIRE OP=service_mode",
            "TREED_Z_PARK_BOTTOM_MANUAL", "G28 X Y",
            "SAVE_GCODE_STATE NAME=TREED_UI_SERVICE_MODE_STATE", "G90",
            "SAVE_GCODE_STATE NAME=TREED_SERVICE_MOVE_STATE",
            "G1 X125.0 Y128.5 F6000",
            "RESTORE_GCODE_STATE NAME=TREED_SERVICE_MOVE_STATE MOVE=0", "M400",
            "RESTORE_GCODE_STATE NAME=TREED_UI_SERVICE_MODE_STATE MOVE=0",
        ])

    def test_center_uses_runtime_axis_limits(self):
        self.printer["toolhead"]["axis_minimum"].update(x=-10, y=20)
        self.printer["toolhead"]["axis_maximum"].update(x=240, y=220)
        self.run_service()
        self.assertIn("G1 X115.0 Y120.0 F6000", self.commands)

    def test_quiet_service_center_caps_actual_speed_without_changing_normal(self):
        for factor, expected_feed in [(0.5, 6000.0), (2.0, 5400.0)]:
            with self.subTest(factor=factor):
                self.printer["treed_driver_mode"].update(mode="quiet", service_velocity_limit=180.0)
                self.printer["gcode_move"]["speed_factor"] = factor
                self.commands = []
                self.run_service()
                move = next(line for line in self.commands if line.startswith("G1 X"))
                feed = float(move.rsplit("F", 1)[1])
                self.assertEqual(feed, expected_feed)
                self.assertLessEqual(feed * factor / 60.0, 180.0)

        self.printer["treed_driver_mode"].update(mode="normal", service_velocity_limit=600.0)
        self.printer["gcode_move"]["speed_factor"] = 2.0
        self.commands = []
        self.run_service()
        self.assertIn("G1 X125.0 Y128.5 F6000", self.commands)

    def test_quiet_manual_xy_caps_requested_feed_and_preserves_lower_request(self):
        self.printer["toolhead"]["homed_axes"] = "xyz"
        self.printer["treed_driver_mode"].update(mode="quiet", service_velocity_limit=180.0)
        self.printer["gcode_move"]["speed_factor"] = 2.0

        move = self.run_move_axis({"AXIS": "X", "DISTANCE": "10", "FEEDRATE": "4000"})
        self.assertEqual(move, "G1 X10.0 F4000")
        self.assertAlmostEqual(float(move.rsplit("F", 1)[1]) * 2.0 / 60.0, 133.33333333333334)

        move = self.run_move_axis({"AXIS": "Y", "DISTANCE": "10", "FEEDRATE": "1200"})
        self.assertEqual(move, "G1 Y10.0 F1200")
        self.assertLessEqual(float(move.rsplit("F", 1)[1]) * 2.0 / 60.0, 180.0)

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
