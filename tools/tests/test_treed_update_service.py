"""Изолированные проверки root update queue и persistent operation state."""

import contextlib
import configparser
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SERVICE_PATH = ROOT / "runtime-scripts/treed-update/treed-update-service"
service = types.ModuleType("treed_update_service")
sys.modules[service.__name__] = service
exec(compile(SERVICE_PATH.read_text(encoding="utf-8"), str(SERVICE_PATH), "exec"), service.__dict__)


class FakeLock:
    def fileno(self):
        return 42

    def close(self):
        pass


class UpdateServiceTests(unittest.TestCase):
    def test_override_copy_failure_aborts_before_runtime_rebuild(self):
        git = shutil.which("git")
        bash = str(Path(git).parents[1] / "bin/bash.exe") if sys.platform == "win32" and git else shutil.which("bash")
        if not bash or not Path(bash).is_file():
            self.skipTest("Bash недоступен для проверки сохранения override")
        loader = (ROOT / "loader/steps/klipper-core.sh").read_text(encoding="utf-8")
        block = loader.split("# Блок 4:", 1)[1].split("# Блок 5:", 1)[0].split("\n", 1)[1]
        # Достижение следующего блока означает разрешение на полную раскладку runtime.
        for fails in (False, True):
            with self.subTest(copy_fails=fails), tempfile.TemporaryDirectory() as temp:
                config = Path(temp) / "config"
                config.mkdir()
                override = config / "local_overrides.cfg"
                override.write_bytes(b"[extruder]\npressure_advance: 0.123\n")
                prelude = 'set -e\nTMPDIR="$PWD"\nDEPLOY_MODE=preserve\nCONFIG_DIR=./config\nlog_info() { :; }\nlog_error() { :; }\n'
                if fails:
                    prelude += 'cp() { return 1; }\n'
                script = prelude + block + '\ntouch rebuild_allowed\n'
                result = subprocess.run([bash, "-c", script], cwd=temp, capture_output=True)
                self.assertEqual(result.returncode, 1 if fails else 0, result.stderr)
                self.assertEqual((Path(temp) / "rebuild_allowed").exists(), not fails)
                self.assertEqual(override.read_bytes(), b"[extruder]\npressure_advance: 0.123\n")

    def test_printer_guard_ignores_stale_flags_but_rejects_active_and_unknown_jobs(self):
        for state, expected in ((" CANCELLED ", True), ("complete", True), ("standby", True),
                                ("error", True), ("paused", False), ("printing", False),
                                ("unknown", False), ("", False), (None, False)):
            payload = {"result": {"status": {"print_stats": {"state": state},
                                            "pause_resume": {"is_paused": True}}}}
            with self.subTest(state=state), \
                 patch.object(service.urllib.request, "urlopen", return_value=io.BytesIO(json.dumps(payload).encode())):
                self.assertEqual(service.printer_is_idle(), expected)

    def test_path_unit_watches_state_file_without_creating_a_directory(self):
        config = configparser.ConfigParser()
        config.read(SERVICE_PATH.with_name("treed-update.path"), encoding="utf-8")
        self.assertEqual(config["Path"]["PathChanged"], "/var/lib/treed-update/state.json")
        self.assertFalse(config["Path"].getboolean("MakeDirectory", fallback=False))

    def test_loader_repairs_only_empty_state_directory(self):
        git = shutil.which("git")
        bash = str(Path(git).parents[1] / "bin/bash.exe") if sys.platform == "win32" and git else shutil.which("bash")
        if not bash or not Path(bash).is_file():
            self.skipTest("Bash недоступен для проверки миграции loader")
        loader = (ROOT / "loader/steps/moonraker-config.sh").read_text(encoding="utf-8")
        block = loader.split("  # Блок 3.2:", 1)[1].split("  systemctl daemon-reload", 1)[0]
        block = block[block.index("  if "):].replace("/var/lib/treed-update/state.json", "./state.json")
        script = "set -e\nsystemctl() { echo \"$*\" >> calls; }\nlog_error() { :; }\nlog_warn() { :; }\n" + block
        for kind in ("missing", "file", "empty", "nonempty"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp:
                state = Path(temp) / "state.json"
                if kind == "file":
                    state.write_text('{"history": []}', encoding="utf-8")
                elif kind in ("empty", "nonempty"):
                    state.mkdir()
                    if kind == "nonempty":
                        (state / "keep.json").write_text("сохранить", encoding="utf-8")
                result = subprocess.run([bash, "-c", script], cwd=temp, capture_output=True)
                self.assertEqual(result.returncode, 1 if kind == "nonempty" else 0)
                if kind == "empty":
                    self.assertFalse(state.exists())
                    self.assertEqual((Path(temp) / "calls").read_text().strip(), "stop treed-update.path")
                elif kind == "nonempty":
                    self.assertEqual((state / "keep.json").read_text(encoding="utf-8"), "сохранить")
                elif kind == "file":
                    self.assertEqual(state.read_text(encoding="utf-8"), '{"history": []}')
                    self.assertFalse((Path(temp) / "calls").exists())
                else:
                    self.assertFalse(state.exists())

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        service.STATE = root / "state.json"
        service.LOCK = root / "lock"
        service.LOG = root / "worker.log"
        service.fcntl = types.SimpleNamespace(LOCK_EX=1, LOCK_NB=2, LOCK_UN=4,
                                               flock=lambda *_args: None)
        self.root = root

    def test_submit_is_idempotent_and_does_not_replace_active_operation(self):
        command = ["11111111-1111-4111-8111-111111111111", "printer-ui", "ui-main-123-1"]
        output = io.StringIO()
        with patch.object(service.os, "geteuid", return_value=0, create=True), \
             patch.object(service, "acquire", return_value=FakeLock()), \
             patch.object(service.subprocess, "run") as start_service, \
             contextlib.redirect_stdout(output):
            self.assertEqual(service.submit(*command), 0)
            first = json.loads(output.getvalue())
            output.seek(0); output.truncate(0)
            self.assertEqual(service.submit(*command), 0)
            duplicate = json.loads(output.getvalue())
            output.seek(0); output.truncate(0)
            other = ["22222222-2222-4222-8222-222222222222", "printer-ui", "ui-main-124-1"]
            self.assertEqual(service.submit(*other), 4)
            busy = json.loads(output.getvalue())

        persisted = json.loads(service.STATE.read_text(encoding="utf-8"))
        self.assertEqual(first["operationId"], duplicate["operationId"])
        self.assertTrue(duplicate["duplicate"])
        self.assertFalse(busy["accepted"])
        self.assertEqual(persisted["requestId"], command[0])
        self.assertEqual(persisted["operationId"], first["operationId"])
        start_service.assert_called_once()

    def test_lock_contention_returns_existing_operation_without_rewrite(self):
        existing = {
            "operationId": "operation-1", "requestId": "11111111-1111-4111-8111-111111111111",
            "targetId": "printer-ui", "targetTag": "ui-main-123-1", "status": "installing",
            "phase": "installing", "busy": True, "history": [],
        }
        service.write_state(existing)
        output = io.StringIO()
        with patch.object(service.os, "geteuid", return_value=0, create=True), \
             patch.object(service, "acquire", return_value=None), \
             contextlib.redirect_stdout(output):
            result = service.submit("22222222-2222-4222-8222-222222222222", "printer-ui", "ui-main-124-1")
        response = json.loads(output.getvalue())
        persisted = json.loads(service.STATE.read_text(encoding="utf-8"))
        self.assertEqual(result, 4)
        self.assertFalse(response["accepted"])
        self.assertEqual(response["operationId"], "operation-1")
        self.assertEqual(persisted["operationId"], "operation-1")

    def test_terminal_state_is_durable_and_keeps_bounded_history(self):
        operation = {
            "operationId": "operation-2", "requestId": "11111111-1111-4111-8111-111111111111",
            "targetId": "printer-ui", "targetTag": "ui-main-123-1", "status": "installing",
            "phase": "installing", "busy": True, "progress": None, "history": [],
        }
        service.write_state(operation)
        service.finish(operation, "applied", "ui_updated", "Обновление TreeD UI завершено.")
        state = json.loads(service.STATE.read_text(encoding="utf-8"))
        self.assertFalse(state["busy"])
        self.assertEqual(state["status"], "applied")
        self.assertEqual(state["phase"], "complete")
        self.assertEqual(state["progress"], 100)
        self.assertEqual(len(state["history"]), 1)

    def test_invalid_identifiers_tags_and_shell_metacharacters_are_rejected(self):
        invalid = [
            ("not-a-uuid", "printer-ui", "ui-main-123-1"),
            ("11111111-1111-4111-8111-111111111111", "printer-ui;touch /tmp/x", "ui-main-123-1"),
            ("11111111-1111-4111-8111-111111111111", "printer-ui", "ui-main-123-1;touch /tmp/x"),
        ]
        with patch.object(service.os, "geteuid", return_value=0, create=True), \
             patch.object(service, "acquire", return_value=FakeLock()), \
             patch.object(service.subprocess, "run") as start_service:
            for arguments in invalid:
                with self.assertRaises(ValueError):
                    service.submit(*arguments)
        start_service.assert_not_called()

    def test_systemd_start_failure_clears_busy_state(self):
        output = io.StringIO()
        with patch.object(service.os, "geteuid", return_value=0, create=True), \
             patch.object(service, "acquire", return_value=FakeLock()), \
             patch.object(service.subprocess, "run", side_effect=TimeoutError("systemd timeout")), \
             contextlib.redirect_stdout(output):
            result = service.submit(
                "11111111-1111-4111-8111-111111111111", "printer-ui", "ui-main-123-1")
        state = json.loads(service.STATE.read_text(encoding="utf-8"))
        self.assertEqual(result, 1)
        self.assertEqual(state["status"], "error")
        self.assertEqual(state["resultCode"], "service_start_failed")
        self.assertFalse(state["busy"])

    def test_core_worker_uses_managed_runtime_package_and_persists_result(self):
        operation = {
            "operationId": "operation-3", "requestId": "11111111-1111-4111-8111-111111111111",
            "targetId": "printer-core", "targetTag": "v1.2.3", "status": "queued",
            "phase": "queued", "busy": True, "progress": 0, "history": [],
        }
        service.write_state(operation)
        with patch.object(service.os, "geteuid", return_value=0, create=True), \
             patch.object(service, "acquire", return_value=FakeLock()), \
             patch.object(service, "printer_is_idle", return_value=True), \
             patch.object(service.subprocess, "run", return_value=types.SimpleNamespace(
                 returncode=0, stdout=json.dumps({"status": "applied", "resultCode": "core_updated",
                                                 "message": "Core установлен."}))) as apply:
            self.assertEqual(service.run_worker(), 0)
        state = json.loads(service.STATE.read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "applied")
        self.assertEqual(state["resultCode"], "core_updated")
        self.assertFalse(state["busy"])
        self.assertEqual(apply.call_args.args[0], [service.CORE_APPLY, "apply", "v1.2.3", "operation-3"])

    def test_core_recovery_records_rollback_from_independent_installer(self):
        service.write_state({"operationId": "operation-3", "targetId": "printer-core",
                             "targetTag": "v1.2.3", "status": "installing", "busy": True})
        with patch.object(service.os, "geteuid", return_value=0, create=True), \
             patch.object(service, "acquire", return_value=FakeLock()), \
             patch.object(service.subprocess, "run", return_value=types.SimpleNamespace(
                 returncode=1, stdout=json.dumps({"status": "rolled_back", "resultCode": "rollback_succeeded",
                                                 "message": "Core восстановлен."}))) as command:
            self.assertEqual(service.recover(), 0)
        self.assertEqual(command.call_args.args[0], [service.CORE_APPLY, "recover", "operation-3"])
        self.assertEqual(service.read_state()["status"], "rolled_back")

    def test_core_resume_recovers_instead_of_overwriting_interrupted_transaction(self):
        service.write_state({"operationId": "operation-3", "targetId": "printer-core",
                             "targetTag": "v1.2.3", "status": "installing", "busy": True})
        with patch.object(service.os, "geteuid", return_value=0, create=True), \
             patch.object(service, "acquire", return_value=FakeLock()), \
             patch.object(service, "run_core", return_value=1) as command, \
             patch.object(service, "printer_is_idle") as idle:
            self.assertEqual(service.run_worker(), 1)
        self.assertTrue(command.call_args.kwargs["recovery"])
        idle.assert_not_called()

    def test_reboot_recovery_marks_interrupted_operation_without_claiming_rollback(self):
        operation = {
            "operationId": "operation-4", "requestId": "11111111-1111-4111-8111-111111111111",
            "targetId": "printer-ui", "targetTag": "ui-main-123-1", "status": "installing",
            "phase": "installing", "busy": True, "history": [],
        }
        service.write_state(operation)
        with patch.object(service.os, "geteuid", return_value=0, create=True), \
             patch.object(service, "acquire", return_value=FakeLock()):
            self.assertEqual(service.recover(), 0)
        state = json.loads(service.STATE.read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "error")
        self.assertEqual(state["resultCode"], "operation_interrupted")
        self.assertFalse(state["busy"])
        self.assertNotIn("rollbackSucceeded", state)

    def test_interrupted_ui_publication_restores_previous_files_and_checks_readiness(self):
        operation_id = "11111111-1111-4111-8111-111111111111"
        runtime = self.root / "treed" / "treed-shell-runtime"
        current = runtime / "ui"
        rollback = runtime / f"ui.rollback.{operation_id}"
        current.mkdir(parents=True)
        rollback.mkdir()
        (current / "version.txt").write_text("new", encoding="utf-8")
        (rollback / "version.txt").write_text("previous", encoding="utf-8")
        operation = {
            "operationId": operation_id, "requestId": "22222222-2222-4222-8222-222222222222",
            "targetId": "printer-ui", "targetTag": "ui-main-123-1", "status": "installing",
            "phase": "installing", "busy": True, "history": [],
        }
        service.write_state(operation)
        with patch.object(service.os, "geteuid", return_value=0, create=True), \
             patch.object(service, "acquire", return_value=FakeLock()), \
             patch.object(service, "shell_runtime_dir", return_value=runtime), \
             patch.object(service, "wait_shell_ready", return_value=True), \
             patch.object(service.subprocess, "run"):
            self.assertEqual(service.recover(), 0)
        state = json.loads(service.STATE.read_text(encoding="utf-8"))
        self.assertEqual((current / "version.txt").read_text(encoding="utf-8"), "previous")
        self.assertFalse(rollback.exists())
        self.assertEqual(state["status"], "rolled_back")
        self.assertEqual(state["resultCode"], "rollback_succeeded")

    def test_recovery_confirms_a_fully_published_and_ready_ui_without_rollback_directory(self):
        operation_id = "33333333-3333-4333-8333-333333333333"
        runtime = self.root / "treed" / "treed-shell-runtime"
        current = runtime / "ui"
        current.mkdir(parents=True)
        manifest = {"runNumber": 123, "runAttempt": 1, "mode": "live"}
        (current / "treed-shell-ui-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        operation = {
            "operationId": operation_id, "requestId": "22222222-2222-4222-8222-222222222222",
            "targetId": "printer-ui", "targetTag": "ui-main-123-1", "status": "verifying",
            "phase": "verifying", "busy": True, "history": [],
        }
        service.write_state(operation)
        with patch.object(service.os, "geteuid", return_value=0, create=True), \
             patch.object(service, "acquire", return_value=FakeLock()), \
             patch.object(service, "shell_runtime_dir", return_value=runtime), \
             patch.object(service, "wait_shell_ready", return_value=True):
            self.assertEqual(service.recover(), 0)
        state = json.loads(service.STATE.read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "applied")
        self.assertEqual(state["resultCode"], "ui_updated_recovered")


    def test_combined_update_runs_core_then_ui_and_stops_on_core_failure(self):
        for outcome in ("applied", "error", "rolled_back"):
            with self.subTest(core_result=outcome):
                service.write_state({"status": "idle"})
                calls = []

                def run(command, **_kwargs):
                    calls.append(command)
                    if command[0] == service.CORE_APPLY:
                        return types.SimpleNamespace(returncode=0 if outcome == "applied" else 1,
                            stdout=json.dumps({"status": outcome, "message": "core result"}))
                    if command[0] == service.APPLY:
                        self.assertTrue(service.read_state()["busy"])
                        self.assertTrue(service.read_state()["coreUpdated"])
                    return types.SimpleNamespace(returncode=0)

                with patch.object(service.os, "geteuid", return_value=0, create=True), \
                     patch.object(service, "acquire", return_value=FakeLock()), \
                     patch.object(service, "printer_is_idle", return_value=True), \
                     patch.object(service.subprocess, "run", side_effect=run), \
                     contextlib.redirect_stdout(io.StringIO()):
                    service.submit("11111111-1111-4111-8111-111111111111", "printer-core", "v0.2.0", "ui-main-123-1")
                    queued = service.read_state()
                    self.assertEqual(queued["pendingUiTag"], "ui-main-123-1")
                    service.run_worker()
                final = service.read_state()
                self.assertEqual(final["operationId"], queued["operationId"])
                self.assertFalse(final["busy"])
                self.assertEqual(final["status"], outcome)
                self.assertEqual([call[0] for call in calls],
                                 ["systemctl", service.CORE_APPLY] + ([service.APPLY] if outcome == "applied" else []))

    def test_combined_update_preserves_next_step_across_recovery(self):
        state = {"operationId": "batch", "requestId": "request", "targetId": "printer-core",
                 "targetTag": "v0.2.0", "pendingUiTag": "ui-main-123-1", "busy": True, "status": "verifying"}
        service.finish(state, "applied", "core_updated", "core ready")
        with patch.object(service.os, "geteuid", return_value=0, create=True), \
             patch.object(service, "acquire", return_value=FakeLock()), \
             patch.object(service.subprocess, "run") as restart:
            service.recover()
            restart.assert_called_once_with(["systemctl", "start", "--no-block", service.UNIT], check=True, timeout=3)
        resumed = service.read_state()
        self.assertEqual((resumed["targetId"], resumed["targetTag"], resumed["status"]),
                         ("printer-ui", "ui-main-123-1", "queued"))
        self.assertTrue(resumed["busy"])
        service.finish(resumed, "error", "ui_failed", "Интерфейс не обновлён.")
        self.assertEqual(service.read_state()["message"], "Система обновлена. Интерфейс не обновлён.")

    def test_combined_submission_validates_both_targets_before_acquiring_lock(self):
        with patch.object(service.os, "geteuid", return_value=0, create=True), \
             patch.object(service, "acquire") as acquire:
            for target, tag, ui_tag in (("printer-ui", "ui-main-1-1", "ui-main-2-1"),
                                        ("printer-core", "v0.2.0", "../../bad")):
                with self.assertRaises(ValueError):
                    service.submit("11111111-1111-4111-8111-111111111111", target, tag, ui_tag)
            acquire.assert_not_called()

    def test_config_reset_selection_is_validated_and_persisted_with_combined_update(self):
        selection = [{"path": "config/profiles/treed_v2_corexy_v1/service_fans.cfg", "sha256": "a" * 64}]
        request = "11111111-1111-4111-8111-111111111111"
        with patch.object(service.os, "geteuid", return_value=0, create=True), \
             patch.object(service, "acquire", return_value=FakeLock()), \
             patch.object(service.fcntl, "flock"), patch.object(service.subprocess, "run"):
            self.assertEqual(service.submit(request, "printer-core", "v0.2.0", "ui-main-123-1", json.dumps(selection)), 0)
        self.assertEqual(service.read_state()["resetConfigs"], selection)
        self.assertEqual(service.read_state()["pendingUiTag"], "ui-main-123-1")
        with patch.object(service.os, "geteuid", return_value=0, create=True), \
             patch.object(service, "acquire") as acquire:
            for invalid in ([{"path": "config/../printer.cfg", "sha256": "a" * 64}], selection * 2, False):
                with self.assertRaises(ValueError):
                    service.submit(request, "printer-core", "v0.2.0", "", json.dumps(invalid))
            with self.assertRaises(ValueError):
                service.submit(request, "printer-ui", "ui-main-123-1", "", json.dumps(selection))
            acquire.assert_not_called()


if __name__ == "__main__":
    unittest.main()
