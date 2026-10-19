"""Тесты контракта офлайн-адаптера устройства AutoPrinter.
Контур: read-only относительно устройства; использует только временные файлы.
"""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from moonraker.components import treed_autoprinter_device
from moonraker.components.treed_autoprinter_device import DeviceError, MoonrakerDevice


RUN_ID = "123e4567-e89b-42d3-a456-426614174000"


class DeviceTests(unittest.IsolatedAsyncioTestCase):
    # Блок 1: Изолированный fake Moonraker и тестовые данные во временном каталоге.
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "gcodes"
        self.root.mkdir()
        self.server = SimpleNamespace()
        self.klippy = SimpleNamespace(
            query_objects=AsyncMock(return_value={
                "print_stats": {"filename": "", "state": "standby"},
                "virtual_sdcard": {"is_active": False},
                "gcode_macro _TREED_OPERATION_STATE": {"phase": "idle"},
            }),
            start_print=AsyncMock(), pause_print=AsyncMock(), cancel_print=AsyncMock(),
        )
        self.queue = SimpleNamespace(load_on_start=False, automatic=False, queued_jobs={}, queue_state="paused")
        self.update = SimpleNamespace(state_file=Path(self.temp.name) / "update-state.json")
        self.update.state_file.write_text('{"busy": false}', encoding="utf-8")
        self.file_manager = SimpleNamespace(
            get_directory=lambda root: str(self.root),
            get_full_path=lambda root, name: self.root / name,
        )
        components = {"klippy_apis": self.klippy, "job_queue": self.queue,
                      "treed_update": self.update, "file_manager": self.file_manager}
        self.server.lookup_component = components.__getitem__
        self.device = MoonrakerDevice(self.server)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _asset(self, body: bytes) -> tuple[Path, dict]:
        source = Path(self.temp.name) / "asset.gcode"
        source.write_bytes(body)
        return source, {"binding_epoch": 3, "run_id": RUN_ID, "sha256": hashlib.sha256(body).hexdigest(),
                        "size_bytes": len(body), "profile_id": "profile", "completion_profile":
                        {"version": 1, "profile_id": "profile", "auto_remove": False}}

    async def test_prepare_copies_verified_immutable_bytes_to_unique_gcodes_path(self) -> None:
        body = b"G28\nEND_PRINT\n"
        source, run = self._asset(body)
        filename = await self.device.prepare(run, source)
        self.assertTrue(filename.startswith(f"treed-autoprinter/3-{RUN_ID}-"))
        self.assertEqual(body, (self.root / filename).read_bytes())
        self.assertEqual(filename, await self.device.prepare(run, source))

    async def test_existing_identical_spool_file_does_not_consume_quota_again(self) -> None:
        source, run = self._asset(b"END_PRINT\n")
        filename = await self.device.prepare(run, source)
        with patch.object(treed_autoprinter_device, "MAX_SPOOL_BYTES", 1), \
                patch.object(treed_autoprinter_device.shutil, "disk_usage", side_effect=AssertionError):
            self.assertEqual(filename, await self.device.prepare(run, source))

    async def test_new_spool_file_obeys_total_quota_and_free_space_reserve(self) -> None:
        body = b"END_PRINT\n"
        source, run = self._asset(body)
        task_spool = self.root / "treed-autoprinter"
        task_spool.mkdir()
        (task_spool / ".interrupted-copy.tmp").write_bytes(b"partial")
        with patch.object(treed_autoprinter_device, "MAX_SPOOL_BYTES", len(body) + len(b"partial") - 1), \
                patch.object(treed_autoprinter_device.shutil, "disk_usage", return_value=SimpleNamespace(free=100_000_000)):
            with self.assertRaises(DeviceError) as error:
                await self.device.prepare(run, source)
            self.assertEqual("spool_quota_exceeded", error.exception.code)
        with patch.object(treed_autoprinter_device, "MAX_SPOOL_BYTES", 1_000), \
                patch.object(treed_autoprinter_device.shutil, "disk_usage",
                             return_value=SimpleNamespace(free=treed_autoprinter_device.MIN_FREE_BYTES + len(body) - 1)):
            with self.assertRaises(DeviceError) as error:
                await self.device.prepare(run, source)
            self.assertEqual("spool_quota_exceeded", error.exception.code)

    # Блок 2: Границы целостности G-code и неизвестного runtime-состояния.
    async def test_prepare_rejects_conflicting_completion_profile_and_ambiguous_macro(self) -> None:
        source, run = self._asset(b"END_PRINT AUTO_REMOVE=1\n")
        with self.assertRaises(DeviceError):
            await self.device.prepare(run, source)
        source, run = self._asset(b"END_PRINT\nEND_PRINT AUTO_REMOVE=0\n")
        with self.assertRaises(DeviceError):
            await self.device.prepare(run, source)

    async def test_inspect_fails_closed_for_unreadable_operation_state(self) -> None:
        self.update.state_file.write_text("not-json", encoding="utf-8")
        observed = await self.device.inspect()
        self.assertFalse(observed["ready"])
        self.assertTrue(observed["service_active"])
        self.assertFalse(observed["queue_safe"])

    async def test_raw_updater_busy_flag_blocks_runtime(self) -> None:
        self.update.state_file.write_text('{"busy": true}', encoding="utf-8")
        observed = await self.device.inspect()
        self.assertTrue(observed["service_active"])

    async def test_missing_updater_state_matches_component_idle_default(self) -> None:
        self.update.state_file.unlink()
        observed = await self.device.inspect()
        self.assertTrue(observed["ready"])
        self.assertFalse(observed["service_active"])

    async def test_previous_terminal_print_allows_next_start_without_reset(self) -> None:
        body = b"END_PRINT\n"
        source, run = self._asset(body)
        filename = await self.device.prepare(run, source)
        self.klippy.query_objects.return_value["print_stats"] = {
            "filename": "treed-autoprinter/previous-run.gcode", "state": "complete"}
        await self.device.start(filename)
        self.klippy.start_print.assert_awaited_once_with(filename, wait_klippy_started=False, user=None)

    async def test_virtual_sd_active_blocks_terminal_state_start(self) -> None:
        body = b"END_PRINT\n"
        source, run = self._asset(body)
        filename = await self.device.prepare(run, source)
        self.klippy.query_objects.return_value["print_stats"] = {"filename": filename, "state": "complete"}
        self.klippy.query_objects.return_value["virtual_sdcard"]["is_active"] = True
        with self.assertRaises(DeviceError):
            await self.device.start(filename)
        self.klippy.start_print.assert_not_awaited()

    async def test_start_rechecks_runtime_and_calls_only_pinned_start_api(self) -> None:
        body = b"END_PRINT\n"
        source, run = self._asset(body)
        filename = await self.device.prepare(run, source)
        await self.device.start(filename)
        self.klippy.start_print.assert_awaited_once_with(filename, wait_klippy_started=False, user=None)

    async def test_reconcile_requires_exact_unique_filename(self) -> None:
        self.klippy.query_objects.return_value["print_stats"] = {"filename": "elsewhere.gcode", "state": "complete"}
        result = await self.device.reconcile({"filename": "treed-autoprinter/expected.gcode", "run_id": RUN_ID})
        self.assertEqual("unknown", result["state"])
        self.assertIsNone(result["evidence_source"])

    async def test_reconcile_preserves_paused_state_for_exact_filename(self) -> None:
        filename = f"treed-autoprinter/3-{RUN_ID}-{'a' * 64}.gcode"
        self.klippy.query_objects.return_value["print_stats"] = {"filename": filename, "state": "paused"}
        self.klippy.query_objects.return_value["gcode_macro _TREED_OPERATION_STATE"]["phase"] = "paused"
        result = await self.device.reconcile({"filename": filename, "run_id": RUN_ID})
        self.assertEqual("paused", result["state"])
        self.assertEqual(filename, result["filename"])
        self.assertEqual("runtime", result["evidence_source"])


if __name__ == "__main__":
    unittest.main()
