"""Read-only: рендер purge, парковки и сохранения babystep; без устройства."""
import ast
import configparser
from copy import deepcopy
import math
from pathlib import Path
import unittest

from jinja2 import Environment, StrictUndefined


# Блок 1: Настоящие шаблоны Klipper и минимальное состояние для проверки команд.
PROFILE = Path(__file__).resolve().parents[2] / "klipper/profiles/treed_v2_corexy_v1"
CFG = configparser.ConfigParser(interpolation=None, strict=False)
for filename in ["macros_start_purge.cfg", "macros_pause_resume.cfg", "macros_core.cfg",
                 "macros_ui_tune.cfg", "probe_eddy_duo.cfg", "macros_print_flow.cfg"]:
    CFG.read(PROFILE / filename, encoding="utf-8")
ENV = Environment(variable_start_string="{", variable_end_string="}", undefined=StrictUndefined)


def fail(message):
    raise RuntimeError(message)


class Harness:
    def __init__(self, pa=0.08):
        settings = {key.removeprefix("variable_"): ast.literal_eval(value)
                    for key, value in CFG["gcode_macro _TREED_START_PURGE_CFG"].items()
                    if key.startswith("variable_")}
        position = {"x": 10.0, "y": 5.0, "z": 0.4}
        self.printer = {
            "gcode_macro _TREED_START_PURGE_CFG": settings,
            "gcode_macro _TREED_PRINT_AREA_CFG": {"print_offset_enabled": 1},
            "gcode_macro _TREED_GEOMETRY_CFG": {
                "print_min_x": 0, "print_min_y": 0, "print_size_x": 245, "print_size_y": 245},
            "gcode_macro _TREED_PAUSE_STATE": {"is_active": 0},
            "gcode_macro G28": {"xy_backoff_mm": 5.0},
            "gcode_macro _TREED_UI_TUNE_STATE": {"applied_babystep": 0.0},
            "gcode_macro _TREED_EDDY_Z_OFFSET_AUTOSAVE_STATE": {
                key.removeprefix("variable_"): ast.literal_eval(value)
                for key, value in CFG["gcode_macro _TREED_EDDY_Z_OFFSET_AUTOSAVE_STATE"].items()
                if key.startswith("variable_")},
            "toolhead": {"homed_axes": "xyz", "position": position,
                         "axis_minimum": {"x": 0, "y": 0, "z": 0},
                         "axis_maximum": {"x": 245, "y": 245, "z": 203}},
            "gcode_move": {"gcode_position": position, "speed_factor": 0.73,
                           "extrude_factor": 1.15, "absolute_coordinates": False, "absolute_extrude": True,
                           "homing_origin": {"z": 0.0}},
            "extruder": {"pressure_advance": pa, "can_extrude": True},
            "configfile": {"settings": {"extruder": {
                "filament_diameter": 1.75, "max_extrude_cross_section": 5, "pressure_advance": 0.08}}},
        }
        self.commands = []
        self.moves = []
        self.saved = {}
        self.pending_probe_offset = None
        self.saved_offsets = []

    def run(self, name, stop_after=None):
        script = ENV.from_string(CFG["gcode_macro " + name]["gcode"]).render(
            printer=self.printer, params={}, action_raise_error=fail)
        for line in script.splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            tokens = line.split()
            command = tokens[0]
            if command.startswith("_TREED_PURGE_") or command in [
                    "_TREED_EDDY_CAPTURE_LIVE_Z_OFFSET", "_TREED_EDDY_APPLY_CAPTURED_Z_OFFSET",
                    "_TREED_UI_RESET_Z_OFFSET"]:
                self.run(command)
                continue
            self.commands.append(line)
            fields = dict(token.split("=", 1) for token in tokens[1:] if "=" in token)
            gm = self.printer["gcode_move"]
            if command == "SET_GCODE_VARIABLE":
                state = self.printer.get("gcode_macro " + fields["MACRO"])
                if state is not None:
                    state[fields["VARIABLE"]] = ast.literal_eval(fields["VALUE"])
            elif command == "SET_GCODE_OFFSET" and "Z" in fields:
                gm["homing_origin"] = {"z": float(fields["Z"])}
            elif command == "Z_OFFSET_APPLY_PROBE":
                self.pending_probe_offset = gm["homing_origin"]["z"]
            elif command == "SAVE_CONFIG":
                self.saved_offsets.append(self.pending_probe_offset)
            elif command == "M84":
                self.printer["toolhead"]["homed_axes"] = ""
            elif command == "SET_PRESSURE_ADVANCE":
                self.printer["extruder"]["pressure_advance"] = float(fields["ADVANCE"])
            elif command == "SAVE_GCODE_STATE":
                self.saved[fields["NAME"]] = {key: value for key, value in gm.items() if key != "gcode_position"}
            elif command == "RESTORE_GCODE_STATE":
                gm.update(self.saved[fields["NAME"]])
            elif command in ["M220", "M221"]:
                gm["speed_factor" if command == "M220" else "extrude_factor"] = float(tokens[1][1:]) / 100
            elif command in ["G90", "M83"]:
                gm["absolute_coordinates" if command == "G90" else "absolute_extrude"] = command == "G90"
            elif command in ["G0", "G1"]:
                axes = {token[0]: float(token[1:]) for token in tokens[1:]}
                for axis in "XYZ":
                    if axis in axes:
                        gm["gcode_position"][axis.lower()] = axes[axis]
                self.moves.append((axes, deepcopy(gm["gcode_position"]), self.printer["extruder"]["pressure_advance"]))
            if line == stop_after:
                return


# Блок 2: Одна линия, безопасные переезды и восстановление после отмены.
class PurgeTests(unittest.TestCase):
    def test_pause_uses_travel_y_min_and_preserves_explicit_override(self):
        default_y = ast.literal_eval(CFG["gcode_macro _TREED_PAUSE_PARK_CFG"]["variable_park_y_raw"])
        for travel_min, travel_max, override, expected in [(0, 245, default_y, 0), (-5, 300, default_y, -5),
                                                          (0, 300, 0, 0), (0, 300, 270, 270)]:
            with self.subTest(travel_min=travel_min, travel_max=travel_max, override=override):
                h = Harness()
                h.printer["toolhead"].update(axis_minimum={"x": 0, "y": travel_min},
                                             axis_maximum={"x": 245, "y": travel_max})
                h.printer["gcode_macro _TREED_PAUSE_PARK_CFG"] = {"park_x_raw": 122.5, "park_y_raw": override}
                h.printer["extruder"]["target"] = 220
                h.printer["heater_bed"] = {"target": 60}
                h.printer["gcode_macro _TREED_IDLE_TIMEOUT_STATE"] = {"timeout": 600}
                h.printer["gcode_macro _TREED_CAM_STATE"] = {"enabled": 0, "generation": 0}
                h.run("_TREED_PAUSE_PREP_STATE")
                self.assertIn(f"SET_GCODE_VARIABLE MACRO=_TREED_PAUSE_EXEC_STATE VARIABLE=park_y VALUE={float(expected)}",
                              h.commands)

    def test_one_line_geometry_amount_speed_and_state(self):
        h = Harness(pa=0.06)
        h.run("_TREED_LINE_PURGE")
        extrusion = [move for move in h.moves if "E" in move[0]]
        self.assertEqual(extrusion, [({"X": 132.5, "E": 30, "F": 400},
                                     {"x": 132.5, "y": 5, "z": 0.4}, 0.06)])
        self.assertEqual(h.moves[-1][1]["z"], 10)
        self.assertFalse(any(command.startswith(("SET_PRESSURE_ADVANCE", "G10", "G11"))
                             for command in h.commands))
        self.assertEqual(h.printer["extruder"]["pressure_advance"], 0.06)
        self.assertEqual(h.printer["gcode_move"]["speed_factor"], 0.73)
        self.assertEqual(h.printer["gcode_move"]["extrude_factor"], 1.15)
        self.assertFalse(h.printer["gcode_move"]["absolute_coordinates"])
        self.assertTrue(h.printer["gcode_move"]["absolute_extrude"])
        self.assertEqual(h.printer["gcode_macro _TREED_START_PURGE_CFG"]["state_saved"], 0)

    def test_line_tracks_print_geometry_and_tunable_amount(self):
        for size, origin in [(245, 0), (220, 5)]:
            with self.subTest(size=size, origin=origin):
                h = Harness()
                h.printer["gcode_macro _TREED_GEOMETRY_CFG"].update(
                    print_size_x=size, print_size_y=size, print_min_x=origin, print_min_y=origin)
                h.printer["gcode_macro _TREED_START_PURGE_CFG"]["prime_amount"] = 20
                h.run("_TREED_SMART_PARK")
                h.run("_TREED_LINE_PURGE")
                extrusion = [move for move in h.moves if "E" in move[0]]
                self.assertEqual(extrusion, [({"X": origin + 10 + size / 2, "E": 20, "F": 400},
                                             {"x": origin + 10 + size / 2,
                                              "y": origin + 5, "z": 0.4}, 0.08)])

    def test_wait_at_start_and_safe_transfers_from_other_points(self):
        for x, z in [(10, 0.4), (100, 0.4), (100, 20)]:
            with self.subTest(x=x, z=z):
                h = Harness()
                h.printer["gcode_move"]["gcode_position"].update(x=x, z=z)
                h.run("_TREED_SMART_PARK")
                self.assertEqual(h.moves[-1][1], {"x": 10, "y": 5, "z": 0.4})
                self.assertTrue(all(pos["z"] >= 10 for axes, pos, _ in h.moves if "X" in axes or "Y" in axes))
                h.moves.clear()
                h.run("_TREED_LINE_PURGE")
                self.assertEqual(h.moves[0][1]["z"], 0.4)
                self.assertTrue(all(pos["z"] >= 10 for axes, pos, _ in h.moves if "Y" in axes))
        h = Harness()
        h.printer["gcode_move"]["gcode_position"].update(x=100)
        h.run("_TREED_LINE_PURGE")
        self.assertGreaterEqual(h.moves[0][1]["z"], 10)

    def test_zero_pa_does_not_block_purge(self):
        h = Harness(pa=0)
        h.printer["configfile"]["settings"]["extruder"]["pressure_advance"] = 0
        h.run("_TREED_LINE_PURGE")
        self.assertEqual([pa for axes, _, pa in h.moves if "E" in axes], [0])
        self.assertEqual(h.printer["extruder"]["pressure_advance"], 0)

    def test_cancel_restores_pending_state_and_is_repeatable(self):
        h = Harness(pa=0.06)
        h.run("_TREED_LINE_PURGE", stop_after="M221 S100")
        self.assertEqual(h.printer["extruder"]["pressure_advance"], 0.06)
        self.assertEqual(h.printer["gcode_move"]["speed_factor"], 1)
        self.assertEqual(h.printer["gcode_move"]["extrude_factor"], 1)
        h.run("CANCEL_PRINT")
        self.assertEqual(h.printer["extruder"]["pressure_advance"], 0.06)
        self.assertEqual(h.printer["gcode_move"]["speed_factor"], 0.73)
        self.assertEqual(h.printer["gcode_move"]["extrude_factor"], 1.15)
        self.assertEqual(h.printer["gcode_macro _TREED_START_PURGE_CFG"]["state_saved"], 0)
        h.commands.clear()
        h.run("_TREED_PURGE_RESTORE_STATE")
        self.assertEqual(h.commands, [])

    def test_purge_start_uses_print_coordinates_with_raw_offset(self):
        h = Harness()
        h.printer["toolhead"]["position"] = {"x": 110, "y": 55, "z": 0.3}
        h.run("_TREED_LINE_PURGE")
        self.assertEqual(h.moves[0][1], {"x": 10, "y": 5, "z": 0.4})

    def test_cancel_saves_live_babystep_once_after_cleanup(self):
        for offset in [-0.05, 0.01, 0.05]:
            with self.subTest(offset=offset):
                h = Harness()
                h.printer["gcode_move"]["homing_origin"]["z"] = offset
                h.run("CANCEL_PRINT")
                self.assertEqual(h.saved_offsets, [offset])
                self.assertEqual(h.printer["gcode_move"]["homing_origin"]["z"], 0)
                self.assertEqual(h.printer["gcode_macro _TREED_UI_TUNE_STATE"]["applied_babystep"], 0)
                self.assertEqual(h.printer["gcode_macro _TREED_EDDY_Z_OFFSET_AUTOSAVE_STATE"]["has_pending"], 0)
                self.assertLess(h.commands.index("TURN_OFF_HEATERS"), h.commands.index("CANCEL_PRINT_BASE"))
                capture = f"SET_GCODE_VARIABLE MACRO=_TREED_EDDY_Z_OFFSET_AUTOSAVE_STATE VARIABLE=pending_z VALUE={offset}"
                self.assertLess(h.commands.index("CANCEL_PRINT_BASE"), h.commands.index(capture))
                self.assertLess(h.commands.index(capture), h.commands.index("SET_GCODE_OFFSET Z=0 MOVE=0"))
                self.assertLess(h.commands.index("M400"), h.commands.index("SAVE_CONFIG"))
                h.run("CANCEL_PRINT")
                self.assertEqual(h.saved_offsets, [offset])

    def test_cancel_captures_babystep_before_purge_state_restore(self):
        h = Harness()
        h.run("_TREED_LINE_PURGE", stop_after="M221 S100")
        h.printer["gcode_move"]["homing_origin"] = {"z": 0.05}
        h.run("CANCEL_PRINT")
        self.assertEqual(h.saved_offsets, [0.05])
        self.assertEqual(h.printer["gcode_macro _TREED_START_PURGE_CFG"]["state_saved"], 0)

    def test_cancel_preserves_offset_already_captured_by_end_print(self):
        h = Harness()
        h.printer["toolhead"]["homed_axes"] = "xy"
        h.printer["gcode_macro _TREED_EDDY_Z_OFFSET_AUTOSAVE_STATE"].update(has_pending=1, pending_z=0.03)
        h.run("CANCEL_PRINT")
        self.assertEqual(h.saved_offsets, [0.03])
        self.assertFalse(h.moves)

    def test_cancel_skips_save_without_trustworthy_or_significant_offset(self):
        for homed, offset in [("xy", 0.05), ("xyz", 0), ("xyz", 0.005), ("xyz", -0.005)]:
            with self.subTest(homed=homed, offset=offset):
                h = Harness()
                h.printer["toolhead"]["homed_axes"] = homed
                h.printer["gcode_move"]["homing_origin"]["z"] = offset
                h.run("CANCEL_PRINT")
                self.assertEqual(h.saved_offsets, [])
                self.assertIn("CANCEL_PRINT_BASE", h.commands)
                self.assertEqual(h.printer["gcode_move"]["homing_origin"]["z"], 0)

    def test_end_print_keeps_captured_offset_after_motors_are_disabled(self):
        for homed, offset in [("xyz", 0.05), ("xyz", -0.05), ("xy", 0.05)]:
            with self.subTest(homed=homed, offset=offset):
                h = Harness()
                h.printer["toolhead"]["homed_axes"] = homed
                h.printer["gcode_move"]["homing_origin"]["z"] = offset
                h.run("END_PRINT")
                self.assertEqual(h.saved_offsets, [offset] if "z" in homed else [])
                self.assertEqual(h.printer["gcode_move"]["homing_origin"]["z"], 0)
                self.assertEqual(h.printer["toolhead"]["homed_axes"], "")
                if "z" in homed:
                    self.assertLess(h.commands.index("M84"), h.commands.index("SAVE_CONFIG"))

    def test_invalid_settings_fail_before_motion_or_pa_changes(self):
        for key, value in [("heat_wait_height", 0), ("heat_wait_height", 0.5),
                           ("purge_height", -1), ("park_height", 0.4), ("park_height", 204),
                           ("prime_amount", 0), ("prime_amount", math.nan), ("prime_amount", 300),
                           ("purge_speed", 0), ("purge_speed", math.nan), ("purge_speed", math.inf),
                           ("x_inset", -1), ("x_inset", 0), ("x_inset", 123),
                           ("y_inset", -1), ("y_inset", 0), ("y_inset", 245)]:
            for macro in ["_TREED_SMART_PARK", "_TREED_LINE_PURGE"]:
                with self.subTest(key=key, value=value, macro=macro):
                    h = Harness()
                    h.printer["gcode_macro _TREED_START_PURGE_CFG"][key] = value
                    with self.assertRaises(RuntimeError):
                        h.run(macro)
                    self.assertEqual(h.commands, [])
                    self.assertEqual(h.printer["extruder"]["pressure_advance"], 0.08)
        for mode in ["cold", "unhomed", "raw", "pending"]:
            with self.subTest(mode=mode):
                h = Harness(pa=0)
                if mode == "cold":
                    h.printer["extruder"]["can_extrude"] = False
                elif mode == "unhomed":
                    h.printer["toolhead"]["homed_axes"] = "xy"
                elif mode == "raw":
                    h.printer["gcode_macro _TREED_PRINT_AREA_CFG"]["print_offset_enabled"] = 0
                else:
                    h.printer["gcode_macro _TREED_START_PURGE_CFG"]["state_saved"] = 1
                with self.assertRaises(RuntimeError):
                    h.run("_TREED_LINE_PURGE")
                self.assertEqual(h.commands, [])

    def test_invalid_filament_diameter_fails_before_state_changes(self):
        for diameter in [0, -1, math.nan, math.inf]:
            for macro in ["_TREED_SMART_PARK", "_TREED_LINE_PURGE"]:
                with self.subTest(diameter=diameter, macro=macro):
                    h = Harness()
                    h.printer["configfile"]["settings"]["extruder"]["filament_diameter"] = diameter
                    with self.assertRaises(RuntimeError):
                        h.run(macro)
                    self.assertEqual(h.commands, [])

    def test_flow_limit_and_width_reserve_fail_before_motion(self):
        for limit, inset in [(0.5, 5), (5, 0.5)]:
            for macro in ["_TREED_SMART_PARK", "_TREED_LINE_PURGE"]:
                with self.subTest(limit=limit, inset=inset, macro=macro):
                    h = Harness()
                    h.printer["configfile"]["settings"]["extruder"]["max_extrude_cross_section"] = limit
                    h.printer["gcode_macro _TREED_START_PURGE_CFG"]["y_inset"] = inset
                    with self.assertRaises(RuntimeError):
                        h.run(macro)
                    self.assertEqual(h.commands, [])


if __name__ == "__main__":
    unittest.main()
