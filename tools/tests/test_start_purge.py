"""Read-only: рендер чистки, purge, парковки и сохранения babystep; без устройства."""
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
                 "macros_ui_tune.cfg", "probe_eddy_duo.cfg", "macros_print_flow.cfg", "macros_utils.cfg", "geometry.cfg", "steppers.cfg"]:
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
            "gcode_macro _TREED_START_STATE": {
                key.removeprefix("variable_"): ast.literal_eval(value)
                for key, value in CFG["gcode_macro _TREED_START_STATE"].items()
                if key.startswith("variable_")},
            "gcode_macro _TREED_PRINT_AREA_CFG": {"print_offset_enabled": 1},
            "gcode_macro _TREED_GEOMETRY_CFG": {
                key.removeprefix("variable_"): ast.literal_eval(value)
                for key, value in CFG["gcode_macro _TREED_GEOMETRY_CFG"].items()
                if key.startswith("variable_")},
            "gcode_macro _TREED_PAUSE_STATE": {"is_active": 0},
            "gcode_macro G28": {"xy_backoff_mm": 5.0},
            "gcode_macro _TREED_UI_TUNE_STATE": {"applied_babystep": 0.0},
            "gcode_macro _TREED_EDDY_Z_OFFSET_AUTOSAVE_STATE": {
                key.removeprefix("variable_"): ast.literal_eval(value)
                for key, value in CFG["gcode_macro _TREED_EDDY_Z_OFFSET_AUTOSAVE_STATE"].items()
                if key.startswith("variable_")},
            "toolhead": {"homed_axes": "xyz", "position": position, "max_velocity": 600, "max_accel": 25000,
                         "axis_minimum": {axis: CFG.getfloat("stepper_" + axis, "position_min") for axis in "xyz"},
                         "axis_maximum": {axis: CFG.getfloat("stepper_" + axis, "position_max") for axis in "xyz"}},
            "gcode_move": {"gcode_position": position, "speed_factor": 0.73,
                           "extrude_factor": 1.15, "absolute_coordinates": False, "absolute_extrude": True,
                           "homing_origin": {"x": 0.0, "y": 0.0, "z": 0.0}},
            "extruder": {"pressure_advance": pa, "can_extrude": True, "temperature": 175},
            "print_stats": {"info": {"total_layer": 0}},
            "configfile": {"settings": {"extruder": {
                "filament_diameter": 1.75, "max_extrude_cross_section": 5, "pressure_advance": 0.08}}},
        }
        self.commands = []
        self.moves = []
        self.move_accels = []
        self.wait_positions = []
        self.saved = {}
        self.pending_probe_offset = None
        self.live_probe_offset = 0.0
        self.staged_offsets = []
        self.saved_offsets = []

    def run(self, name, stop_after=None, params=None):
        script = ENV.from_string(CFG["gcode_macro " + name]["gcode"]).render(
            printer=self.printer, params=params or {}, action_raise_error=fail)
        for line in script.splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            tokens = line.split()
            command = tokens[0]
            fields = dict(token.split("=", 1) for token in tokens[1:] if "=" in token)
            if command.startswith("_TREED_PURGE_") or command in [
                    "_TREED_EDDY_CAPTURE_LIVE_Z_OFFSET", "_TREED_EDDY_APPLY_CAPTURED_Z_OFFSET",
                    "_TREED_UI_RESET_Z_OFFSET", "_TREED_START_WAIT_PREHEAT_NOZZLE",
                    "_TREED_START_NOZZLE_WIPE_RUN"]:
                if self.run(command, stop_after=stop_after, params=fields):
                    return True
                continue
            self.commands.append(line)
            gm = self.printer["gcode_move"]
            if command == "SET_GCODE_VARIABLE":
                state = self.printer.get("gcode_macro " + fields["MACRO"])
                if state is not None:
                    state[fields["VARIABLE"]] = ast.literal_eval(fields["VALUE"])
            elif command == "SET_GCODE_OFFSET":
                gm["homing_origin"] = {**gm["homing_origin"],
                                       **{axis.lower(): float(fields[axis]) for axis in "XYZ" if axis in fields}}
            elif command in ["Z_OFFSET_APPLY_PROBE", "_TREED_EDDY_APPLY_LIVE_Z_OFFSET"]:
                offset = float(fields["Z"]) if command == "_TREED_EDDY_APPLY_LIVE_Z_OFFSET" else gm["homing_origin"]["z"]
                self.pending_probe_offset = self.live_probe_offset + offset
                self.staged_offsets.append(offset)
                if command == "_TREED_EDDY_APPLY_LIVE_Z_OFFSET":
                    self.live_probe_offset = self.pending_probe_offset
            elif command == "SAVE_CONFIG":
                self.saved_offsets.append(self.pending_probe_offset)
            elif command == "M84":
                self.printer["toolhead"]["homed_axes"] = ""
            elif command == "SET_PRESSURE_ADVANCE":
                self.printer["extruder"]["pressure_advance"] = float(fields["ADVANCE"])
            elif command == "SET_VELOCITY_LIMIT":
                self.printer["toolhead"]["max_accel"] = float(fields["ACCEL"])
            elif command in ["M109", "M190"]:
                self.wait_positions.append((command, deepcopy(gm["gcode_position"])))
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
                self.move_accels.append(self.printer["toolhead"]["max_accel"])
            if line == stop_after:
                return True


# Блок 2: Одна линия, безопасные переезды и восстановление после отмены.
class PurgeTests(unittest.TestCase):
    def test_wipe_parks_before_heat_and_uses_half_runtime_limits(self):
        for velocity, accel in [(200, 8000), (600, 25000)]:
            with self.subTest(velocity=velocity, accel=accel):
                h = Harness(pa=0.06)
                h.printer["toolhead"].update(max_velocity=velocity, max_accel=accel)
                h.run("_TREED_START_NOZZLE_WIPE")
                self.assertLess(h.commands.index("G0 X40 Y257.0 F6000"), h.commands.index("M109 S175.0"))
                self.assertLess(h.commands.index("M109 S175.0"), h.commands.index("G0 Z0.15 F1500"))
                self.assertEqual(h.wait_positions, [("M109", {"x": 40, "y": 257, "z": 1})])
                self.assertEqual(h.moves[0][1]["z"], 1)
                self.assertEqual(h.moves[1][1], {"x": 40, "y": 257, "z": 1})
                strokes = [move for move in h.moves if "X" in move[0] and "Z" in move[0]]
                self.assertEqual([axes for axes, _, _ in strokes],
                                 [{"X": 100, "Z": 0.5, "F": velocity * 30},
                                  {"X": 40, "Z": 0.15, "F": velocity * 30}] * 5)
                self.assertEqual([limit for (axes, _, _), limit in zip(h.moves, h.move_accels)
                                  if "X" in axes and "Z" in axes], [accel / 2] * 10)
                self.assertTrue(all(pos["y"] == 257 for _, pos, _ in strokes))
                self.assertEqual(h.moves[-1][1], {"x": 40, "y": 257, "z": 1})
                self.assertFalse(any("E" in axes for axes, _, _ in h.moves))
                self.assertEqual(h.printer["extruder"]["pressure_advance"], 0.06)
                self.assertEqual(h.printer["gcode_move"]["speed_factor"], 0.73)
                self.assertEqual(h.printer["toolhead"]["max_accel"], accel)
                self.assertFalse(h.printer["gcode_move"]["absolute_coordinates"])
                self.assertEqual(h.printer["gcode_macro _TREED_START_PURGE_CFG"]["state_saved"], 0)

    def test_wipe_rejects_unknown_axes_offsets_and_unreachable_scraper(self):
        for case in ["unhomed", "narrow_x", "short_z", "offset_x", "offset_y", "offset_z", "pending"]:
            with self.subTest(case=case):
                h = Harness()
                if case == "unhomed":
                    h.printer["toolhead"]["homed_axes"] = "xy"
                elif case == "narrow_x":
                    h.printer["toolhead"]["axis_maximum"]["x"] = 99
                elif case == "short_z":
                    h.printer["toolhead"]["axis_maximum"]["z"] = 0.9
                elif case.startswith("offset_"):
                    h.printer["gcode_move"]["homing_origin"][case[-1]] = 0.05
                else:
                    h.printer["gcode_macro _TREED_START_PURGE_CFG"]["state_saved"] = 1
                with self.assertRaises(RuntimeError):
                    h.run("_TREED_START_NOZZLE_WIPE")
                self.assertEqual(h.commands, [])

    def test_cancel_during_heat_or_wipe_restores_speed_and_accel(self):
        for stop in ["M109 S175.0", "G1 X100 Z0.5 F18000.0"]:
            with self.subTest(stop=stop):
                h = Harness()
                self.assertTrue(h.run("_TREED_START_NOZZLE_WIPE", stop_after=stop))
                self.assertEqual(h.printer["gcode_move"]["speed_factor"], 1)
                self.assertEqual(h.printer["toolhead"]["max_accel"], 25000 if stop.startswith("M109") else 12500)
                self.assertEqual(h.printer["gcode_macro _TREED_START_PURGE_CFG"]["state_saved"], 1)
                h.run("CANCEL_PRINT")
                self.assertEqual(h.printer["gcode_move"]["speed_factor"], 0.73)
                self.assertEqual(h.printer["toolhead"]["max_accel"], 25000)
                self.assertEqual(h.printer["gcode_macro _TREED_START_PURGE_CFG"]["state_saved"], 0)
                self.assertEqual(h.printer["gcode_macro _TREED_START_PURGE_CFG"]["saved_accel"], 0)

    def test_start_heats_early_waits_at_scraper_and_reprobes_after_bed_heat(self):
        h = Harness()
        h.printer["gcode_macro _TREED_START_STATE"].update(bed_temp=60, extruder_temp=170)
        h.printer["extruder"]["temperature"] = 20
        h.run("_TREED_START_MACHINE_PREP")
        self.assertIn("M104 S175", h.commands)
        self.assertIn("M140 S60.0", h.commands)
        self.assertEqual(h.wait_positions, [])
        h.run("_TREED_START_NOZZLE_WIPE")
        h.run("_TREED_START_WAIT_BED")
        self.assertEqual(h.wait_positions, [("M109", {"x": 40, "y": 257, "z": 1}),
                                            ("M190", {"x": 40, "y": 257, "z": 1})])
        start = ENV.from_string(CFG["gcode_macro START_PRINT"]["gcode"]).render(rawparams="")
        phases = ["_TREED_START_MACHINE_PREP", "_TREED_HOME_ALL", "_TREED_START_NOZZLE_WIPE", "_TREED_START_WAIT_BED", "G28 Z",
                  "_TREED_START_ADAPTIVE_MESH", "_TREED_PRINT_OFFSET_ENABLE", "_TREED_SMART_PARK",
                  "_TREED_START_FINAL_HEAT", "_TREED_LINE_PURGE"]
        self.assertEqual([start.index(phase) for phase in phases], sorted(start.index(phase) for phase in phases))

    def test_optional_mesh_validates_before_start_and_requires_objects_only_when_enabled(self):
        base = {"BED_TEMP": "60", "EXTRUDER_TEMP": "220"}
        for mesh in [None, "0", "1", "2", "0.5", "nan"]:
            for objects in [False, True]:
                with self.subTest(mesh=mesh, objects=objects):
                    h = Harness()
                    if objects:
                        h.printer["exclude_object"] = {"objects": [{"polygon": [[50, 50], [100, 50], [75, 100]]}]}
                    params = {**base, **({"MESH": mesh} if mesh is not None else {})}
                    valid = mesh == "0" or (objects and mesh in [None, "1"])
                    if not valid:
                        with self.assertRaises(RuntimeError):
                            h.run("_TREED_START_PREP_STATE", params=params)
                        self.assertEqual(h.commands, [])
                        continue
                    h.run("_TREED_START_PREP_STATE", params=params)
                    self.assertIn("BED_MESH_CLEAR", h.commands)
                    h.run("_TREED_START_ADAPTIVE_MESH")
                    self.assertEqual(any(line.startswith("TREED_BED_MESH_CALIBRATE_EDDY") for line in h.commands), mesh != "0")

    def test_print_area_uses_travel_limits_and_clears_old_xy_offset(self):
        for origin, size in [(0, 245), (5, 220)]:
            with self.subTest(origin=origin, size=size):
                h = Harness()
                geo = h.printer["gcode_macro _TREED_GEOMETRY_CFG"]
                geo.update(print_min_x=0, print_min_y=0, print_size_x=220, print_size_y=215,
                           print_offset_x=5, print_offset_y=30, bed_origin_y=30, bed_size_y=215)
                h.printer["toolhead"]["axis_minimum"].update(x=origin, y=origin)
                h.printer["toolhead"]["axis_maximum"].update(x=origin + size, y=origin + size)
                h.printer["gcode_move"]["homing_origin"].update(x=5, y=30, z=0.03)
                h.run("_TREED_PRINT_OFFSET_ENABLE")
                self.assertEqual(h.printer["gcode_move"]["homing_origin"], {"x": 0, "y": 0, "z": 0.03})
                self.assertEqual((geo["print_min_x"], geo["print_min_y"],
                                  geo["print_size_x"], geo["print_size_y"]), (origin, origin, size, size))
                self.assertEqual((geo["print_offset_x"], geo["print_offset_y"]), (0, 0))
                self.assertEqual((geo["bed_origin_x"], geo["bed_origin_y"],
                                  geo["bed_size_x"], geo["bed_size_y"]), (origin, origin, size, size))
                h.run("_TREED_SMART_PARK")
                h.run("_TREED_LINE_PURGE")
                extrusion = [move for move in h.moves if "E" in move[0]]
                self.assertEqual(extrusion[0][1]["y"] + h.printer["gcode_move"]["homing_origin"]["y"], origin + 5)

    def test_scan_inset_and_probe_offset_do_not_shift_print_geometry(self):
        h = Harness()
        before = deepcopy(h.printer["gcode_macro _TREED_GEOMETRY_CFG"])
        h.printer["gcode_macro _TREED_GEOMETRY_CFG"].update(
            print_min_y=65, print_size_y=180, bed_origin_y=65, bed_size_y=180)
        for name in ["_TREED_EDDY_MESH_CFG", "_TREED_EDDY_Z0_CFG"]:
            h.printer["gcode_macro " + name] = {
                key.removeprefix("variable_"): ast.literal_eval(value)
                for key, value in CFG["gcode_macro " + name].items() if key.startswith("variable_")}
        h.printer["configfile"]["settings"].update({
            "probe_eddy_current btt_eddy": {"x_offset": 0, "y_offset": -30}, "bed_mesh": {"speed": 120}})
        h.printer["treed_z_recovery"] = {"mesh_active": False}
        for name in ["TREED_BED_MESH_CALIBRATE_EDDY", "_TREED_EDDY_HOME_Z"]:
            script = ENV.from_string(CFG["gcode_macro " + name]["gcode"]).render(
                printer=h.printer, params={}, action_raise_error=fail)
            if name == "TREED_BED_MESH_CALIBRATE_EDDY":
                self.assertIn("MESH_MIN=25.0,25.0 MESH_MAX=225.0,222.0", script)
                self.assertIn("Print area: X0.0..250.0 Y0.0..257.0", script)
                self.assertNotIn("MACRO=_TREED_GEOMETRY_CFG", script)
            else:
                self.assertIn("G1 X125.0 Y158.5 F12000", script)
        h.run("_TREED_PRINT_OFFSET_ENABLE")
        self.assertEqual(h.printer["gcode_macro _TREED_GEOMETRY_CFG"], before)

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
        self.assertEqual(extrusion, [({"X": 135, "E": 30, "F": 400},
                                     {"x": 135, "y": 5, "z": 0.4}, 0.06)])
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

    def test_cancel_stages_live_babystep_once_after_cleanup_without_restart(self):
        for offset in [-0.05, 0.01, 0.05]:
            with self.subTest(offset=offset):
                h = Harness()
                h.printer["gcode_move"]["homing_origin"]["z"] = offset
                h.run("CANCEL_PRINT")
                self.assertEqual(h.staged_offsets, [offset])
                self.assertEqual(h.saved_offsets, [])
                self.assertEqual(h.printer["gcode_move"]["homing_origin"]["z"], 0)
                self.assertEqual(h.printer["gcode_macro _TREED_UI_TUNE_STATE"]["applied_babystep"], 0)
                self.assertEqual(h.printer["gcode_macro _TREED_EDDY_Z_OFFSET_AUTOSAVE_STATE"]["has_pending"], 0)
                self.assertLess(h.commands.index("TURN_OFF_HEATERS"), h.commands.index("CANCEL_PRINT_BASE"))
                capture = f"SET_GCODE_VARIABLE MACRO=_TREED_EDDY_Z_OFFSET_AUTOSAVE_STATE VARIABLE=pending_z VALUE={offset}"
                self.assertLess(h.commands.index("CANCEL_PRINT_BASE"), h.commands.index(capture))
                self.assertLess(h.commands.index(capture), h.commands.index("SET_GCODE_OFFSET Z=0 MOVE=0"))
                self.assertLess(h.commands.index("M400"), h.commands.index(f"_TREED_EDDY_APPLY_LIVE_Z_OFFSET Z={offset}"))
                self.assertNotIn(f"SET_GCODE_OFFSET Z={offset} MOVE=0", h.commands)
                self.assertEqual(h.live_probe_offset, offset)
                self.assertFalse(any(line.split()[0] in ["SAVE_CONFIG", "RESTART", "FIRMWARE_RESTART"]
                                     for line in h.commands))
                h.run("CANCEL_PRINT")
                self.assertEqual(h.staged_offsets, [offset])
                self.assertEqual(h.saved_offsets, [])
                self.assertEqual(h.pending_probe_offset, offset)
                self.assertEqual(h.live_probe_offset, offset)

    def test_cancel_leaves_babystep_for_explicit_save_config(self):
        h = Harness()
        h.printer["gcode_move"]["homing_origin"]["z"] = 0.15
        h.run("CANCEL_PRINT")
        self.assertEqual(h.saved_offsets, [])
        self.assertEqual(h.pending_probe_offset, 0.15)
        h.printer["print_stats"]["state"] = "cancelled"
        h.run("TREED_SAVE_CONFIG")
        self.assertEqual(h.saved_offsets, [0.15])

    def test_ui_babystep_rejects_non_numbers_before_commands(self):
        for delta, current in [('nan', 0.), ('inf', 0.), ('-inf', 0.), ('invalid', 0.),
                               ('0.01', math.nan), ('0.05', 0.99)]:
            with self.subTest(delta=delta, current=current):
                h = Harness()
                h.printer['print_stats']['state'] = 'printing'
                h.printer['gcode_move']['homing_origin']['z'] = current
                h.printer['gcode_macro _TREED_UI_TUNE_STATE'].update(babystep_total_min=-1., babystep_total_max=1.)
                with self.assertRaises(RuntimeError):
                    h.run('TREED_UI_ADJUST_Z_OFFSET', params={'DELTA': delta})
                self.assertEqual(h.commands, [])

    def test_ui_babystep_accepts_valid_range_boundaries(self):
        for delta, current, expected in [('0.05', 0.95, 1.), ('-0.05', -0.95, -1.)]:
            h = Harness()
            h.printer['print_stats']['state'] = 'printing'
            h.printer['gcode_move']['homing_origin']['z'] = current
            h.printer['gcode_macro _TREED_UI_TUNE_STATE'].update(babystep_total_min=-1., babystep_total_max=1.)
            h.run('TREED_UI_ADJUST_Z_OFFSET', params={'DELTA': delta})
            self.assertIn(f'SET_GCODE_OFFSET Z_ADJUST={delta} MOVE=1 MOVE_SPEED=5', h.commands)
            self.assertEqual(h.printer['gcode_macro _TREED_UI_TUNE_STATE']['applied_babystep'], expected)

    def test_start_rejects_unapplied_offset_before_resetting_state(self):
        h = Harness()
        h.printer['gcode_macro _TREED_EDDY_Z_OFFSET_AUTOSAVE_STATE'].update(has_pending=1, pending_z=0.15)
        with self.assertRaisesRegex(RuntimeError, 'неприменённая Z-поправка'):
            h.run('_TREED_START_PREP_STATE', params={'BED_TEMP': '60', 'EXTRUDER_TEMP': '210', 'MESH': '0'})
        self.assertEqual(h.commands, [])
        h.run('CANCEL_PRINT')
        self.assertEqual(h.live_probe_offset, 0.15)
        h.run('_TREED_START_PREP_STATE', params={'BED_TEMP': '60', 'EXTRUDER_TEMP': '210', 'MESH': '0'})
        self.assertIn('BED_MESH_CLEAR', h.commands)

    def test_captured_offset_at_threshold_is_cleared_without_applying(self):
        for offset in (-0.005, 0.005):
            h = Harness()
            state = h.printer['gcode_macro _TREED_EDDY_Z_OFFSET_AUTOSAVE_STATE']
            state.update(has_pending=1, pending_z=offset)
            h.run('_TREED_EDDY_APPLY_CAPTURED_Z_OFFSET', params={'SAVE': '0'})
            self.assertEqual(h.staged_offsets, [])
            self.assertEqual(state['has_pending'], 0)
            self.assertEqual(state['pending_z'], 0.)

    def test_next_print_keeps_cancelled_correction_and_adds_only_new_babystep(self):
        h = Harness()
        h.printer["gcode_move"]["homing_origin"]["z"] = 0.15
        h.run("CANCEL_PRINT")
        h.run("_TREED_UI_RESET_Z_OFFSET")
        self.assertEqual(h.live_probe_offset, 0.15)
        self.assertEqual(h.printer["gcode_move"]["homing_origin"]["z"], 0)
        h.printer["gcode_move"]["homing_origin"]["z"] = -0.05
        h.run("CANCEL_PRINT")
        self.assertAlmostEqual(h.live_probe_offset, 0.10)
        self.assertEqual(h.staged_offsets, [0.15, -0.05])
        self.assertEqual(h.saved_offsets, [])
        h.printer["gcode_move"]["homing_origin"]["z"] = 0.02
        h.run("END_PRINT")
        self.assertEqual(h.staged_offsets, [0.15, -0.05, 0.02])
        self.assertEqual(len(h.saved_offsets), 1)
        self.assertAlmostEqual(h.saved_offsets[0], 0.12)

    def test_autosave_rejects_invalid_save_flag_before_staging(self):
        for save in ["2", "invalid"]:
            with self.subTest(save=save):
                h = Harness()
                h.printer["gcode_macro _TREED_EDDY_Z_OFFSET_AUTOSAVE_STATE"].update(has_pending=1, pending_z=0.05)
                with self.assertRaisesRegex(RuntimeError, "SAVE должен быть 0 или 1"):
                    h.run("_TREED_EDDY_APPLY_CAPTURED_Z_OFFSET", params={"SAVE": save})
                self.assertEqual(h.commands, [])

    def test_cancel_captures_babystep_before_purge_state_restore(self):
        h = Harness()
        h.run("_TREED_LINE_PURGE", stop_after="M221 S100")
        h.printer["gcode_move"]["homing_origin"] = {"z": 0.05}
        h.run("CANCEL_PRINT")
        self.assertEqual(h.staged_offsets, [0.05])
        self.assertEqual(h.saved_offsets, [])
        self.assertEqual(h.printer["gcode_macro _TREED_START_PURGE_CFG"]["state_saved"], 0)

    def test_cancel_preserves_offset_already_captured_by_end_print(self):
        h = Harness()
        h.printer["toolhead"]["homed_axes"] = "xy"
        h.printer["gcode_macro _TREED_EDDY_Z_OFFSET_AUTOSAVE_STATE"].update(has_pending=1, pending_z=0.03)
        h.run("CANCEL_PRINT")
        self.assertEqual(h.staged_offsets, [0.03])
        self.assertEqual(h.saved_offsets, [])
        self.assertFalse(h.moves)

    def test_cancel_skips_save_without_trustworthy_or_significant_offset(self):
        for homed, offset in [("xy", 0.05), ("xyz", 0), ("xyz", 0.005), ("xyz", -0.005)]:
            with self.subTest(homed=homed, offset=offset):
                h = Harness()
                h.printer["toolhead"]["homed_axes"] = homed
                h.printer["gcode_move"]["homing_origin"]["z"] = offset
                h.run("CANCEL_PRINT")
                self.assertEqual(h.staged_offsets, [])
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
                           ("x_inset", -1), ("x_inset", 0), ("x_inset", 125),
                           ("y_inset", -1), ("y_inset", 0), ("y_inset", 257)]:
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
