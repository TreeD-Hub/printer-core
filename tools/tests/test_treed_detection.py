"""Адресная офлайн-проверка реакции на дефект; без сети и устройства."""

import asyncio
import base64
import importlib.util
import os
from pathlib import Path
import stat
import tempfile
import time
from datetime import datetime, timezone
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location(
    "moonraker.components.treed_detection", ROOT / "moonraker/components/treed_detection.py")
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
        async def run_gcode(command):
            if command.startswith("_TREED_DETECTION_PAUSE"):
                self.objects["print_stats"]["state"] = "paused"
            return "ok"
        self.api.run_gcode.side_effect = run_gcode
        self.endpoints = {}
        server = NS(lookup_component=lambda _: self.api,
                    error=lambda message, code=400: ValueError((code, message)),
                    register_endpoint=lambda path, methods, handler, **kwargs:
                    self.endpoints.update({path: (methods, handler)}))
        values = {"frames_root": str(self.frames), "session_file": str(self.marker)}
        self.config = NS(get_server=lambda: server, get=lambda key, default=None: values.get(key, default))
        self.component = module.TreeDDetection(self.config)
        self.session_id = f"1:0:{self.session.name}"

    async def result(self, index, verdict="spaghetti", **overrides):
        frame_id = f"img_{index}.jpg"
        frame = self.session / frame_id
        if not frame.exists():
            frame.write_bytes(b"\xff\xd8placeholder\xff\xd9")
            timestamp = time.time_ns() + index * 1_000_000
            os.utime(frame, ns=(timestamp, timestamp))
        captured_at = datetime.fromtimestamp(frame.stat().st_mtime, timezone.utc).isoformat().replace("+00:00", "Z")
        args = dict(session_id=self.session_id, frame_id=frame_id,
                    captured_at=captured_at, worker_generation="00000000-0000-4000-8000-000000000001",
                    model_version="spaghetti-v1", verdict=verdict, score=0.99)
        args.update(overrides)
        return await self.component._handle_result(Request(**args))

    # Блок 2: Порог, сброс и подтверждённая пауза.
    async def test_three_consecutive_results_pause_once(self):
        for index in (1, 2):
            state = await self.result(index)
            self.assertEqual(state["counter"], index)
            self.assertEqual(state["recent_frame"]["consecutive_count"], index)
            self.api.run_gcode.assert_not_awaited()
        state = await self.result(3)
        self.assertEqual(state["recent_frame"]["consecutive_count"], 3)
        self.assertEqual(state["pause_state"], "confirmed")
        status = await self.component._handle_status(None)
        self.assertEqual(status["pause_state"], "confirmed")
        self.assertEqual(status["context"]["session_id"], self.session_id)
        self.api.run_gcode.assert_awaited_once_with("_TREED_DETECTION_PAUSE GENERATION=1")

    async def test_autoprinter_hold_is_durable_before_fenced_pause(self):
        hold = AsyncMock(return_value={"state": "active"})
        self.component._autoprinter = NS(hold_for_detection=hold)
        for index in (1, 2, 3):
            state = await self.result(index)
        hold.assert_awaited_once_with("ai_detection", state["context"])
        self.api.run_gcode.assert_awaited_once_with("_TREED_DETECTION_PAUSE GENERATION=1")
        self.assertEqual(state["pause_state"], "confirmed")

    def test_positive_series_accepts_five_second_spacing_but_resets_beyond_limits(self):
        def series(times):
            decision = module.DetectionDecision()
            return [decision.update(True, value, "worker", "model", str(i))
                    for i, value in enumerate(times)]
        self.assertEqual(series((0, 5, 10)), [False, False, True])
        self.assertEqual(series((0, 7, 10)), [False, False, False])
        self.assertEqual(series((0, 5, 13)), [False, False, False])

    async def test_stream_adapter_resets_and_publishes_normalized_results(self):
        # Настоящий адаптер и компонент; заменён только HTTP и внешний Klipper API.
        loop = asyncio.get_running_loop()
        reaction = stream.Reaction("http://127.0.0.1:7125", self.frames, self.marker, 1)

        def request(endpoint, data=None):
            if endpoint.endswith("/reset"):
                operation = self.component._handle_reset(Request(**data))
            elif data is None:
                operation = self.component._handle_status(None)
            else:
                operation = self.component._handle_result(Request(**data))
            return asyncio.run_coroutine_threadsafe(operation, loop).result(timeout=2)

        reaction.request = request

        async def publish(verdict="spaghetti"):
            frame = b"\xff\xd8JPEG\xff\xd9"
            frame_id = await asyncio.to_thread(reaction.save_frame, frame)
            captured_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            result = {
                "context": {"session_id": reaction.session, "frame_id": frame_id,
                            "captured_at": captured_at},
                "captured_at": captured_at,
                "worker_generation": "00000000-0000-4000-8000-000000000001",
                "model_version": "spaghetti-v1", "verdict": verdict, "score": 0.99, "threshold": 0.6,
            }
            return await asyncio.to_thread(reaction.publish, result, frame)

        self.component._decision = module.DetectionDecision(
            count=2, first_at=time.time(), last_at=time.time(),
            worker_generation="00000000-0000-4000-8000-000000000001",
            model_version="spaghetti-v1")
        self.assertTrue(await asyncio.to_thread(reaction.prepare))
        self.assertFalse(reaction.reset_pending)
        self.assertEqual(self.component._decision.count, 0)
        for _ in range(2):
            await publish()
        await publish("normal")
        self.assertEqual(self.component._decision.count, 0)
        for _ in range(2):
            await publish()
        await asyncio.to_thread(reaction.break_series)
        self.assertEqual(self.component._decision.count, 0)
        for _ in range(2):
            await publish()
        self.objects["print_stats"]["state"] = "paused"
        self.objects["gcode_macro _TREED_OPERATION_STATE"]["phase"] = "paused"
        self.objects["gcode_macro _TREED_CAM_STATE"]["enabled"] = 0
        self.objects["gcode_macro _TREED_CAM_STATE"]["generation"] = 2
        with self.assertRaises(ValueError):
            await publish()
        self.assertFalse(await asyncio.to_thread(reaction.prepare))
        self.assertFalse(await asyncio.to_thread(reaction.prepare))
        self.assertEqual(self.component._decision.count, 0)
        self.api.run_gcode.assert_not_awaited()
        self.objects["print_stats"]["state"] = "printing"
        self.objects["gcode_macro _TREED_OPERATION_STATE"]["phase"] = "printing"
        self.objects["gcode_macro _TREED_CAM_STATE"]["enabled"] = 1
        self.assertTrue(await asyncio.to_thread(reaction.prepare))
        for _ in range(2):
            await publish()
        self.api.run_gcode.assert_not_awaited()
        await publish()
        self.api.run_gcode.assert_awaited_once_with("_TREED_DETECTION_PAUSE GENERATION=2")
        self.assertFalse(await asyncio.to_thread(reaction.prepare))
        self.assertEqual(len(list(self.session.glob("detect_*.jpg"))), 3)

    async def test_normal_unknown_or_other_defect_breaks_series(self):
        for verdict in ("normal", "unknown", "unavailable"):
            now = time.time()
            self.component._decision = module.DetectionDecision(
                count=2, first_at=now - .1, last_at=now - .05,
                worker_generation="00000000-0000-4000-8000-000000000001",
                model_version="spaghetti-v1")
            self.component._last_order = None
            state = await self.result(1, verdict)
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
        self.assertEqual(self.component._last_order,
                         ((self.session / "img_2.jpg").stat().st_mtime_ns, "img_2.jpg"))
        with self.assertRaises(ValueError):
            await self.result(2)
        old_frame = self.session / "img_1.jpg"
        timestamp = (self.session / "img_2.jpg").stat().st_mtime_ns - 1_000_000
        old_frame.write_bytes(b"\xff\xd8placeholder\xff\xd9")
        os.utime(old_frame, ns=(timestamp, timestamp))
        with self.assertRaises(ValueError):
            await self.result(1)
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

    async def test_preview_reuses_print_frame_without_switching_model(self):
        state = await self.result(1, verdict="normal", score=0.27, threshold=0.6)
        payload = {**state["recent_frame"], "jpeg_base64": "/9j/2Q=="}
        self.component._preview_vision.infer = AsyncMock()
        with patch.object(self.component, "_handle_frame", AsyncMock(return_value=payload)):
            preview = await self.component._handle_preview(None)
        self.assertEqual("spaghetti", preview["task"])
        self.assertEqual((0.27, 0.6, []), (preview["score"], preview["threshold"], preview["boxes"]))
        self.assertEqual(preview["consecutive_count"], 0)
        self.component._preview_vision.infer.assert_not_awaited()
        self.api.run_gcode.assert_not_awaited()

    async def test_idle_preview_pairs_real_boxes_with_same_camera_frame(self):
        self.objects["print_stats"]["state"] = "standby"
        self.objects["gcode_macro _TREED_OPERATION_STATE"]["phase"] = "idle"
        jpeg = b"\xff\xd8preview\xff\xd9"
        self.component._preview_vision.infer = AsyncMock(return_value={
            "verdict": "occupied", "score": 0.87, "threshold": 0.52,
            "boxes": [[10, 20, 40, 60]], "model_version": "e0", "reason": None})
        with patch.object(module, "_capture_cancellable", AsyncMock(return_value=jpeg)):
            preview = await self.component._handle_preview(None)
        self.assertEqual(jpeg, base64.b64decode(preview["jpeg_base64"]))
        self.assertEqual([[10, 20, 40, 60]], preview["boxes"])
        self.assertEqual("bed", preview["task"])
        self.assertEqual(jpeg, self.component._preview_vision.infer.call_args.args[2])
        self.assertEqual(preview["captured_at"], self.component._preview_vision.infer.call_args.args[1]["captured_at"])
        self.assertIsNone(self.component._recent_frame)
        self.api.run_gcode.assert_not_awaited()

    async def test_preview_disabled_ai_and_start_during_capture_never_infer(self):
        self.objects["print_stats"]["state"] = "standby"
        self.objects["gcode_macro _TREED_OPERATION_STATE"]["phase"] = "idle"
        self.component._preview_vision.infer = AsyncMock()
        self.component._enabled = False
        with patch.object(module, "_capture_cancellable", AsyncMock(return_value=b"\xff\xd8\xff\xd9")):
            preview = await self.component._handle_preview(None)
        self.assertEqual("ai_disabled", preview["reason"])
        self.component._enabled = True

        async def capture(_parts):
            self.objects["print_stats"]["state"] = "printing"
            self.objects["gcode_macro _TREED_OPERATION_STATE"]["phase"] = "printing"
            return b"\xff\xd8\xff\xd9"

        with patch.object(module, "_capture_cancellable", capture):
            preview = await self.component._handle_preview(None)
        self.assertEqual("awaiting_detection", preview["reason"])
        self.component._preview_vision.infer.assert_not_awaited()

    async def test_preview_rejects_invalid_boxes_and_surfaces_camera_failure(self):
        self.objects["print_stats"]["state"] = "standby"
        self.objects["gcode_macro _TREED_OPERATION_STATE"]["phase"] = "idle"
        self.component._preview_vision.infer = AsyncMock(return_value={
            "verdict": "occupied", "score": 0.9, "threshold": 0.5,
            "boxes": [[0, 0, float("nan"), 10]], "model_version": "e0", "reason": None})
        with patch.object(module, "_capture_cancellable", AsyncMock(return_value=b"\xff\xd8\xff\xd9")):
            preview = await self.component._handle_preview(None)
        self.assertEqual("vision_unavailable", preview["reason"])
        self.assertEqual([], preview["boxes"])
        with patch.object(module, "_capture_cancellable", AsyncMock(side_effect=OSError("camera"))):
            with self.assertRaisesRegex(ValueError, "Camera snapshot is unavailable"):
                await self.component._handle_preview(None)

    async def test_preview_discards_inference_after_ai_switches_off(self):
        self.objects["print_stats"]["state"] = "standby"
        self.objects["gcode_macro _TREED_OPERATION_STATE"]["phase"] = "idle"

        async def infer(*_args):
            await self.component._handle_settings(Request(action="POST", enabled=False))
            return {"verdict": "occupied", "boxes": [[10, 20, 40, 60]],
                    "score": 0.9, "threshold": 0.5, "model_version": "e0", "reason": None}

        self.component._preview_vision.infer = infer
        with patch.object(module, "_capture_cancellable", AsyncMock(return_value=b"\xff\xd8\xff\xd9")):
            preview = await self.component._handle_preview(None)
        self.assertEqual("ai_disabled", preview["reason"])
        self.assertEqual([], preview["boxes"])
        self.assertIsNone(preview["score"])

    async def test_frame_endpoint_returns_only_bounded_recent_frame(self):
        status = await self.result(1)
        request = Request(session_id=self.session_id, frame_id="img_1.jpg")
        data = (self.session / "img_1.jpg").read_bytes()
        with patch.object(module.os, "open", side_effect=[41, 42]), \
                patch.object(module.os, "fstat", return_value=NS(st_mode=stat.S_IFREG, st_size=len(data))), \
                patch.object(module.os, "read", side_effect=[data, b""]), \
                patch.object(module.os, "close"):
            payload = await self.component._handle_frame(request)
        self.assertEqual(payload["frame_id"], status["recent_frame"]["frame_id"])
        self.assertEqual(base64.b64decode(payload["jpeg_base64"]), data)
        with patch.object(module.os, "open", side_effect=[41, 42]), \
                patch.object(module.os, "fstat", return_value=NS(st_mode=stat.S_IFREG,
                                                                st_size=8 * 1024 * 1024 + 1)), \
                patch.object(module.os, "read") as read, patch.object(module.os, "close"):
            with self.assertRaisesRegex(ValueError, "Detection frame is too large"):
                await self.component._handle_frame(request)
            read.assert_not_called()

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

    async def test_pause_error_is_visible_after_bounded_retries(self):
        self.api.run_gcode.side_effect = RuntimeError("Klippy unavailable")
        await self.result(1)
        await self.result(2)
        with self.assertLogs(module.LOGGER, level="ERROR"):
            state = await self.result(3)
        state = await self.result(4)
        self.assertIsNotNone(state["pause_error"])
        self.assertEqual(state["pause_state"], "failed")
        self.api.run_gcode.assert_awaited()

    async def test_registration_and_deploy_wiring(self):
        self.assertEqual(self.endpoints["/server/treed/detection/result"][0], ["POST"])
        status = await self.endpoints["/server/treed/detection/status"][1](None)
        self.assertEqual(status["session_id"], self.session_id)
        core = (ROOT / "moonraker/base/00-core.conf").read_text(encoding="utf-8")
        self.assertIn("[treed_detection]\nframes_root: {{PI_HOME}}/treed/cam/prints", core)
        loader = (ROOT / "loader/steps/moonraker-config.sh").read_text(encoding="utf-8")
        self.assertIn("treed_detection", loader)
        camera = (ROOT / "klipper/profiles/treed_v2_corexy_v1/macros_camera.cfg").read_text(encoding="utf-8")
        guard = camera.split("[gcode_macro _TREED_DETECTION_PAUSE]", 1)[1]
        for condition in ("params.GENERATION", "camera.generation", "camera.enabled",
                          'printer.print_stats.state == "printing"',
                          'gcode_macro _TREED_OPERATION_STATE', 'phase == "printing"'):
            self.assertIn(condition, guard)
        self.assertIn("PAUSE REASON=ai_detection", guard)
        pause = (ROOT / "klipper/profiles/treed_v2_corexy_v1/macros_pause_resume.cfg").read_text(encoding="utf-8")
        self.assertIn("VARIABLE=generation", pause)

    def test_pause_is_observable_before_risky_preparation_fails(self):
        source = (ROOT / "klipper/profiles/treed_v2_corexy_v1/macros_pause_resume.cfg").read_text(encoding="utf-8")
        macro = source.split("[gcode_macro PAUSE]", 1)[1].split("[gcode_macro RESUME]", 1)[0]
        commands = [line.strip() for line in macro.splitlines()
                    if line.strip() and not line.lstrip().startswith("#")]

        def command_index(prefix):
            return next(index for index, command in enumerate(commands)
                        if command.startswith(prefix))

        pause_base = command_index("PAUSE_BASE")
        active = command_index("SET_GCODE_VARIABLE MACRO=_TREED_PAUSE_STATE VARIABLE=is_active VALUE=1")
        timeout = command_index("SET_IDLE_TIMEOUT TIMEOUT=43200")
        preparation = command_index("_TREED_PAUSE_PREP_STATE")
        self.assertLess(pause_base, active)
        self.assertLess(timeout, preparation)

        observed = {"paused": False, "active": False, "idle_timeout": None}
        for command in commands[pause_base:preparation]:
            if command == "PAUSE_BASE":
                observed["paused"] = True
            elif command.startswith("SET_GCODE_VARIABLE MACRO=_TREED_PAUSE_STATE VARIABLE=is_active VALUE=1"):
                observed["active"] = True
            elif command.startswith("SET_IDLE_TIMEOUT TIMEOUT="):
                observed["idle_timeout"] = int(command.rsplit("=", 1)[1])

        # Подготовка (включая проверку Z/homing) завершается ошибкой после физической паузы.
        with self.assertRaises(RuntimeError):
            if command_index("_TREED_PAUSE_PREP_STATE") > pause_base:
                raise RuntimeError("simulated preparation failure")
        self.assertEqual(observed, {"paused": True, "active": True, "idle_timeout": 43200})

        resume = source.split("[gcode_macro RESUME]", 1)[1].split("\n[", 1)[0]
        self.assertLess(resume.index("action_raise_error"), resume.rindex("RESUME_BASE"))


# Блок 4: Единственная адресная команда; success-маркер только после всех проверок.
if __name__ == "__main__":
    outcome = unittest.main(exit=False)
    if not outcome.result.wasSuccessful():
        raise SystemExit(1)
    print("TREED_DETECTION_CHECKS_PASSED")
