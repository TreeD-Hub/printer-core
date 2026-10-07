"""Адресная офлайн-проверка реакции на дефект; без сети и устройства."""

import asyncio
import importlib.util
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock
import sys

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "treed_detection", ROOT / "moonraker/components/treed_detection.py")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
stream_spec = importlib.util.spec_from_file_location(
    "stream_detect", ROOT / "runtime-scripts/treed-cam/stream_detect.py")
stream = importlib.util.module_from_spec(stream_spec)
stream_spec.loader.exec_module(stream)


# Блок 1: Модель только внешнего Moonraker API; проверяется реальный компонент.
class Request:
    def __init__(self, **args):
        self.args = args

    def get(self, key, default=None):
        return self.args.get(key, default)

    def get_str(self, key, default=None):
        value = self.get(key, default)
        if not isinstance(value, str):
            raise ValueError(key)
        return value

    def get_action(self):
        return self.get("action", "GET")


class DetectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.frames = self.root / "prints"
        self.session = self.frames / "part__20260930_120000"
        self.session.mkdir(parents=True)
        self.marker = self.root / "session"
        self.marker.write_text(str(self.session), encoding="utf-8")
        self.objects = {
            "print_stats": {"state": "printing"},
            "gcode_macro _TREED_CAM_STATE": {"enabled": 1, "generation": 1},
            "gcode_macro _TREED_OPERATION_STATE": {"phase": "printing"},
        }
        self.api = NS(query_objects=AsyncMock(return_value=self.objects),
                      run_gcode=AsyncMock(return_value="ok"))
        self.endpoints = {}
        server = NS(lookup_component=lambda _: self.api,
                    error=lambda message, code=400: ValueError((code, message)),
                    register_endpoint=lambda path, methods, handler, **kwargs:
                    self.endpoints.update({path: (methods, handler)}))
        values = {"frames_root": str(self.frames), "session_file": str(self.marker)}
        self.config = NS(get_server=lambda: server, get=lambda key, default=None: values.get(key, default))
        self.component = module.TreeDDetection(self.config)
        self.session_id = f"1:0:{self.session.name}"

    async def result(self, index, critical=True, **overrides):
        frame_id = f"img_{index}.jpg"
        frame = self.session / frame_id
        if not frame.exists():
            frame.write_bytes(b"JPEG placeholder")
            timestamp = 1_700_000_000_000_000_000 + index * 1_000_000_000
            os.utime(frame, ns=(timestamp, timestamp))
        args = dict(session_id=self.session_id, frame_id=frame_id,
                    critical=critical, defect="spaghetti")
        args.update(overrides)
        return await self.component._handle_result(Request(**args))

    # Блок 2: Порог, сброс и единственная команда отмены.
    async def test_three_consecutive_results_cancel_once(self):
        for index in (1, 2):
            state = await self.result(index)
            self.assertEqual(state["counter"], index)
            self.api.run_gcode.assert_not_awaited()
        state = await self.result(3)
        self.assertTrue(state["cancel_requested"])
        await self.result(4)
        self.api.run_gcode.assert_awaited_once_with("_TREED_DETECTION_CANCEL GENERATION=1")

    async def test_stream_adapter_reset_failure_and_pause_before_cancel(self):
        # Настоящий адаптер и компонент; заменён только HTTP и внешний Klipper API.
        loop = asyncio.get_running_loop()
        reaction = stream.Reaction("http://127.0.0.1:7125", self.frames, self.marker, 1)

        def request(endpoint, data=None):
            operation = (self.component._handle_status(None) if data is None
                         else self.component._handle_result(Request(**data)))
            return asyncio.run_coroutine_threadsafe(operation, loop).result(timeout=2)

        reaction.request = request
        self.component._decision.count = 2  # Остаток серии до перезапуска клиента.
        self.assertTrue(await asyncio.to_thread(reaction.prepare))
        self.assertTrue(reaction.reset_pending)
        await asyncio.to_thread(reaction.publish, None, b"JPEG")
        for _ in range(2):
            await asyncio.to_thread(reaction.publish, True, b"JPEG")
        await asyncio.to_thread(reaction.publish, False, b"JPEG")
        self.assertEqual(self.component._decision.count, 0)
        for _ in range(2):
            await asyncio.to_thread(reaction.publish, True, b"JPEG")
        await asyncio.to_thread(reaction.break_series)
        self.assertEqual(self.component._decision.count, 0)
        for _ in range(2):
            await asyncio.to_thread(reaction.publish, True, b"JPEG")
        self.objects["print_stats"]["state"] = "paused"
        self.objects["gcode_macro _TREED_OPERATION_STATE"]["phase"] = "paused"
        self.objects["gcode_macro _TREED_CAM_STATE"]["enabled"] = 0
        self.objects["gcode_macro _TREED_CAM_STATE"]["generation"] = 2
        with self.assertRaises(ValueError):
            await asyncio.to_thread(reaction.publish, True, b"JPEG")
        self.assertFalse(await asyncio.to_thread(reaction.prepare))
        self.assertFalse(await asyncio.to_thread(reaction.prepare))
        self.assertEqual(self.component._decision.count, 0)
        self.api.run_gcode.assert_not_awaited()
        self.objects["print_stats"]["state"] = "printing"
        self.objects["gcode_macro _TREED_OPERATION_STATE"]["phase"] = "printing"
        self.objects["gcode_macro _TREED_CAM_STATE"]["enabled"] = 1
        self.assertTrue(await asyncio.to_thread(reaction.prepare))
        await asyncio.to_thread(reaction.publish, None, b"JPEG")
        for _ in range(2):
            await asyncio.to_thread(reaction.publish, True, b"JPEG")
        self.api.run_gcode.assert_not_awaited()
        await asyncio.to_thread(reaction.publish, True, b"JPEG")
        self.api.run_gcode.assert_awaited_once_with("_TREED_DETECTION_CANCEL GENERATION=2")
        self.assertFalse(await asyncio.to_thread(reaction.prepare))
        self.assertEqual(len(list(self.session.glob("detect_*.jpg"))), 3)

    async def test_normal_unknown_or_other_defect_breaks_series(self):
        for critical, defect in ((False, "spaghetti"), (None, "spaghetti"), (True, "crack")):
            self.component._decision = module.DetectionDecision(count=2)
            self.component._last_order = None
            state = await self.result(1, critical, defect=defect)
            self.assertEqual(state["counter"], 0)
            await self.result(2)
            state = await self.result(3)
            self.assertEqual(state["counter"], 2)
        self.api.run_gcode.assert_not_awaited()

    async def test_persistent_switch_resets_series_and_rejects_inflight_results(self):
        self.assertEqual(await self.component._handle_settings(Request()), {"enabled": True})
        await self.result(1)
        await self.result(2)
        for invalid in (None, "false", 0):
            with self.assertRaises(ValueError):
                await self.component._handle_settings(Request(action="POST", enabled=invalid))
        await self.component._handle_settings(Request(action="POST", enabled=False))
        status = await self.component._handle_status(None)
        self.assertFalse(status["enabled"])
        self.assertFalse(status["active"])
        self.assertEqual(status["counter"], 0)
        with self.assertRaises(ValueError):
            await self.result(3)
        restored = module.TreeDDetection(self.config)
        self.assertEqual(await restored._handle_settings(Request()), {"enabled": False})
        await self.component._handle_settings(Request(action="POST", enabled=True))
        with self.assertRaises(ValueError):
            await self.result(3)
        self.session_id = (await self.component._handle_status(None))["session_id"]
        for index in (4, 5):
            await self.result(index)
        self.api.run_gcode.assert_not_awaited()
        await self.result(6)
        self.api.run_gcode.assert_awaited_once()

    async def test_duplicate_and_old_frames_cannot_count(self):
        await self.result(2)
        for index in (2, 1):
            with self.assertRaises(ValueError):
                await self.result(index)
        self.assertEqual(self.component._decision.count, 1)
        self.api.run_gcode.assert_not_awaited()

    async def test_boolean_is_not_coerced_and_bad_result_breaks_series(self):
        await self.result(1)
        await self.result(2)
        with self.assertRaises(ValueError):
            await self.result(3, "false")
        state = await self.result(4)
        self.assertEqual(state["counter"], 1)
        self.api.run_gcode.assert_not_awaited()

    # Блок 3: Границы сессии, пути, паузы и параллельных запросов.
    async def test_foreign_session_and_path_are_rejected(self):
        for overrides in (dict(session_id="old-session"), dict(frame_id="../outside.jpg")):
            with self.assertRaises(ValueError):
                await self.result(1, **overrides)
        self.api.run_gcode.assert_not_awaited()

    async def test_new_generation_resets_counter_even_for_same_directory(self):
        await self.result(1)
        await self.result(2)
        self.objects["gcode_macro _TREED_CAM_STATE"]["generation"] = 2
        with self.assertRaises(ValueError):
            await self.result(3)
        self.session_id = f"2:0:{self.session.name}"
        state = await self.result(4)
        self.assertEqual(state["counter"], 1)
        self.api.run_gcode.assert_not_awaited()

    async def test_pause_end_or_preparation_cannot_cancel(self):
        await self.result(1)
        await self.result(2)
        for state in ("paused", "complete", "standby"):
            self.objects["print_stats"]["state"] = state
            with self.assertRaises(ValueError):
                await self.result(3)
        self.objects["print_stats"]["state"] = "printing"
        self.objects["gcode_macro _TREED_OPERATION_STATE"]["phase"] = "preparing"
        with self.assertRaises(ValueError):
            await self.result(4)
        self.api.run_gcode.assert_not_awaited()

    async def test_concurrent_replies_still_cancel_once(self):
        await self.result(1)
        await self.result(2)
        results = await asyncio.gather(self.result(3), self.result(3), return_exceptions=True)
        self.assertEqual(sum(isinstance(result, ValueError) for result in results), 1)
        self.api.run_gcode.assert_awaited_once()

    async def test_cancel_error_is_visible_without_automatic_retry(self):
        self.api.run_gcode.side_effect = RuntimeError("Klippy unavailable")
        await self.result(1)
        await self.result(2)
        with self.assertLogs(module.LOGGER, level="ERROR"):
            with self.assertRaises(ValueError):
                await self.result(3)
        state = await self.result(4)
        self.assertIsNotNone(state["cancel_error"])
        self.api.run_gcode.assert_awaited_once()

    async def test_registration_and_deploy_wiring(self):
        self.assertEqual(self.endpoints["/server/treed/detection/result"][0], ["POST"])
        status = await self.endpoints["/server/treed/detection/status"][1](None)
        self.assertEqual(status["session_id"], self.session_id)
        core = (ROOT / "moonraker/base/00-core.conf").read_text(encoding="utf-8")
        self.assertIn("[treed_detection]\nframes_root: {{PI_HOME}}/treed/cam/prints", core)
        loader = (ROOT / "loader/steps/moonraker-config.sh").read_text(encoding="utf-8")
        loaded = next(line for line in loader.splitlines() if "for component in " in line)
        self.assertIn("treed_detection", loaded.split())
        camera = (ROOT / "klipper/profiles/treed_v2_corexy_v1/macros_camera.cfg").read_text(encoding="utf-8")
        guard = camera.split("[gcode_macro _TREED_DETECTION_CANCEL]", 1)[1]
        for condition in ("params.GENERATION", "camera.generation", "camera.enabled",
                          'printer.print_stats.state == "printing"',
                          'gcode_macro _TREED_OPERATION_STATE', 'phase == "printing"'):
            self.assertIn(condition, guard)
        self.assertIn("\n    CANCEL_PRINT REASON=spaghetti\n  {% endif %}", guard)
        pause = (ROOT / "klipper/profiles/treed_v2_corexy_v1/macros_pause_resume.cfg").read_text(encoding="utf-8")
        self.assertIn("VARIABLE=generation", pause)


# Блок 4: Единственная адресная команда; success-маркер только после всех проверок.
if __name__ == "__main__":
    outcome = unittest.main(exit=False)
    if not outcome.result.wasSuccessful():
        raise SystemExit(1)
    print("TREED_DETECTION_CHECKS_PASSED")
