"""Read-only: исполняемые контракты admission gate на настоящих Jinja-макросах."""
import configparser
from pathlib import Path
import unittest

from jinja2 import Environment, StrictUndefined


ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "klipper/profiles/treed_v2_corexy_v1"
CFG = configparser.ConfigParser(interpolation=None, strict=False)
for filename in ("macros_core.cfg", "macros_print_flow.cfg", "macros_pause_resume.cfg"):
    CFG.read(PROFILE / filename, encoding="utf-8")
ENV = Environment(variable_start_string="{", variable_end_string="}", undefined=StrictUndefined)


def fail(message):
    raise RuntimeError(message)


def render(section, printer, **params):
    return ENV.from_string(CFG[section]["gcode"]).render(
        printer=printer, params=params, action_raise_error=fail)


class UpdateAdmissionTests(unittest.TestCase):
    def idle_printer(self):
        return {
            "gcode_macro _TREED_UPDATE_ADMISSION_STATE": {"active": 0},
            "gcode_macro _TREED_OPERATION_STATE": {"phase": "idle"},
            "gcode_macro _TREED_EDDY_TEMPERATURE_STATE": {"active": 0},
            "gcode_macro _TREED_CLOG_RECOVERY_STATE": {"active": 0},
            "print_stats": {"state": "standby"},
            "pause_resume": {"is_paused": 0},
            "virtual_sdcard": {"is_active": False},
            "manual_probe": {"is_active": False},
            "treed_z_recovery": {"running": False},
        }

    def test_begin_checks_idle_and_emits_gate_change_as_one_gcode(self):
        rendered = render("gcode_macro _TREED_UPDATE_GATE", self.idle_printer(), ACTION="BEGIN")
        self.assertEqual(rendered.strip(),
                         "SET_GCODE_VARIABLE MACRO=_TREED_UPDATE_ADMISSION_STATE VARIABLE=active VALUE=1")

    def test_begin_rejects_each_active_or_unknown_condition(self):
        mutations = [
            lambda p: p["print_stats"].update(state="printing"),
            lambda p: p["print_stats"].update(state="unknown"),
            lambda p: p["pause_resume"].update(is_paused=1),
            lambda p: p["virtual_sdcard"].update(is_active=True),
            lambda p: p["manual_probe"].update(is_active=True),
            lambda p: p["treed_z_recovery"].update(running=True),
            lambda p: p["gcode_macro _TREED_EDDY_TEMPERATURE_STATE"].update(active=1),
            lambda p: p["gcode_macro _TREED_CLOG_RECOVERY_STATE"].update(active=1),
            lambda p: p["gcode_macro _TREED_OPERATION_STATE"].update(phase="calibrating"),
        ]
        for mutate in mutations:
            printer = self.idle_printer()
            mutate(printer)
            with self.subTest(printer=printer), self.assertRaisesRegex(RuntimeError, "не готов"):
                render("gcode_macro _TREED_UPDATE_GATE", printer, ACTION="BEGIN")

    def test_begin_revalidates_idle_state_when_reacquiring_an_active_gate(self):
        printer = self.idle_printer()
        printer["gcode_macro _TREED_UPDATE_ADMISSION_STATE"]["active"] = 1
        rendered = render("gcode_macro _TREED_UPDATE_GATE", printer, ACTION="BEGIN")
        self.assertEqual(rendered.strip(),
                         "SET_GCODE_VARIABLE MACRO=_TREED_UPDATE_ADMISSION_STATE VARIABLE=active VALUE=1")
        printer["print_stats"]["state"] = "printing"
        with self.assertRaisesRegex(RuntimeError, "не готов"):
            render("gcode_macro _TREED_UPDATE_GATE", printer, ACTION="BEGIN")

    def test_begin_fails_closed_when_a_required_signal_is_missing(self):
        for required in ("print_stats", "pause_resume", "virtual_sdcard", "manual_probe",
                         "treed_z_recovery", "gcode_macro _TREED_EDDY_TEMPERATURE_STATE"):
            printer = self.idle_printer()
            del printer[required]
            with self.subTest(required=required), self.assertRaises(Exception):
                render("gcode_macro _TREED_UPDATE_GATE", printer, ACTION="BEGIN")

    def test_end_is_idempotent_and_clears_only_the_gate(self):
        printer = self.idle_printer()
        self.assertEqual(render("gcode_macro _TREED_UPDATE_GATE", printer, ACTION="END").strip(), "")
        printer["gcode_macro _TREED_UPDATE_ADMISSION_STATE"]["active"] = 1
        self.assertEqual(render("gcode_macro _TREED_UPDATE_GATE", printer, ACTION="END").strip(),
                         "SET_GCODE_VARIABLE MACRO=_TREED_UPDATE_ADMISSION_STATE VARIABLE=active VALUE=0")

    def test_start_and_resume_guards_reject_during_update(self):
        printer = self.idle_printer()
        printer["gcode_macro _TREED_UPDATE_ADMISSION_STATE"]["active"] = 1
        for op in ("start", "resume"):
            with self.subTest(op=op), self.assertRaisesRegex(RuntimeError, "выполняется обновление"):
                render("gcode_macro _TREED_OPERATION_REQUIRE", printer, OP=op)

    def test_native_virtual_sd_entrypoints_are_blocked_only_while_gate_is_active(self):
        for section, base in (("gcode_macro SDCARD_PRINT_FILE", "SDCARD_PRINT_FILE_BASE"),
                              ("gcode_macro M24", "M24.1")):
            with self.subTest(section=section):
                printer = self.idle_printer()
                template = ENV.from_string(CFG[section]["gcode"])
                self.assertIn(base, template.render(printer=printer, params={}, rawparams="FILENAME=test.gcode",
                                                    action_raise_error=fail))
                printer["gcode_macro _TREED_UPDATE_ADMISSION_STATE"]["active"] = 1
                with self.assertRaisesRegex(RuntimeError, "выполняется обновление"):
                    template.render(printer=printer, params={}, rawparams="FILENAME=test.gcode",
                                    action_raise_error=fail)


if __name__ == "__main__":
    unittest.main()
