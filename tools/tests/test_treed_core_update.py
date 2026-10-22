"""Назначение: адресные проверки пакета, сохранности данных и journal recovery.
Контур: временные файлы и mock services/network; без принтера и systemd.
"""

# Блок 1: Реальный installer в изолированном runtime.
import contextlib
import configparser
import io
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import ANY, call, patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "runtime-scripts/treed-update/treed-core-update"
core = types.ModuleType("treed_core_update")
exec(compile(PATH.read_text(encoding="utf-8"), str(PATH), "exec"), core.__dict__)
core_update_admission = core.update_admission
core_services_inactive = core.services_inactive
OPERATION = "11111111-1111-4111-8111-111111111111"


class CoreUpdateTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.env = {"TREED_UPDATE_PI_HOME": str(self.root / "home"),
                    "TREED_UPDATE_PI_USER": "radxa", "TREED_UPDATE_PI_GROUP": "radxa",
                    "TREED_CORE_MANIFEST_FILE": str(self.root / "state/core-manifest.json"),
                    "TREED_CORE_STATE_DIR": str(self.root / "state/core")}
        self.locations = {name: self.root / name for name in ("config", "klipper", "moonraker", "camera", "vision", "sbin")}
        self.old = {
            "config/printer.cfg": b"[include profiles/treed_v2_corexy_v1/macros.cfg]\n\n" + core.MARKER + b"\n#*# [input_shaper]\n#*# shaper_freq_x = 43\n",
            "config/profiles/treed_v2_corexy_v1/macros.cfg": b"[gcode_macro OLD]\ngcode: G4 P0\n",
            "klipper/treed_driver_mode.py": b"version = 1\n",
            "moonraker/treed_update.py": b"version = 1\n",
        }
        self.payload = dict(self.old)
        self.payload.update({"config/printer.cfg": b"# new release\n[include profiles/treed_v2_corexy_v1/macros.cfg]\n",
                             "klipper/treed_driver_mode.py": b"version = 2\n",
                             "moonraker/treed_update.py": b"version = 2\n"})
        for name in self.locations.values():
            name.mkdir()
        for key, data in self.old.items():
            path = core.destination(key, self.locations)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.previous = {"kind": core.KIND, "schema": 1, "version": "0.1.0", "files": [
            {"path": key, "runtimeSha256": core.content_hash(key, value)} for key, value in self.old.items()]}
        core.write_json(core.manifest_path(self.env), self.previous)
        self.manifest = dict(self.previous, version="0.2.0", tag="v0.2.0")
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(core, "roots", return_value=self.locations))
        self.stack.enter_context(patch.object(core, "download"))
        self.stack.enter_context(patch.object(core, "load_package", side_effect=lambda *_: (self.manifest, self.payload)))
        self.vision_units = self.stack.enter_context(patch.object(core, "vision_units_installed", return_value=True))
        self.compatibility = self.stack.enter_context(patch.object(core, "check_compatibility"))
        self.phase = self.stack.enter_context(patch.object(core, "phase"))
        self.services = self.stack.enter_context(patch.object(core, "services"))
        self.services_inactive = self.stack.enter_context(patch.object(core, "services_inactive", return_value=True))
        self.admission = self.stack.enter_context(patch.object(core, "update_admission"))
        self.stack.enter_context(patch.object(core, "idle", return_value=True))
        self.stop_heating = self.stack.enter_context(patch.object(core, "stop_heating"))
        self.wait = self.stack.enter_context(patch.object(core, "wait_ready", return_value=True))
        self.stack.enter_context(patch.object(core, "owner", return_value=(
            getattr(os, "getuid", lambda: 0)(), getattr(os, "getgid", lambda: 0)())))

    def apply(self):
        return core.apply("v0.2.0", OPERATION, self.env)

    def test_managed_loader_receives_fixed_preservation_environment(self):
        self.manifest.update(requires={"klipper": "a" * 40, "moonraker": "b" * 40},
                             provisioning={"schema": 1, "revision": "c" * 64})
        self.stack.enter_context(patch.object(core.subprocess, "check_output", return_value=""))
        self.stack.enter_context(patch.object(core, "extract_source"))
        upstream = self.stack.enter_context(patch.object(core, "check_upstream_clean", wraps=core.check_upstream_clean))
        for name, suffix in (("klipper", Path("klippy/extras")),
                             ("moonraker", Path("moonraker/components"))):
            checkout = self.root / f"custom-{name}"
            location = checkout / suffix
            location.mkdir(parents=True)
            self.locations[name] = location
            self.env[f"TREED_CORE_{name.upper()}_DIR"] = str(checkout)
            key = f"{name}/treed_{'driver_mode' if name == 'klipper' else 'update'}.py"
            (location / Path(key).name).write_bytes(self.old[key])
        loader = self.stack.enter_context(patch.object(core, "run_managed_loader", return_value=0))
        self.assertEqual("applied", self.apply()["status"])
        args, kwargs = loader.call_args
        self.assertEqual(args[0], self.root / "state/core" / OPERATION / "source")
        self.assertEqual(args[1]["TREED_UPDATE_MANAGED"], "1")
        self.assertEqual(args[1]["TREED_UPDATE_MANAGED_LOADER"], "1")
        self.assertEqual(args[1]["TREED_UPDATE_OPERATION_ID"], OPERATION)
        self.assertEqual(args[1]["TREED_DEPLOY_MODE"], "preserve")
        self.assertEqual(args[1]["TREED_SHELL_INSTALL"], "0")
        self.assertEqual(args[1]["TREED_KLIPPER_SRC_DIR"], str(self.env_path("klipper")))
        self.assertEqual(args[1]["TREED_MOONRAKER_SRC_DIR"], str(self.env_path("moonraker")))
        journal = core.read_json(core.journal_path(self.env, OPERATION))
        self.assertEqual(journal["stage"], "committed")
        self.assertTrue(journal["provisioningStarted"])
        upstream.assert_called_once_with(self.locations, self.previous)
        self.assertEqual(self.compatibility.call_args.args[0], self.manifest)
        self.assertTrue(all("runtimeSha256" in row for row in self.compatibility.call_args.args[2]["files"]))

    def env_path(self, name):
        return Path(self.env[f"TREED_CORE_{name.upper()}_DIR"])

    def test_managed_loader_failure_keeps_durable_provisioning_failure_without_rollback(self):
        self.manifest.update(requires={"klipper": "a" * 40, "moonraker": "b" * 40},
                             provisioning={"schema": 1, "revision": "c" * 64})
        self.stack.enter_context(patch.object(core.subprocess, "check_output", return_value=""))
        self.stack.enter_context(patch.object(core, "extract_source"))
        self.stack.enter_context(patch.object(core, "run_managed_loader", return_value=1))
        result = self.apply()
        self.assertEqual("provisioning_failed", result["resultCode"])
        self.assertEqual("provisioning_failed", core.read_json(core.journal_path(self.env, OPERATION))["stage"])
        self.assertTrue(core.read_json(core.journal_path(self.env, OPERATION))["provisioningStarted"])
        self.assertEqual(self.payload["klipper/treed_driver_mode.py"],
                         core.destination("klipper/treed_driver_mode.py", self.locations).read_bytes())
        self.services.assert_called_once_with("stop")

    def test_committed_journal_write_error_does_not_runtime_rollback_provisioning(self):
        self.manifest.update(requires={"klipper": "a" * 40, "moonraker": "b" * 40},
                             provisioning={"schema": 1, "revision": "c" * 64})
        self.stack.enter_context(patch.object(core.subprocess, "check_output", return_value=""))
        self.stack.enter_context(patch.object(core, "extract_source"))
        self.stack.enter_context(patch.object(core, "run_managed_loader", return_value=0))
        original_write = core.write_json
        journal = core.journal_path(self.env, OPERATION)

        def fail_after_committed_write(path, value):
            original_write(path, value)
            if Path(path) == journal and value.get("stage") == "committed":
                raise OSError("simulated fsync failure after durable rename")

        self.stack.enter_context(patch.object(core, "write_json", side_effect=fail_after_committed_write))
        result = self.apply()
        self.assertEqual("provisioning_failed", result["resultCode"])
        self.assertTrue(core.read_json(journal)["provisioningStarted"])
        self.assertEqual(self.payload["klipper/treed_driver_mode.py"],
                         core.destination("klipper/treed_driver_mode.py", self.locations).read_bytes())
        self.assertEqual("0.2.0", core.read_json(core.manifest_path(self.env))["version"])
        self.services.assert_called_once_with("stop")

    def test_dirty_upstream_rejects_before_source_download_or_runtime_mutation(self):
        self.manifest.update(requires={"klipper": "a" * 40, "moonraker": "b" * 40},
                             provisioning={"schema": 1, "revision": "c" * 64})
        self.stack.enter_context(patch.object(core.subprocess, "check_output", return_value=" M klippy.py"))
        with self.assertRaisesRegex(ValueError, "Изменены исходники"):
            self.apply()
        self.services.assert_not_called()

    def assert_restored(self):
        for key, data in self.old.items():
            self.assertEqual(core.destination(key, self.locations).read_bytes(), data, key)
        self.assertEqual(core.read_json(core.manifest_path(self.env))["version"], "0.1.0")

    def seed_autoprinter_update(self):
        private = {
            "printer_data/system/treed-autoprinter-credentials.json": b'private-device-secret',
            "printer_data/system/treed-autoprinter/store.sqlite3": b'private-journal',
            "printer_data/system/treed-autoprinter/bindings/1/model.gcode": b'private-account-model',
            "treed/models/e0.pth": b'private-weights',
        }
        paths = {}
        for relative, data in private.items():
            path = Path(self.env["TREED_UPDATE_PI_HOME"]) / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            paths[path] = data
        for key in core.AUTOPRINTER_MODULES:
            self.payload[key] = b"version = 2\n"
        self.manifest["files"] = [{"path": key, "sha256": core.sha(data)}
                                  for key, data in self.payload.items()]
        return paths

    def test_autoprinter_update_and_recovery_preserve_private_data(self):
        private = self.seed_autoprinter_update()
        self.assertEqual("applied", self.apply()["status"])
        self.assertEqual("applied", core.recover(self.env, OPERATION)["status"])
        for path, data in private.items():
            self.assertEqual(data, path.read_bytes())
        for key in core.AUTOPRINTER_MODULES:
            self.assertEqual(self.payload[key], core.destination(key, self.locations).read_bytes())
        self.assertTrue(all(any(row["path"] == "moonraker/treed_autoprinter.py"
                                for row in call.args[0]["files"])
                            for call in self.wait.call_args_list))

    def test_failed_autoprinter_readiness_rolls_back_and_preserves_private_data(self):
        private = self.seed_autoprinter_update()
        self.wait.side_effect = [False, True]
        self.assertEqual("rolled_back", self.apply()["status"])
        self.assert_restored()
        for path, data in private.items():
            self.assertEqual(data, path.read_bytes())
        self.assertFalse(any(core.destination(key, self.locations).exists()
                             for key in core.AUTOPRINTER_MODULES))
        self.assertEqual(self.previous, self.wait.call_args_list[-1].args[0])
        self.assertEqual(2, self.wait.call_count)
        self.assertTrue(any(row["path"] == "moonraker/treed_vision_client.py"
                            for row in self.wait.call_args_list[0].args[0]["files"]))
        self.assertEqual(self.previous, self.wait.call_args_list[1].args[0])

    def test_provisioning_interruption_never_runtime_rolls_back(self):
        journal = core.journal_path(self.env, OPERATION)
        journal.parent.mkdir(parents=True)
        core.write_json(journal, {"schema": 1, "stage": "provisioning_failed", "entries": []})
        result = core.recover(self.env, OPERATION)
        self.assertEqual(result["resultCode"], "provisioning_interrupted")
        self.assertEqual(core.read_json(journal)["stage"], "provisioning_failed")
        self.assertEqual((self.locations["config"] / "printer.cfg").read_bytes(),
                         self.old["config/printer.cfg"])

    def test_committed_managed_provisioning_recovers_only_when_ready(self):
        journal = core.journal_path(self.env, OPERATION)
        journal.parent.mkdir(parents=True)
        expected = dict(self.manifest, files=[])
        core.write_json(core.manifest_path(self.env), expected)
        core.write_json(journal.parent / "manifest.new.json", expected)
        core.write_json(journal, {"schema": 1, "stage": "committed", "provisioningStarted": True,
                                  "entries": []})
        with patch.object(core, "wait_ready", return_value=False), \
             patch.object(core, "rollback") as rollback:
            failed = core.recover(self.env, OPERATION)
        self.assertEqual(failed["resultCode"], "provisioning_interrupted")
        rollback.assert_not_called()
        with patch.object(core, "wait_ready", return_value=True), \
             patch.object(core, "rollback") as rollback:
            ready = core.recover(self.env, OPERATION)
        self.assertEqual(ready["status"], "applied")
        rollback.assert_not_called()

    def test_committed_legacy_recovery_keeps_runtime_rollback(self):
        journal = core.journal_path(self.env, OPERATION)
        journal.parent.mkdir(parents=True)
        core.write_json(core.manifest_path(self.env), self.previous)
        core.write_json(journal.parent / "manifest.new.json", self.manifest)
        core.write_json(journal, {"schema": 1, "stage": "committed", "entries": []})
        with patch.object(core, "rollback", return_value={"status": "rolled_back"}) as rollback:
            result = core.recover(self.env, OPERATION)
        self.assertEqual(result["status"], "rolled_back")
        rollback.assert_called_once()


    def seed_pid_calibration(self, extruder_already_saved=False):
        extruder = b"[extruder]\ncontrol: pid\npid_Kp: 11.818\npid_Ki: 0.435\npid_Kd: 80.218\n"
        bed = b"[heater_bed]\ncontrol: pid\npid_Kp: 62.937\npid_Ki: 1.165\npid_Kd: 849.647\n"
        def commented(block):
            return block.replace(b"control:", b"#control:").replace(b"pid_", b"#pid_")
        saved_extruder = b"#*# [extruder]\n#*# control = pid\n#*# pid_kp = 12.0\n#*# pid_ki = 0.5\n#*# pid_kd = 81.0\n"
        saved_bed = b"#*# [heater_bed]\n#*# control = pid\n#*# pid_kp = 63.0\n#*# pid_ki = 1.2\n#*# pid_kd = 850.0\n"
        include = b"[include profiles/treed_v2_corexy_v1/macros.cfg]\n\n"
        before = include + (commented(extruder) if extruder_already_saved else extruder) + bed + b"\n" + core.MARKER + b"\n"
        if extruder_already_saved:
            before += saved_extruder
        calibrated = include + commented(extruder) + commented(bed) + b"\n" + core.MARKER + b"\n" + saved_extruder + saved_bed
        self.old["config/printer.cfg"] = calibrated
        (self.locations["config"] / "printer.cfg").write_bytes(calibrated)
        self.payload["config/printer.cfg"] = include + extruder.replace(b"11.818", b"20.0") + bed.replace(b"62.937", b"70.0")
        for row in self.previous["files"]:
            if row["path"] == "config/printer.cfg":
                row["runtimeSha256"] = core.content_hash(row["path"], before)
        core.write_json(core.manifest_path(self.env), self.previous)
        return before, calibrated

    def test_pid_save_config_is_not_treated_as_manual_edits_and_calibration_wins(self):
        _before, calibrated = self.seed_pid_calibration()
        self.assertEqual(self.apply()["status"], "applied")
        installed = (self.locations["config"] / "printer.cfg").read_bytes()
        body, saved = installed.split(core.MARKER, 1)
        self.assertEqual(saved, calibrated.split(core.MARKER, 1)[1])
        settings = configparser.RawConfigParser()
        settings.read_string(body.decode())
        self.assertFalse(settings.has_option("extruder", "control"))
        self.assertFalse(settings.has_option("heater_bed", "pid_kp"))
        settings.read_string("\n".join(line[4:] for line in saved.decode().splitlines() if line.startswith("#*# ")))
        self.assertEqual(settings.getfloat("extruder", "pid_kp"), 12.0)
        self.assertEqual(settings.getfloat("heater_bed", "pid_kp"), 63.0)
        self.assertIn(b"#pid_Kp: 20.0", body)
        self.assertIn(b"#pid_Kp: 70.0", body)

    def test_new_bed_calibration_is_compatible_with_manifest_after_extruder_calibration(self):
        self.seed_pid_calibration(extruder_already_saved=True)
        self.assertEqual(self.apply()["status"], "applied")

    def test_pid_save_config_just_before_service_stop_is_preserved(self):
        before, calibrated = self.seed_pid_calibration()
        path = self.locations["config"] / "printer.cfg"
        path.write_bytes(before)
        def stop(action):
            if action == "stop":
                path.write_bytes(calibrated)
        self.services.side_effect = stop
        self.assertEqual(self.apply()["status"], "applied")
        self.assertEqual(path.read_bytes().split(core.MARKER, 1)[1], calibrated.split(core.MARKER, 1)[1])

    def test_manual_printer_edits_remain_rejected_after_pid_save_config(self):
        self.seed_pid_calibration()
        path = self.locations["config"] / "printer.cfg"
        path.write_bytes(path.read_bytes().replace(b"macros.cfg", b"changed.cfg"))
        with self.assertRaisesRegex(ValueError, "Локально изменён"):
            self.apply()
        self.services.assert_not_called()

    def test_commented_pid_values_cannot_hide_manual_changes(self):
        self.seed_pid_calibration()
        path = self.locations["config"] / "printer.cfg"
        path.write_bytes(path.read_bytes().replace(b"#pid_Kp: 11.818", b"#pid_Kp: 99.0"))
        with self.assertRaisesRegex(ValueError, "Локально изменён"):
            self.apply()
        self.services.assert_not_called()

    def test_pid_comments_without_matching_autosave_fields_remain_rejected(self):
        _before, calibrated = self.seed_pid_calibration()
        path = self.locations["config"] / "printer.cfg"
        path.write_bytes(calibrated.split(core.MARKER, 1)[0] + core.MARKER + b"\n#*# [input_shaper]\n#*# shaper_freq_x = 71\n")
        with self.assertRaisesRegex(ValueError, "Локально изменён"):
            self.apply()
        self.services.assert_not_called()

    def test_baseline_accepts_stock_pid_comments_and_saved_calibration(self):
        before, _calibrated = self.seed_pid_calibration()
        source = self.root / "source-printer.cfg"
        source.write_bytes(before.split(core.MARKER, 1)[0])
        with patch.object(core, "package_manifest", return_value=dict(self.manifest, files=[])), \
             patch.object(core, "sources", return_value={"config/printer.cfg": source}):
            core.baseline(self.root, self.env)
        self.assertEqual(core.read_json(core.manifest_path(self.env))["version"], "0.2.0")

    def test_heater_shutdown_failure_does_not_stop_services_or_replace_files(self):
        self.stop_heating.side_effect = ValueError("heater still active")
        with self.assertRaisesRegex(ValueError, "heater still active"):
            self.apply()
        self.assertEqual(self.admission.call_args_list, [call("BEGIN"), call("END")])
        self.services.assert_not_called()
        self.assert_restored()

    def test_update_gate_precedes_heater_shutdown_and_service_stop(self):
        events = []
        self.admission.side_effect = lambda action: events.append(action)
        self.stop_heating.side_effect = lambda: events.append("heaters")
        self.services.side_effect = lambda action: events.append(action)

        self.assertEqual(self.apply()["status"], "applied")
        self.assertEqual(events[:3], ["BEGIN", "heaters", "stop"])
        self.assertEqual(events[-1], "start")

    def test_admission_sends_the_private_begin_and_end_gcode(self):
        with patch.object(core, "request_json") as request:
            request.side_effect = [None,
                {"status": {"gcode_macro _TREED_UPDATE_ADMISSION_STATE": {"active": 1}}},
                None,
                {"status": {"gcode_macro _TREED_UPDATE_ADMISSION_STATE": {"active": 0}}},
            ]
            core_update_admission("BEGIN")
            core_update_admission("END")
        self.assertEqual(request.call_args_list, [
            call("/printer/gcode/script", {"script": "_TREED_UPDATE_GATE ACTION=BEGIN"}),
            call("/printer/objects/query?gcode_macro%20_TREED_UPDATE_ADMISSION_STATE"),
            call("/printer/gcode/script", {"script": "_TREED_UPDATE_GATE ACTION=END"}),
            call("/printer/objects/query?gcode_macro%20_TREED_UPDATE_ADMISSION_STATE"),
        ])

    def test_unknown_command_ack_without_gate_status_fails_closed(self):
        with patch.object(core, "request_json", side_effect=[
                "ok", {"status": {}}]) as request:
            with self.assertRaisesRegex(RuntimeError, "не подтвердил состояние admission gate"):
                core_update_admission("BEGIN")
        self.assertEqual(request.call_count, 2)

    def test_admission_rejects_a_status_value_that_does_not_match_the_action(self):
        cases = [("BEGIN", 0), ("END", 1), ("BEGIN", "1")]
        for action, active in cases:
            with self.subTest(action=action, active=active), \
                 patch.object(core, "request_json", side_effect=[None, {
                     "status": {"gcode_macro _TREED_UPDATE_ADMISSION_STATE": {"active": active}}
                 }]):
                with self.assertRaisesRegex(RuntimeError, "не подтвердил состояние admission gate"):
                    core_update_admission(action)

    def test_missing_update_gate_fails_before_heaters_or_service_stop(self):
        self.admission.side_effect = RuntimeError("unknown command _TREED_UPDATE_GATE")
        with self.assertRaisesRegex(RuntimeError, "unknown command"):
            self.apply()
        self.stop_heating.assert_not_called()
        self.services.assert_not_called()

    def test_validated_recovery_releases_gate_after_services_are_ready(self):
        events = []
        self.admission.side_effect = lambda action: events.append(action)
        self.phase.side_effect = lambda name, _message: (_ for _ in ()).throw(RuntimeError("phase failed")) \
            if name == "installing" else None
        result = self.apply()
        self.assertEqual(result["resultCode"], "core_not_installed")
        self.assertEqual(events, ["BEGIN", "END"])
        self.services.assert_called_once_with("start")

    def test_validated_recovery_does_not_report_completion_when_gate_release_fails(self):
        self.phase.side_effect = lambda name, _message: (_ for _ in ()).throw(RuntimeError("phase failed")) \
            if name == "installing" else None
        self.admission.side_effect = [None, RuntimeError("Klipper response lost")]
        with self.assertRaisesRegex(RuntimeError, "не смогла снять admission gate"):
            self.apply()
        self.services.assert_called_once_with("start")
        self.assertEqual(core.read_json(core.journal_path(self.env, OPERATION))["stage"], "validated")

    def prepared_install_transaction(self):
        journal = core.journal_path(self.env, OPERATION)
        core.prepare(self.manifest, self.payload, self.env, journal)
        transaction = core.read_json(journal)
        transaction["stage"] = "installing"
        core.write_json(journal, transaction)
        for key, data in self.payload.items():
            core.destination(key, self.locations).write_bytes(data)
        return journal, transaction

    def test_rollback_refuses_to_stop_running_services_without_gate(self):
        journal, transaction = self.prepared_install_transaction()
        self.admission.side_effect = RuntimeError("Klipper unavailable")
        self.services_inactive.return_value = False
        with self.assertRaisesRegex(RuntimeError, "не подтверждены как остановленные"):
            core.rollback(self.env, journal, transaction)
        self.services.assert_not_called()
        self.assertEqual(core.destination("klipper/treed_driver_mode.py", self.locations).read_bytes(),
                         self.payload["klipper/treed_driver_mode.py"])

    def test_rollback_can_recover_offline_when_systemd_confirms_both_services_stopped(self):
        journal, transaction = self.prepared_install_transaction()
        self.admission.side_effect = RuntimeError("Klipper unavailable")
        self.assertEqual(core.rollback(self.env, journal, transaction)["status"], "rolled_back")
        self.services.assert_has_calls([call("stop"), call("start")])
        self.assert_restored()

    def test_rollback_does_not_restore_files_if_service_stop_is_unconfirmed(self):
        journal, transaction = self.prepared_install_transaction()
        self.services_inactive.side_effect = [False]
        with self.assertRaisesRegex(RuntimeError, "не подтвердил остановку"):
            core.rollback(self.env, journal, transaction)
        self.services.assert_called_once_with("stop")
        self.assertEqual(core.destination("klipper/treed_driver_mode.py", self.locations).read_bytes(),
                         self.payload["klipper/treed_driver_mode.py"])

    def test_systemd_unknown_state_is_not_treated_as_offline(self):
        unknown = subprocess.CompletedProcess([], 0, stdout="deactivating\n", stderr="")
        with patch.object(core.subprocess, "run", return_value=unknown):
            self.assertFalse(core_services_inactive())

    # Блок 2: Успех и сохранность принадлежащего принтеру состояния.
    def test_selected_config_reset_uses_package_and_retains_manual_backup(self):
        key = "config/profiles/treed_v2_corexy_v1/macros.cfg"
        path = core.destination(key, self.locations)
        manual = b"# custom fan settings\n"
        path.write_bytes(manual)
        selection = [{"path": key, "sha256": core.sha(manual)}]
        self.assertEqual(core.config_conflicts(self.env), selection)
        with self.assertRaisesRegex(ValueError, "Локально изменён"):
            self.apply()
        self.assertEqual(path.read_bytes(), manual)
        result = core.apply("v0.2.0", OPERATION, self.env, selection)
        self.assertEqual(result["status"], "applied")
        self.assertEqual(path.read_bytes(), self.payload[key])
        journal = core.journal_path(self.env, OPERATION)
        row = next(row for row in core.read_json(journal)["entries"] if row["path"] == key)
        self.assertEqual((journal.parent / row["backup"]).read_bytes(), manual)

    def test_selected_runtime_reset_replaces_all_managed_groups_and_retains_backups(self):
        additions = {"camera/stream_detect.py": b"old camera\n",
                     "sbin/treed-core-update": b"old updater\n",
                     "config/moonraker/base/00-core.conf": b"old moonraker config\n"}
        for key, data in additions.items():
            path = core.destination(key, self.locations)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            self.previous["files"].append({"path": key, "runtimeSha256": core.content_hash(key, data)})
            self.payload[key] = b"# new runtime\n"
        core.write_json(core.manifest_path(self.env), self.previous)
        keys = ["klipper/treed_driver_mode.py", "moonraker/treed_update.py", *additions]
        manual = b"manual runtime change\n"
        for key in keys:
            core.destination(key, self.locations).write_bytes(manual)
        selection = core.config_conflicts(self.env)
        self.assertEqual({row["path"] for row in selection}, set(keys))
        with self.assertRaisesRegex(ValueError, "Локально изменён"):
            self.apply()
        camera = core.destination("camera/stream_detect.py", self.locations)
        camera.write_bytes(manual + b"# newer edit\n")
        with self.assertRaisesRegex(ValueError, "Файл изменился после выбора сброса"):
            core.apply("v0.2.0", OPERATION, self.env, selection)
        self.assertEqual(camera.read_bytes(), manual + b"# newer edit\n")
        camera.write_bytes(manual)
        with patch.object(core.os, "fchown", create=True) as chown:
            result = core.apply("v0.2.0", OPERATION, self.env, selection)
        if os.name != "nt":
            chown.assert_any_call(ANY, 0, 0)
        self.assertEqual(result["status"], "applied")
        journal = core.journal_path(self.env, OPERATION)
        entries = {row["path"]: row for row in core.read_json(journal)["entries"]}
        for key in keys:
            self.assertEqual(core.destination(key, self.locations).read_bytes(), self.payload[key])
            self.assertEqual((journal.parent / entries[key]["backup"]).read_bytes(), manual)

    def test_selected_removed_managed_file_is_deleted_with_backup(self):
        key = "klipper/treed_driver_mode.py"
        manual = b"manual runtime change\n"
        core.destination(key, self.locations).write_bytes(manual)
        del self.payload[key]
        result = core.apply("v0.2.0", OPERATION, self.env, core.config_conflicts(self.env))
        self.assertEqual(result["status"], "applied")
        self.assertFalse(core.destination(key, self.locations).exists())
        journal = core.journal_path(self.env, OPERATION)
        row = next(row for row in core.read_json(journal)["entries"] if row["path"] == key)
        self.assertEqual((journal.parent / row["backup"]).read_bytes(), manual)

    def test_reset_consent_does_not_cover_subsequent_edit_or_unmanaged_files(self):
        for key in ("config/printer.cfg", "klipper/treed_driver_mode.py", "config/local_overrides.cfg", "config/../printer.cfg"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                core.prepare(self.manifest, self.payload, self.env, core.journal_path(self.env, OPERATION),
                             [{"path": key, "sha256": "0" * 64}])
        self.assert_restored()
        self.services.assert_not_called()

    def test_edit_during_service_stop_is_not_overwritten_by_reset_or_rollback(self):
        key = "config/printer.cfg"
        path = core.destination(key, self.locations)
        manual = b"# manual\n" + self.old[key]
        path.write_bytes(manual)
        selection = core.config_conflicts(self.env)
        newer = manual + b"# newer edit\n"
        self.services.side_effect = lambda action: path.write_bytes(newer) if action == "stop" else None
        result = core.apply("v0.2.0", OPERATION, self.env, selection)
        self.assertEqual(result["status"], "error")
        self.assertEqual(path.read_bytes(), newer)
        self.assertEqual(core.recover(self.env, OPERATION)["status"], "error")
        self.assertEqual(path.read_bytes(), newer)

    def test_selected_printer_reset_preserves_calibration_and_rolls_back_manual_file(self):
        key = "config/printer.cfg"
        manual = b"# manual\n" + self.old[key]
        core.destination(key, self.locations).write_bytes(manual)
        self.old[key] = manual
        def start(action):
            if action == "start":
                self.assertIn(core.MARKER + manual.split(core.MARKER, 1)[1], core.destination(key, self.locations).read_bytes())
        self.services.side_effect = start
        self.wait.side_effect = [False, True]
        result = core.apply("v0.2.0", OPERATION, self.env, core.config_conflicts(self.env))
        self.assertEqual(result["status"], "rolled_back")
        self.assert_restored()

    def test_apply_preserves_save_config_overrides_generated_data_and_unknown_files(self):
        preserved = {"local_overrides.cfg": b"custom settings\n", "treed_variables.cfg": b"light = True\n",
                     "filament_motion_runtime.cfg": b"detection_length: 17\n",
                     "moonraker/generated/50-webcam-treed.conf": b"webcam settings\n",
                     "my_custom.cfg": b"user-owned\n"}
        for relative, data in preserved.items():
            path = self.locations["config"] / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.assertEqual(self.apply()["status"], "applied")
        printer = (self.locations["config"] / "printer.cfg").read_bytes()
        self.assertIn(b"# new release", printer)
        self.assertIn(core.MARKER + self.old["config/printer.cfg"].split(core.MARKER, 1)[1], printer)
        for relative, data in preserved.items():
            self.assertEqual((self.locations["config"] / relative).read_bytes(), data)
        installed = core.read_json(core.manifest_path(self.env))
        self.assertEqual(installed["version"], "0.2.0")
        self.assertEqual(core.read_json(core.journal_path(self.env, OPERATION))["stage"], "committed")

    def test_save_config_just_before_service_stop_is_preserved(self):
        path = self.locations["config"] / "printer.cfg"
        def stop(action):
            if action == "stop":
                path.write_bytes(self.old["config/printer.cfg"] + b"#*# shaper_freq_y = 55\n")
        self.services.side_effect = stop
        self.assertEqual(self.apply()["status"], "applied")
        self.assertIn(b"shaper_freq_y = 55", path.read_bytes())

    def test_baseline_cannot_confirm_undelivered_checkout_files(self):
        source = self.root / "source-driver.py"
        source.write_bytes(self.payload["klipper/treed_driver_mode.py"])
        with patch.object(core, "package_manifest", return_value=dict(self.manifest, files=[])), \
             patch.object(core, "sources", return_value={"klipper/treed_driver_mode.py": source}):
            with self.assertRaisesRegex(ValueError, "не соответствует"):
                core.baseline(self.root, self.env)
        self.assertEqual(core.read_json(core.manifest_path(self.env))["version"], "0.1.0")

    def test_baseline_confirms_delivered_source_with_device_save_config(self):
        source = self.root / "source-printer.cfg"
        source.write_bytes(self.old["config/printer.cfg"].split(core.MARKER, 1)[0].rstrip(b"\n"))
        with patch.object(core, "package_manifest", return_value=dict(self.manifest, files=[])), \
             patch.object(core, "sources", return_value={"config/printer.cfg": source}):
            core.baseline(self.root, self.env)
        self.assertEqual(core.read_json(core.manifest_path(self.env))["version"], "0.2.0")

    def test_only_previous_manifest_owned_files_can_be_removed(self):
        self.payload.pop("config/profiles/treed_v2_corexy_v1/macros.cfg")
        unrelated = self.locations["config"] / "profiles/treed_v2_corexy_v1/user.cfg"
        unrelated.write_text("keep", encoding="utf-8")
        self.assertEqual(self.apply()["status"], "applied")
        self.assertFalse(core.destination("config/profiles/treed_v2_corexy_v1/macros.cfg", self.locations).exists())
        self.assertEqual(unrelated.read_text(), "keep")

    # Блок 3: Отказ до мутаций, rollback и повторное recovery после обрыва.
    def test_local_managed_edits_are_rejected_before_stopping_services(self):
        core.destination("klipper/treed_driver_mode.py", self.locations).write_bytes(b"local edits\n")
        with self.assertRaisesRegex(ValueError, "Локально изменён"):
            self.apply()
        self.services.assert_not_called()
        self.assertEqual(core.read_json(core.manifest_path(self.env))["version"], "0.1.0")

    def test_incompatible_stack_is_rejected_before_backup_and_service_stop(self):
        self.compatibility.side_effect = ValueError("different Klipper SHA")
        with self.assertRaisesRegex(ValueError, "different Klipper"):
            self.apply()
        self.services.assert_not_called()
        self.assert_restored()
        self.assertFalse(core.journal_path(self.env, OPERATION).exists())

    def test_missing_loader_vision_units_fails_before_mutation(self):
        self.vision_units.return_value = False
        with self.assertRaisesRegex(ValueError, "сначала запустите loader"):
            self.apply()
        self.services.assert_not_called()
        self.stop_heating.assert_not_called()
        self.assertEqual(core.read_json(core.manifest_path(self.env))["version"], "0.1.0")
        self.assertFalse(core.journal_path(self.env, OPERATION).exists())

    def test_vision_service_and_manifest_are_in_runtime_bundle_not_model_weights(self):
        items = core.sources(ROOT)
        self.assertEqual(items["vision/service.py"], ROOT / "runtime-scripts/treed-vision/service.py")
        self.assertEqual(items["vision/models.json"], ROOT / "runtime-scripts/treed-vision/models.json")
        self.assertIn("moonraker/treed_vision_client.py", core.AUTOPRINTER_MODULES)
        self.assertNotIn("vision/fdm-detail-e0-20261016-8de7e8aa22e2.pth", items)

    def test_readiness_failure_restores_every_file_and_confirmed_version(self):
        self.wait.side_effect = [False, True]
        self.assertEqual(self.apply()["status"], "rolled_back")
        self.assert_restored()

    def test_partial_install_failure_restores_before_images(self):
        original = core.write_file
        injected = False
        def write(path, data, *args, **kwargs):
            nonlocal injected
            if path == self.locations["moonraker"] / "treed_update.py" and not injected:
                injected = True
                raise OSError("disk write failed")
            return original(path, data, *args, **kwargs)
        with patch.object(core, "write_file", side_effect=write):
            self.assertEqual(self.apply()["status"], "rolled_back")
        self.assert_restored()

    def test_phase_notification_failure_does_not_block_file_rollback(self):
        def notify(name, message):
            if name == "rolling_back":
                raise OSError("new service entrypoint unavailable")
        self.phase.side_effect = notify
        self.wait.side_effect = [False, True]
        self.assertEqual(self.apply()["status"], "rolled_back")
        self.assert_restored()

    def test_unknown_new_destination_is_not_overwritten(self):
        self.payload["klipper/treed_new_feature.py"] = b"version = 2\n"
        path = self.locations["klipper"] / "treed_new_feature.py"
        path.write_bytes(b"existing user file\n")
        with self.assertRaisesRegex(ValueError, "занято неизвестным"):
            self.apply()
        self.assertEqual(path.read_bytes(), b"existing user file\n")
        self.services.assert_not_called()

    def test_power_loss_after_partial_install_recovers_idempotently(self):
        original = core.write_file
        def write(path, data, *args, **kwargs):
            if path == self.locations["moonraker"] / "treed_update.py":
                raise KeyboardInterrupt("power loss")
            return original(path, data, *args, **kwargs)
        with patch.object(core, "write_file", side_effect=write):
            with self.assertRaises(KeyboardInterrupt):
                self.apply()
        self.assertEqual(core.recover(self.env, OPERATION)["status"], "rolled_back")
        self.assert_restored()
        self.services.reset_mock()
        self.assertEqual(core.recover(self.env, OPERATION)["status"], "rolled_back")
        self.services.assert_not_called()

    def test_committed_operation_recovers_as_applied(self):
        self.apply()
        self.services.reset_mock()
        self.assertEqual(core.recover(self.env, OPERATION)["status"], "applied")
        self.services.assert_not_called()

    def test_committed_journal_cannot_claim_a_different_installed_version(self):
        self.apply()
        core.write_json(core.manifest_path(self.env), self.previous)
        self.assertEqual(core.recover(self.env, OPERATION)["status"], "rolled_back")
        self.assert_restored()

    def test_bytecode_of_changed_modules_is_removed_before_restart(self):
        cache = self.locations["klipper"] / "__pycache__"
        cache.mkdir()
        changed = cache / "treed_driver_mode.cpython-311.pyc"
        other = cache / "unrelated.cpython-311.pyc"
        changed.write_bytes(b"new bytecode")
        other.write_bytes(b"keep")
        self.wait.side_effect = [False, True]
        self.assertEqual(self.apply()["status"], "rolled_back")
        self.assertFalse(changed.exists())
        self.assertEqual(other.read_bytes(), b"keep")

    def test_failed_rollback_readiness_does_not_claim_success(self):
        self.wait.return_value = False
        with self.assertRaisesRegex(RuntimeError, "Файлы восстановлены"):
            self.apply()

    def test_symlink_destination_cannot_overwrite_an_unrelated_file(self):
        unrelated = self.root / "outside.txt"
        unrelated.write_text("keep")
        destination = self.locations["klipper"] / "treed_driver_mode.py"
        destination.unlink()
        try:
            destination.symlink_to(unrelated)
        except OSError:
            self.skipTest("Создание symlink недоступно в этом Windows-сеансе")
        with self.assertRaises(ValueError):
            self.apply()
        self.assertEqual(unrelated.read_text(), "keep")
        self.services.assert_not_called()


class ManagedLoaderProcessTests(unittest.TestCase):
    def test_timeout_terminates_process_group_and_keeps_durable_log(self):
        class Process:
            pid = 4123
            returncode = -9

            def __init__(self):
                self.waits = 0

            def wait(self, timeout=None):
                self.waits += 1
                if self.waits == 1:
                    raise subprocess.TimeoutExpired("loader", timeout)
                return self.returncode

        process = Process()
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "loader.log"

            def spawn(*_args, **kwargs):
                kwargs["stdout"].write(b"loader started\n")
                return process

            with patch.object(core.subprocess, "Popen", side_effect=spawn) as popen, \
                 patch.object(core.os, "killpg", create=True) as killpg, \
                 patch.object(core.signal, "SIGKILL", 9, create=True), \
                 patch.object(core, "process_group_exists", side_effect=[True, True]), \
                 patch.object(core.time, "monotonic", side_effect=[0, 6]), \
                 patch.object(core.time, "sleep"):
                self.assertEqual(124, core.run_managed_loader(Path(temp), {}, log, timeout=1))
            self.assertEqual(b"loader started\n", log.read_bytes())
            if os.name != "nt":
                self.assertEqual(0o600, log.stat().st_mode & 0o777)
            popen.assert_called_once()
            self.assertTrue(popen.call_args.kwargs["start_new_session"])
            self.assertEqual([call(4123, core.signal.SIGTERM), call(4123, getattr(core.signal, "SIGKILL", 9))],
                             killpg.call_args_list)
            self.assertEqual(2, process.waits)


class PackageTests(unittest.TestCase):
    # Блок 4: Реальный release artifact; пути и checksum проверяются без extractall.
    def test_incomplete_autoprinter_archive_is_rejected_but_legacy_is_supported(self):
        base = {key: b"# fake runtime\n" for key in (
            "config/printer.cfg", "config/moonraker.conf", "config/moonraker/base/00-core.conf",
            "klipper/treed_driver_mode.py", "moonraker/treed_update.py", "sbin/treed-core-update",
        )}
        complete = {**base, "camera/stream_detect.py": b"# fake camera\n",
                    "vision/service.py": b"# fake service\n", "vision/models.json": b"{}",
                    **{key: b"# fake helper\n" for key in core.AUTOPRINTER_MODULES}}
        cases = [base, complete] + [{key: value for key, value in complete.items() if key != missing}
                                   for missing in sorted(core.AUTOPRINTER_MODULES)]
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / core.ASSET
            for index, payload in enumerate(cases):
                with self.subTest(case=index):
                    manifest = {"kind": core.KIND, "schema": 1, "version": "0.2.0", "tag": "v0.2.0",
                                "sha": "1" * 40, "requires": {}, "files": [
                                    {"path": key, "sha256": core.sha(data)} for key, data in payload.items()]}
                    with zipfile.ZipFile(archive, "w") as bundle:
                        bundle.writestr("manifest.json", json.dumps(manifest))
                        for key, data in payload.items():
                            bundle.writestr("payload/" + key, data)
                    if index < 2:
                        self.assertEqual(payload, core.load_package(archive, "v0.2.0")[1])
                    else:
                        with self.assertRaisesRegex(ValueError, "неполный AutoPrinter"):
                            core.load_package(archive, "v0.2.0")

    def test_delivered_autoprinter_imports_without_ml_or_neighbour_checkout(self):
        sources = core.sources(ROOT)
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp) / "delivered"
            package.mkdir()
            (package / "__init__.py").write_bytes(b"")
            for key in core.AUTOPRINTER_MODULES:
                (package / Path(key).name).write_bytes(sources[key].read_bytes())
            script = '''import importlib.abc, sys
class NoML(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.split(".")[0] in {"torch", "torchvision", "rfdetr", "PIL"}:
            raise AssertionError("heavy runtime imported during component load")
sys.meta_path.insert(0, NoML())
sys.path.insert(0, sys.argv[1])
from delivered import treed_autoprinter as component
class Server:
    def register_endpoint(self, *args, **kwargs): pass
    def register_event_handler(self, *args): pass
class Config:
    def get_server(self): return Server()
    def get(self, key, default=None): return default
instance = component.load_component(Config())
assert instance._store is None and instance._task is None
print("DELIVERED_IMPORT_OK")
'''
            result = subprocess.run([sys.executable, "-I", "-B", "-c", script, temp],
                                    cwd=temp, capture_output=True, text=True, timeout=15)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("DELIVERED_IMPORT_OK", result.stdout.strip())

    def test_repository_build_contains_only_runtime_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / core.ASSET
            core.build(ROOT, archive)
            manifest, payload = core.load_package(archive, "v" + (ROOT / "VERSION").read_text().strip())
        self.assertEqual(manifest["kind"], core.KIND)
        self.assertIn("sbin/treed-core-update", payload)
        self.assertIn("sbin/treed-loading-screen", payload)
        self.assertEqual(payload["sbin/treed-loading-screen"],
                         core.sources(ROOT)["sbin/treed-loading-screen"].read_bytes())
        self.assertIn("klipper/treed_driver_mode.py", payload)
        self.assertTrue({"vision/service.py", "vision/models.json", "camera/stream_detect.py",
                         "moonraker/treed_vision_client.py"} <= payload.keys())
        self.assertTrue(core.AUTOPRINTER_MODULES <= payload.keys())
        self.assertFalse(any(key.startswith("vision/") and key.endswith(".pth") for key in payload))
        self.assertFalse(any("local_overrides.cfg" in key for key in payload))
        self.assertFalse(any(key.startswith("loader/") for key in payload))
        self.assertEqual(manifest["provisioning"]["revision"], core.provisioning_revision(ROOT))

    def test_source_asset_matches_target_and_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            archive = temp / core.SOURCE_ASSET
            core.build_source(ROOT, archive)
            with zipfile.ZipFile(archive) as bundle:
                files = {name: bundle.read(name) for name in bundle.namelist()}
            directories = set()
            for name in files:
                parent = PurePosixPath(name).parent
                while parent.as_posix() != ".":
                    directories.add(parent.as_posix() + "/")
                    parent = parent.parent
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
                for name in sorted(directories):
                    bundle.writestr(name, b"")
                for name, data in files.items():
                    bundle.writestr(name, data)
            target_archive = temp / core.ASSET
            core.build(ROOT, target_archive)
            manifest, payload = core.load_package(target_archive, "v" + (ROOT / "VERSION").read_text().strip())
            with zipfile.ZipFile(archive) as bundle:
                self.assertTrue(any(row.is_dir() for row in bundle.infolist()))
            core.extract_source(archive, temp / "source", manifest, payload)
            with zipfile.ZipFile(archive, "a") as bundle:
                bundle.writestr("../escape", b"bad")
            with self.assertRaises(ValueError):
                core.extract_source(archive, temp / "bad-source", manifest, payload)

    def test_source_asset_runtime_mapping_must_match_verified_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            archive, runtime = temp / core.SOURCE_ASSET, temp / core.ASSET
            core.build_source(ROOT, archive); core.build(ROOT, runtime)
            manifest, payload = core.load_package(runtime, "v" + (ROOT / "VERSION").read_text().strip())
            with zipfile.ZipFile(archive) as bundle:
                files = {name: bundle.read(name) for name in bundle.namelist()}
            files["klipper-host/treed_driver_mode.py"] += b"# mismatch\n"
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
                for name, data in files.items():
                    bundle.writestr(name, data)
            with self.assertRaisesRegex(ValueError, "не совпадает с target payload"):
                core.extract_source(archive, temp / "source", manifest, payload)

    def test_provisioning_route_uses_revision_upstream_refs_and_required_units(self):
        target = {"requires": {"klipper": "a" * 40, "moonraker": "b" * 40},
                  "provisioning": {"schema": 1, "revision": "c" * 64}}
        installed = {"kind": core.KIND, "schema": 1, "provisioning": target["provisioning"]}
        roots = {"klipper": Path("/home/pi/klipper/klippy/extras"),
                 "moonraker": Path("/home/pi/moonraker/moonraker/components")}
        with patch.object(core, "roots", return_value=roots), \
             patch.object(core, "vision_units_installed", return_value=True), \
             patch.object(core, "git_head", side_effect=["a" * 40, "b" * 40]):
            self.assertFalse(core.needs_provisioning(target, installed, {}))
        for mutate in (lambda target, baseline: baseline.update(provisioning={"schema": 1, "revision": "d" * 64}),
                       lambda target, baseline: target["requires"].update(klipper="e" * 40)):
            candidate, baseline = dict(target, requires=dict(target["requires"])), dict(installed)
            mutate(candidate, baseline)
            with patch.object(core, "roots", return_value=roots), \
                 patch.object(core, "vision_units_installed", return_value=True), \
                 patch.object(core, "git_head", side_effect=["a" * 40, "b" * 40]):
                self.assertTrue(core.needs_provisioning(candidate, baseline, {}))
        with patch.object(core, "roots", return_value=roots), \
             patch.object(core, "vision_units_installed", return_value=False):
            self.assertTrue(core.needs_provisioning(target, installed, {}))

    def test_archive_corruption_extra_entries_and_duplicates_are_rejected(self):
        for kind in ("corruption", "extra", "duplicate", "system-bundle"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp:
                archive = Path(temp) / core.ASSET
                core.build(ROOT, archive)
                with zipfile.ZipFile(archive) as bundle:
                    content = {name: bundle.read(name) for name in bundle.namelist()}
                if kind == "corruption":
                    content["payload/klipper/treed_driver_mode.py"] = b"corrupted"
                elif kind == "extra":
                    content["../../etc/passwd"] = b"overwrite"
                elif kind == "system-bundle":
                    manifest = json.loads(content["manifest.json"])
                    manifest["kind"] = "system-bundle"
                    content["manifest.json"] = json.dumps(manifest).encode()
                with zipfile.ZipFile(archive, "w") as bundle:
                    for name, data in content.items():
                        bundle.writestr(name, data)
                    if kind == "duplicate":
                        import warnings
                        with warnings.catch_warnings():
                            warnings.simplefilter("ignore")
                            bundle.writestr("manifest.json", content["manifest.json"])
                with self.assertRaises(ValueError):
                    core.load_package(archive, "v" + (ROOT / "VERSION").read_text().strip())

    def test_paths_cannot_write_os_or_preserved_printer_data(self):
        for key in ("../etc/passwd", "sbin/ssh", "config/local_overrides.cfg", "config/treed_variables.cfg",
                    "config/moonraker/generated/webcam.conf", "config/profiles/treed_v2_corexy_v1/../user.cfg",
                    "config\\printer.cfg", "klipper/../../etc/shadow"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                core.allowed(key)

    def test_release_download_rejects_missing_and_mismatching_github_digest(self):
        for digest in (None, "sha256:" + "0" * 64):
            release = {"tag_name": "v0.2.0", "assets": [{"name": core.ASSET,
                       "digest": digest, "browser_download_url": "https://github.com/org/repo/releases/download/v0.2.0/core.zip"}]}
            responses = [io.BytesIO(json.dumps(release).encode()), io.BytesIO(b"wrong archive")]
            with self.subTest(digest=digest), tempfile.TemporaryDirectory() as temp, \
                 patch.object(core.urllib.request, "urlopen", side_effect=responses):
                with self.assertRaises(ValueError):
                    core.download("v0.2.0", Path(temp) / core.ASSET, {})

    def test_render_retains_device_updater_wiring_and_templates(self):
        key = "config/moonraker/base/00-core.conf"
        old = b"[update_manager mainsail]\npath: /my/device/path\n\n[treed_update]\n"
        new = b"[update_manager mainsail]\npath: /default\n\n[update_manager crowsnest]\npath: {{PI_HOME}}/crowsnest\n\n[treed_update]\nrepo_path: {{PI_HOME}}/treed/printer-core\n"
        result = core.render(key, new, old, {"TREED_UPDATE_PI_HOME": "/home/radxa", "TREED_UPDATE_PI_USER": "radxa"})
        self.assertIn(b"path: /my/device/path", result)
        self.assertIn(b"repo_path: /home/radxa/treed/printer-core", result)
        self.assertNotIn(b"[update_manager crowsnest]", result)

    def test_render_preserves_local_autoprinter_identity_and_adds_new_defaults(self):
        key = "config/moonraker/base/00-core.conf"
        old = ("[treed_autoprinter]\nprinter_id: local-id\napi_origin: https://local.example\n"
               "credentials_path: /private/credentials.json\nstorage_path: /private/state.sqlite3\n").encode()
        new = ("[treed_autoprinter]\nprinter_id: new-default\napi_origin: https://default.example\n"
               "credentials_path: /new/default.json\nstorage_path: /new/state.sqlite3\n"
               "bed_check_socket: /home/radxa/treed/vision/run/vision.sock\n").encode()
        result = core.render(key, new, old, {"TREED_UPDATE_PI_HOME": "/home/radxa",
                                             "TREED_UPDATE_PI_USER": "radxa"}).decode()
        self.assertIn("printer_id: local-id", result)
        self.assertIn("api_origin: https://local.example", result)
        self.assertIn("credentials_path: /private/credentials.json", result)
        self.assertIn("storage_path: /private/state.sqlite3", result)
        self.assertIn("bed_check_socket: /home/radxa/treed/vision/run/vision.sock", result)
        self.assertNotIn("printer_id: new-default", result)

    def test_compatibility_checks_both_pinned_upstreams_and_tracked_edits(self):
        manifest = {"requires": {"klipper": "a" * 40, "moonraker": "b" * 40}}
        env = {"TREED_UPDATE_PI_HOME": "/home/radxa"}
        locations = patch.object(core, "roots", return_value={
            "klipper": ROOT / "klippy/extras", "moonraker": ROOT / "moonraker/components"})
        locations.start()
        self.addCleanup(locations.stop)
        with patch.object(core, "git_head", side_effect=["a" * 40, "b" * 40]), \
             patch.object(core.subprocess, "check_output", return_value=""):
            core.check_compatibility(manifest, env)
        with patch.object(core, "git_head", return_value="c" * 40):
            with self.assertRaisesRegex(ValueError, "другую версию"):
                core.check_compatibility(manifest, env)
        with patch.object(core, "git_head", return_value="a" * 40), \
             patch.object(core.subprocess, "check_output", return_value=" M klippy.py"):
            with self.assertRaisesRegex(ValueError, "Изменены исходники"):
                core.check_compatibility(manifest, env)


# Блок 5: Реальный Git с checkout другого владельца, без глобального доверия.
class CoreGitOwnershipTests(unittest.TestCase):
    def test_only_hash_matching_manifest_managed_untracked_upstream_modules_are_allowed(self):
        with tempfile.TemporaryDirectory(prefix="treed managed upstream ") as temp, \
             patch.dict(os.environ, {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}):
            home = Path(temp)
            env = {"TREED_UPDATE_PI_HOME": str(home)}
            locations, requirements, previous_rows, target_rows = {}, {}, [], []
            for name, suffix in (("klipper", Path("klippy/extras")),
                                 ("moonraker", Path("moonraker/components"))):
                repo = home / name
                repo.mkdir()
                subprocess.run(["git", "init", "-q", str(repo)], check=True, timeout=10)
                (repo / "tracked.txt").write_text("original\n", encoding="utf-8")
                subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True, timeout=10)
                subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c",
                                "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
                                "commit", "-qm", "Initial"], check=True, timeout=10)
                requirements[name] = core.git_head(repo)
                locations[name] = repo / suffix
                locations[name].mkdir(parents=True)
                key = f"{name}/treed_calibration.py"
                data = f"# managed {name}\n".encode()
                (locations[name] / "treed_calibration.py").write_bytes(data)
                previous_rows.append({"path": key, "runtimeSha256": core.content_hash(key, data)})
                target_rows.append({"path": key, "sha256": core.sha(data)})

            previous = {"files": previous_rows}
            target = {"requires": requirements, "files": target_rows}
            with patch.object(core, "roots", return_value=locations):
                core.check_upstream_clean(locations, previous)
                core.check_compatibility(target, env, previous)
                unknown = locations["klipper"] / "locally_created.py"
                unknown.write_text("# not managed\n", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "Изменены исходники установленного klipper"):
                    core.check_upstream_clean(locations, previous)
                unknown.unlink()
                managed = locations["moonraker"] / "treed_calibration.py"
                managed.write_text("# locally modified\n", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "Изменены исходники установленного moonraker"):
                    core.check_compatibility(target, env, previous)
                managed.write_text("# managed moonraker\n", encoding="utf-8")
                tracked = home / "klipper/tracked.txt"
                tracked.write_text("local edit\n", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "Изменены исходники установленного klipper"):
                    core.check_upstream_clean(locations, previous)

    def test_cross_owner_read_preserves_version_and_dirty_checks(self):
        with tempfile.TemporaryDirectory(prefix="treed git ownership ") as temp, \
             patch.dict(os.environ, {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}):
            home = Path(temp)
            env = {"TREED_UPDATE_PI_HOME": str(home)}
            requires = {}
            for name in ("klipper", "moonraker"):
                repo = home / name
                subprocess.run(["git", "init", "-q", str(repo)], check=True, timeout=10)
                (repo / "tracked.txt").write_text("original\n", encoding="utf-8")
                subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True, timeout=10)
                subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c",
                                "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
                                "commit", "-qm", "Initial"], check=True, timeout=10)
                requires[name] = core.git_head(repo)
            manifest = {"requires": requires}
            locations = {"klipper": home / "klipper/klippy/extras",
                         "moonraker": home / "moonraker/moonraker/components"}
            with patch.dict(os.environ, {"GIT_TEST_ASSUME_DIFFERENT_OWNER": "1"}), \
                 patch.object(core, "roots", return_value=locations):
                for name, expected in requires.items():
                    repo = home / name
                    rejected = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                                              capture_output=True, text=True, timeout=10)
                    self.assertEqual(rejected.returncode, 128)
                    self.assertIn("dubious ownership", rejected.stderr)
                    self.assertEqual(core.git_head(repo), expected)
                core.check_compatibility(manifest, env)
                incompatible = {"requires": dict(requires, klipper="0" * 40)}
                with self.assertRaisesRegex(ValueError, "другую версию klipper"):
                    core.check_compatibility(incompatible, env)
                for name in requires:
                    with self.subTest(name=name):
                        tracked = home / name / "tracked.txt"
                        tracked.write_text("local edit\n", encoding="utf-8")
                        with self.assertRaisesRegex(ValueError, f"Изменены исходники установленного {name}"):
                            core.check_compatibility(manifest, env)
                        tracked.write_text("original\n", encoding="utf-8")


class PrinterUpdateGuardTests(unittest.TestCase):
    def test_readiness_requires_candidate_autoprinter_and_rejects_failed_components(self):
        loaded = ["treed_update", "treed_recovery", "treed_detection", "treed_host_network",
                  "treed_filament_sensor", "treed_shell_command"]
        legacy = {"klippy_state": "ready", "components": loaded, "failed_components": []}
        candidate = {"files": [{"path": "moonraker/treed_autoprinter.py"},
                               {"path": "moonraker/treed_vision_client.py"}]}
        for info, manifest, expected in (
            (legacy, {"files": []}, True),
            (legacy, candidate, False),
            (legacy | {"components": loaded + ["treed_vision_client"]}, candidate, False),
            (legacy | {"components": loaded + ["treed_autoprinter"]}, candidate, True),
            (legacy | {"components": loaded + ["treed_autoprinter"],
                       "failed_components": ["treed_vision_client"]}, candidate, True),
            (legacy | {"components": loaded + ["treed_autoprinter"],
                       "failed_components": ["treed_autoprinter"]}, candidate, False),
            (legacy | {"failed_components": ["treed_update"]}, {"files": []}, False),
            (legacy | {"components": "treed_update"}, candidate, False),
            (legacy | {"failed_components": None}, candidate, False),
        ):
            with self.subTest(info=info, candidate=bool(manifest["files"])), \
                    patch.object(core, "request_json", return_value=info), \
                    patch.object(core.time, "sleep"), \
                    patch.object(core.time, "monotonic", side_effect=[0, 1, 2, 3, 61]):
                self.assertEqual(expected, core.wait_ready(manifest))

    def test_wait_ready_uses_registered_component_gate_for_candidate_and_rollback(self):
        candidate = {"files": [{"path": "moonraker/treed_autoprinter.py"},
                               {"path": "moonraker/treed_vision_client.py"}]}
        previous = {"files": [{"path": "moonraker/treed_autoprinter.py"}]}
        with patch.object(core, "ready", return_value=True) as ready, \
             patch.object(core.time, "sleep"):
            self.assertTrue(core.wait_ready(candidate))
            self.assertTrue(core.wait_ready(previous))
        self.assertEqual([("treed_autoprinter",)] * 6,
                         [call.args[0] for call in ready.call_args_list])

    def test_cancelled_job_with_stale_pause_and_heat_is_idle(self):
        status = {"print_stats": {"state": " CANCELLED "}, "pause_resume": {"is_paused": True},
                  "extruder": {"target": 140}, "heater_bed": {"target": 60}}
        with patch.object(core, "ready", return_value=True), \
             patch.object(core, "request_json", return_value={"status": status}):
            self.assertTrue(core.idle())

    def test_paused_print_and_unknown_state_are_not_idle(self):
        for state in ("printing", "paused", "unknown", None, ""):
            with self.subTest(state=state), patch.object(core, "ready", return_value=True), \
                 patch.object(core, "request_json", return_value={"status": {"print_stats": {"state": state}}}):
                self.assertFalse(core.idle())

    def test_shutdown_heaters_requires_zero_targets_and_inactive_job(self):
        for state, nozzle, bed in (("cancelled", 0, 0), ("cancelled", 140, 0),
                                   ("cancelled", 0, 60), ("printing", 0, 0), ("paused", 0, 0)):
            status = {"print_stats": {"state": state}, "extruder": {"target": nozzle}, "heater_bed": {"target": bed}}
            with self.subTest(state=state, nozzle=nozzle, bed=bed), \
                 patch.object(core, "request_json", side_effect=["ok", {"status": status}]) as request:
                if state == "cancelled" and nozzle == bed == 0:
                    core.stop_heating()
                else:
                    with self.assertRaises(ValueError):
                        core.stop_heating()
                self.assertEqual(request.call_args_list[0].args, ("/printer/gcode/script", {"script": "TURN_OFF_HEATERS"}))


if __name__ == "__main__":
    unittest.main()
