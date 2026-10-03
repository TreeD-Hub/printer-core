"""Изолированные проверки root update queue и persistent operation state."""

import contextlib
import io
import json
from pathlib import Path
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
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        service.STATE = root / "state.json"
        service.LOCK = root / "lock"
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

    def test_system_update_fails_closed_without_verified_ab_backend(self):
        operation = {
            "operationId": "operation-3", "requestId": "11111111-1111-4111-8111-111111111111",
            "targetId": "printer-core", "targetTag": "v1.2.3", "status": "queued",
            "phase": "queued", "busy": True, "progress": 0, "history": [],
        }
        service.write_state(operation)
        with patch.object(service.os, "geteuid", return_value=0, create=True), \
             patch.object(service, "acquire", return_value=FakeLock()), \
             patch.object(service, "printer_is_idle", return_value=True), \
             patch.object(service, "system_ab_capability", return_value={
                 "supported": False,
                 "reasonCode": "ab_platform_unverified",
                 "reason": "Обновление системы недоступно: платформа не подтверждена.",
             }), \
             patch.object(service.subprocess, "run") as apply:
            self.assertEqual(service.run_worker(), 1)
        state = json.loads(service.STATE.read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "rejected")
        self.assertEqual(state["resultCode"], "ab_platform_unverified")
        self.assertFalse(state["busy"])
        apply.assert_not_called()

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


if __name__ == "__main__":
    unittest.main()
