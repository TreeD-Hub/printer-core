"""Офлайн-проверки границ E0 candidate provider и inference runner.
Контур: read-only fake camera/subprocess/model results; без камеры и ML runtime.
"""

from __future__ import annotations

# Блок 1: тест использует только стандартную библиотеку и поддельные границы.
import asyncio
from contextlib import nullcontext
import hashlib
import importlib
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from moonraker.components import treed_autoprinter_bed as bed
from moonraker.components import treed_autoprinter_bed_inference as inference


PRINTER_ID = "11111111-1111-4111-8111-111111111111"
RUN_ID = "22222222-2222-4222-8222-222222222222"
BOOT_ID = "33333333-3333-4333-8333-333333333333"
JPEG = b"\xff\xd8fake-jpeg\xff\xd9"


def result(frame=JPEG, count=1, confidence=0.8, **changes):
    value = {
        "schema_version": 1,
        "model_version": bed.MODEL_VERSION,
        "model_sha256": bed.MODEL_SHA256,
        "frame_sha256": hashlib.sha256(frame).hexdigest(),
        "width": 640,
        "height": 480,
        "detail_count": count,
        "confidence": confidence if count else None,
    }
    value.update(changes)
    return value


def checker(vision_client=None):
    return bed.E0BedChecker(
        PRINTER_ID, "G:/models/e0.pth", "C:/Python/python.exe",
        "http://127.0.0.1:8080/webcam/?action=snapshot",
        vision_client=vision_client,
    )


class _FakeWriter:
    def write(self, _data):
        pass

    async def drain(self):
        pass

    def close(self):
        pass

    async def wait_closed(self):
        pass


class _FakeReader:
    def __init__(self, data=b""):
        self.data = data

    async def read(self, _size):
        data, self.data = self.data, b""
        return data


class _FakeSocket:
    def __init__(self):
        self.closed = False

    def settimeout(self, _value):
        pass

    def shutdown(self, _how):
        self.closed = True

    def close(self):
        self.closed = True


class _FakeProcess:
    def __init__(self, payload=b"{}\n", block=False):
        self.stdin = _FakeWriter()
        self.stdout = _FakeReader(payload)
        self.returncode = None
        self.killed = False
        self.reaped = False
        self.block = block
        self.wait_started = asyncio.Event()
        self.finished = asyncio.Event()

    async def wait(self):
        self.wait_started.set()
        if self.block:
            await self.finished.wait()
        if self.returncode is None:
            self.returncode = 0
        self.reaped = True
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9
        self.finished.set()


class E0BedCheckerTest(unittest.IsolatedAsyncioTestCase):
    def test_printer_name_cannot_replace_uuid(self):
        with self.assertRaisesRegex(ValueError, "invalid_printer_id"):
            bed.E0BedChecker("tr-v2", "", "", "http://127.0.0.1:8080/?action=snapshot")

    async def test_shared_worker_needs_no_legacy_runner_paths(self):
        socket_path = os.path.abspath("vision.sock")
        instance = bed.E0BedChecker(
            PRINTER_ID, "", "", "http://127.0.0.1:8080/?action=snapshot",
            socket_path=socket_path,
        )
        self.assertEqual(instance._vision_client.socket_path, socket_path)
        with self.assertRaises(ValueError):
            bed.TreeDVisionClient("relative.sock")

    async def test_constructor_is_io_free_and_empty_candidate_is_unknown(self):
        with patch.object(bed, "_capture_jpeg", side_effect=AssertionError("camera access")), \
                patch.object(bed.asyncio, "create_subprocess_exec", side_effect=AssertionError("process")):
            instance = checker()
        self.assertFalse(instance.live_qualified)
        with self.assertRaises(AttributeError):
            instance.live_qualified = True
        with patch.object(bed, "_capture_cancellable", new=AsyncMock(return_value=JPEG)), \
                patch.object(instance, "_infer", new=AsyncMock(return_value=result(count=0))):
            proof = await instance.check(RUN_ID, 4, BOOT_ID)
        self.assertEqual(proof["verdict"], "unknown")
        self.assertEqual(proof["reason"], "model_not_qualified")
        self.assertIsNone(proof["confidence"])
        self.assertEqual(proof["bed_epoch"], 4)
        self.assertEqual(proof["after_run_id"], RUN_ID)
        self.assertEqual(proof["agent_boot_id"], BOOT_ID)

    async def test_positive_candidate_is_occupied_and_each_probe_has_new_id(self):
        instance = checker()
        with patch.object(bed, "_capture_cancellable", new=AsyncMock(return_value=JPEG)), \
                patch.object(instance, "_infer", new=AsyncMock(return_value=result())):
            first = await instance.check(RUN_ID, 1, BOOT_ID)
            second = await instance.check(RUN_ID, 1, BOOT_ID)
        self.assertEqual(first["verdict"], "occupied")
        self.assertRegex(first["captured_at"], r"Z$")
        self.assertEqual(first["confidence"], 0.8)
        self.assertNotEqual(first["check_id"], second["check_id"])
        self.assertEqual(instance.status()["last_check"]["state"], "occupied")

    async def test_invalid_result_identity_and_extra_fields_fail_closed(self):
        instance = checker()
        with patch.object(bed, "_capture_cancellable", new=AsyncMock(return_value=JPEG)):
            with patch.object(instance, "_infer", new=AsyncMock(return_value=result(frame=b"other"))):
                wrong_frame = await instance.check(None, 0, BOOT_ID)
            with patch.object(instance, "_infer", new=AsyncMock(return_value=result(extra="leak"))):
                extra = await instance.check(None, 0, BOOT_ID)
        self.assertEqual(wrong_frame["verdict"], "unknown")
        self.assertEqual(extra["verdict"], "unknown")
        self.assertNotIn("extra", extra)

    async def test_capture_and_inference_errors_return_only_safe_status(self):
        instance = checker()
        with patch.object(bed, "_capture_cancellable", new=AsyncMock(side_effect=OSError("secret path"))):
            proof = await instance.check(None, 0, BOOT_ID)
        self.assertEqual(proof["verdict"], "unknown")
        self.assertRegex(proof["captured_at"], r"Z$")
        self.assertNotIn("secret", json.dumps(instance.status()))
        self.assertNotIn("model_path", instance.status())
        self.assertNotIn("snapshot_url", instance.status())

    def test_snapshot_url_accepts_only_literal_http_loopback(self):
        for value in (
            "https://127.0.0.1/frame.jpg", "http://example.test/frame.jpg",
            "http://user:pass@127.0.0.1/frame.jpg", "http://127.0.0.1.evil/frame.jpg",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                bed._parse_snapshot_url(value)
        self.assertEqual(bed._parse_snapshot_url("http://[::1]:8080/snapshot")[1], 8080)

    async def test_bed_uses_shared_vision_client_and_keeps_empty_unknown(self):
        client = types.SimpleNamespace(infer=AsyncMock(return_value={
            "verdict": "normal", "model_version": bed.MODEL_VERSION,
            "boxes": [], "score": 0.9,
        }))
        instance = checker(client)
        result_value = await instance._infer(JPEG, 1)
        self.assertEqual(result_value["detail_count"], 0)
        client.infer.assert_awaited_once_with("bed", {}, JPEG)

    async def test_bed_occupied_requires_box_evidence(self):
        client = types.SimpleNamespace(infer=AsyncMock(return_value={
            "verdict": "occupied", "model_version": bed.MODEL_VERSION,
            "boxes": [], "score": 0.9,
        }))
        with self.assertRaisesRegex(bed._CheckFailure, "invalid_result"):
            await checker(client)._infer(JPEG, 1)


class InferenceBoundaryTest(unittest.IsolatedAsyncioTestCase):
    def test_network_guard_preserves_socket_class_and_blocks_without_connecting(self):
        original_socket = socket.socket
        previous_ssl = sys.modules.pop("ssl", None)
        try:
            with inference._network_guard() as state:
                self.assertIs(socket.socket, original_socket)
                imported_ssl = importlib.import_module("ssl")
                context = imported_ssl.SSLContext(imported_ssl.PROTOCOL_TLS_CLIENT)
                self.assertIsInstance(context, imported_ssl.SSLContext)
                with socket.socket() as sock, self.assertRaises(inference._NetworkAttempt):
                    sock.connect(("127.0.0.1", 1))
                self.assertTrue(state["attempted"])
        finally:
            sys.modules.pop("ssl", None)
            if previous_ssl is not None:
                sys.modules["ssl"] = previous_ssl
        self.assertIs(socket.socket, original_socket)

    def test_capture_rejects_cached_redirect_and_oversized_response(self):
        class Response:
            status = 200

            def __init__(self, headers, body=JPEG):
                self.headers = headers
                self.body = body

            def begin(self):
                pass

            def close(self):
                pass

            def getheader(self, name, default=None):
                return self.headers.get(name, default)

            def read1(self, size):
                value, self.body = self.body[:size], self.body[size:]
                return value

        connections = []

        class Connection:
            def __init__(self, host, port, timeout):
                self.sock = _FakeSocket()
                self.headers = {}
                self.target = None
                connections.append(self)

            def connect(self):
                pass

            def putrequest(self, method, target, **_kwargs):
                self.target = target

            def putheader(self, name, value):
                self.headers[name] = value

            def endheaders(self):
                pass

            def close(self):
                pass

        responses = [
            Response({"Content-Type": "image/jpeg", "Age": "1"}),
            Response({"Content-Type": "text/plain"}),
            Response({"Content-Type": "image/jpeg", "Content-Length": str(bed.MAX_FRAME_BYTES + 1)}),
        ]
        responses.extend(Response({"Content-Type": "image/jpeg"}, JPEG + suffix)
                         for suffix in (b"", b"\x00", b"\x00" * 7,
                                        b"\x00" * 8, b"\x00\x01", b"\x00\xff\xd9\x00\x01"))
        with patch.object(bed.http.client, "HTTPConnection", Connection), \
                patch.object(bed.http.client, "HTTPResponse", side_effect=lambda *_args, **_kwargs: responses.pop(0)):
            for expected in ("stale_frame", "invalid_frame", "frame_too_large"):
                with self.assertRaisesRegex(bed._CheckFailure, expected):
                    bed._capture_jpeg(("127.0.0.1", 8080, "/snapshot"), bed._CaptureControl())
            for padding in (0, 1, 7):
                with self.subTest(padding=padding):
                    self.assertEqual(bed._capture_jpeg(("127.0.0.1", 8080, "/snapshot?x=1"),
                                                       bed._CaptureControl()), JPEG)
            for suffix in ("excessive_zero_padding", "nonzero_trailer", "embedded_eoi"):
                with self.subTest(suffix=suffix), self.assertRaisesRegex(bed._CheckFailure, "invalid_frame"):
                    bed._capture_jpeg(("127.0.0.1", 8080, "/snapshot"), bed._CaptureControl())
        self.assertIn("_treed_capture=", connections[-1].target)
        self.assertEqual(connections[-1].headers["Cache-Control"], "no-cache, no-store, max-age=0")
        self.assertNotIn("Authorization", connections[-1].headers)

    def test_capture_does_not_follow_redirects_or_send_credentials(self):
        class Response:
            status = 302

            def begin(self):
                pass

            def close(self):
                pass

            def getheader(self, _name, default=None):
                return default

        class Connection:
            def __init__(self, *_args, **_kwargs):
                self.sock = _FakeSocket()
                self.headers = {}

            def connect(self):
                pass

            def putrequest(self, method, target, **_kwargs):
                self.target = target

            def putheader(self, name, value):
                self.headers[name] = value

            def endheaders(self):
                pass

            def close(self):
                pass

        with patch.object(bed.http.client, "HTTPConnection", Connection), \
                patch.object(bed.http.client, "HTTPResponse", side_effect=lambda *_args, **_kwargs: Response()):
            with self.assertRaisesRegex(bed._CheckFailure, "camera_http_error"):
                bed._capture_jpeg(("127.0.0.1", 8080, "/snapshot"), bed._CaptureControl())

    async def test_capture_cancellation_closes_blocked_header_read(self):
        started = threading.Event()
        connections = []

        class Connection:
            def __init__(self, *_args, **_kwargs):
                self.sock = _FakeSocket()
                connections.append(self)

            def connect(self):
                pass

            def putrequest(self, *_args, **_kwargs):
                pass

            def putheader(self, *_args, **_kwargs):
                pass

            def endheaders(self):
                pass

        class Response:
            def __init__(self, sock, **_kwargs):
                self.sock = sock

            def begin(self):
                started.set()
                while not self.sock.closed:
                    time.sleep(0.005)
                raise OSError("closed")

            def close(self):
                pass

        with patch.object(bed.http.client, "HTTPConnection", Connection), \
                patch.object(bed.http.client, "HTTPResponse", Response):
            task = asyncio.create_task(bed._capture_cancellable(("127.0.0.1", 8080, "/snapshot")))
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(connections[0].sock.closed)

    async def test_capture_deadline_closes_stalled_header_read(self):
        started = threading.Event()
        connections = []

        class Connection:
            def __init__(self, *_args, **_kwargs):
                self.sock = _FakeSocket()
                connections.append(self)

            def connect(self):
                pass

            def putrequest(self, *_args, **_kwargs):
                pass

            def putheader(self, *_args, **_kwargs):
                pass

            def endheaders(self):
                pass

            def close(self):
                pass

        class Response:
            def __init__(self, sock, **_kwargs):
                self.sock = sock

            def begin(self):
                started.set()
                while not self.sock.closed:
                    time.sleep(0.005)
                raise OSError("deadline")

            def close(self):
                pass

        with patch.object(bed, "MAX_CAPTURE_SECONDS", 0.02), \
                patch.object(bed.http.client, "HTTPConnection", Connection), \
                patch.object(bed.http.client, "HTTPResponse", Response):
            with self.assertRaises(OSError):
                await asyncio.wait_for(
                    bed._capture_cancellable(("127.0.0.1", 8080, "/snapshot")), timeout=0.5,
                )
        self.assertTrue(started.is_set())
        self.assertTrue(connections[0].sock.closed)

    def test_prediction_rejects_bad_class_score_and_geometry(self):
        class Prediction:
            class_id = [0]
            confidence = [0.8]
            xyxy = [[1.0, 2.0, 20.0, 30.0]]

        count, confidence = inference._validate_prediction(Prediction(), 640, 480)
        self.assertEqual((count, confidence), (1, 0.8))
        for values in (([1], [0.8], [[1, 2, 20, 30]]),
                       ([0], [float("nan")], [[1, 2, 20, 30]]),
                       ([0], [0.8], [[1, 2, 641, 30]])):
            Prediction.class_id, Prediction.confidence, Prediction.xyxy = values
            with self.subTest(values=values), self.assertRaises(ValueError):
                inference._validate_prediction(Prediction(), 640, 480)

    def test_fake_runner_checks_pinned_api_and_applies_nms(self):
        calls = {}

        class FakeImage:
            format = "JPEG"
            size = (640, 480)

            def load(self):
                pass

            def convert(self, mode):
                calls["image_mode"] = mode
                return "rgb-frame"

        class ImageAPI:
            @staticmethod
            def open(_stream):
                return FakeImage()

        class Detections:
            def __init__(self, classes, scores, boxes):
                self.class_id, self.confidence, self.xyxy = classes, scores, boxes

            def with_nms(self, threshold):
                calls["nms_threshold"] = threshold
                return Detections([0], [0.8], [[4, 5, 40, 50]])

        class FakeModel:
            class_names = ["деталь"]

            def predict(self, frame, **kwargs):
                calls["frame"] = frame
                calls["predict_kwargs"] = kwargs
                return Detections([0, 0], [0.8, 0.7], [[4, 5, 40, 50], [5, 6, 39, 49]])

        def create_model(**kwargs):
            calls["constructor"] = kwargs
            return FakeModel()

        pil = types.ModuleType("PIL")
        pil.Image = ImageAPI
        torch = types.ModuleType("torch")
        torch.inference_mode = nullcontext
        torchvision = types.ModuleType("torchvision")
        rfdetr = types.ModuleType("rfdetr")
        rfdetr.RFDETRSmall = create_model
        descriptor = os.open(os.devnull, os.O_RDONLY)
        with patch.object(inference, "_open_verified_model", return_value=(descriptor, "/fake/model-fd")), \
                patch.object(inference, "_check_versions"), \
                patch.dict(sys.modules, {
                    "PIL": pil, "PIL.Image": ImageAPI, "torch": torch,
                    "torchvision": torchvision, "rfdetr": rfdetr,
                }):
            output = inference._infer("unused", JPEG)

        self.assertEqual(output["detail_count"], 1)
        self.assertEqual(output["confidence"], 0.8)
        self.assertEqual(calls["image_mode"], "RGB")
        self.assertEqual(calls["frame"], "rgb-frame")
        self.assertEqual(calls["nms_threshold"], inference.MODEL_NMS)
        self.assertEqual(calls["predict_kwargs"], {
            "threshold": inference.MODEL_THRESHOLD, "include_source_image": False,
        })
        self.assertEqual(calls["constructor"], {
            "pretrain_weights": "/fake/model-fd", "num_classes": 1,
            "resolution": 512, "device": "cpu", "trust_checkpoint": False,
        })

    def test_small_fake_artifact_size_and_hash_mismatch_fail_portably(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "model.pth"
            artifact.write_bytes(b"abc")
            with patch.object(inference, "MODEL_SIZE", 4), \
                    patch.object(inference, "MODEL_SHA256", hashlib.sha256(b"abcd").hexdigest()), \
                    patch.object(inference.Path, "is_dir", side_effect=AssertionError("validation reached procfs")):
                with self.assertRaisesRegex(ValueError, "invalid_model"):
                    inference._open_verified_model(str(artifact))
            with patch.object(inference, "MODEL_SIZE", 3), \
                    patch.object(inference, "MODEL_SHA256", hashlib.sha256(b"abd").hexdigest()), \
                    patch.object(inference.Path, "is_dir", side_effect=AssertionError("validation reached procfs")):
                with self.assertRaisesRegex(ValueError, "invalid_model"):
                    inference._open_verified_model(str(artifact))
            with patch.object(inference, "MODEL_SIZE", 3), \
                    patch.object(inference, "MODEL_SHA256", hashlib.sha256(b"abc").hexdigest()), \
                    patch.object(inference.Path, "is_dir", side_effect=AssertionError("validation reached procfs")):
                with self.assertRaisesRegex(AssertionError, "validation reached procfs"):
                    inference._open_verified_model(str(artifact))

    @unittest.skipUnless(Path("/proc/self/fd").is_dir(), "runner needs Linux procfs")
    def test_small_fake_artifact_positive_open_and_symlink_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "model.pth"
            artifact.write_bytes(b"abc")
            with patch.object(inference, "MODEL_SIZE", 3), \
                    patch.object(inference, "MODEL_SHA256", hashlib.sha256(b"abc").hexdigest()):
                descriptor, verified_path = inference._open_verified_model(str(artifact))
                os.close(descriptor)
                self.assertIn("/proc/self/fd/", verified_path)
                artifact.write_bytes(b"abd")
                with self.assertRaisesRegex(ValueError, "invalid_model"):
                    inference._open_verified_model(str(artifact))
                artifact.write_bytes(b"abc")
                link = Path(directory) / "model-link.pth"
                link.symlink_to(artifact)
                with self.assertRaisesRegex(ValueError, "invalid_model"):
                    inference._open_verified_model(str(link))

    def test_dependency_version_mismatch_fails_closed_without_importing_runtime(self):
        def version(package):
            return "2.11.0+cpu" if package == "torch" else inference.PINNED_VERSIONS[package]

        with patch.object(inference.importlib.metadata, "version", side_effect=version):
            inference._check_versions()
        with patch.object(inference.importlib.metadata, "version", side_effect=lambda _package: "0.0.0"):
            with self.assertRaisesRegex(RuntimeError, "runtime_unavailable"):
                inference._check_versions()

    def test_runner_errors_preserve_only_known_safe_codes(self):
        with self.assertRaisesRegex(bed._CheckFailure, "runtime_unavailable"):
            bed._interpret_result({"schema_version": 1, "error": "runtime_unavailable"}, "0" * 64)
        with self.assertRaisesRegex(bed._CheckFailure, "invalid_result"):
            bed._interpret_result({"schema_version": 1, "error": "private path"}, "0" * 64)


class VisionClientTest(unittest.IsolatedAsyncioTestCase):
    class Writer:
        def __init__(self):
            self.payload = b""

        def write(self, payload):
            self.payload = payload

        async def drain(self):
            return None

        def close(self):
            return None

        async def wait_closed(self):
            return None

    async def _infer_with_response(self, changes=None, read_error=None):
        writer = self.Writer()

        class Reader:
            async def readline(_self):
                if read_error:
                    raise read_error
                request = json.loads(writer.payload)
                response = {
                    "protocol": 1, "request_id": request["request_id"],
                    "task": request["task"], "context": request["context"],
                    "worker_generation": "44444444-4444-4444-8444-444444444444",
                    "model_version": bed.MODEL_VERSION, "verdict": "occupied",
                    "score": 0.8, "threshold": 0.5, "boxes": [{"xyxy": [0, 0, 1, 1]}],
                    "reason": None, "processing_ms": 1.5,
                }
                response.update(changes or {})
                return json.dumps(response, allow_nan=True).encode() + b"\n"

        async def open_connection(_path, **_kwargs):
            return Reader(), writer

        client = bed.TreeDVisionClient(os.path.abspath("vision.sock"), timeout=1)
        with patch.object(bed.asyncio, "open_unix_connection", new=open_connection, create=True):
            return await client.infer("bed", {"check_id": "test"}, JPEG)

    async def test_client_accepts_matching_context_and_rejects_nonfinite_numbers(self):
        response = await self._infer_with_response()
        self.assertEqual(response["context"], {"check_id": "test"})
        for invalid in ({"score": float("nan")}, {"threshold": float("inf")},
                        {"processing_ms": float("nan")},
                        {"context": {"check_id": "foreign"}}, {"request_id": "foreign"}):
            with self.subTest(invalid=invalid), self.assertRaises(bed.VisionUnavailable):
                await self._infer_with_response(invalid)

    async def test_client_maps_bounded_line_errors_to_unavailable(self):
        for error in (asyncio.LimitOverrunError("response too long", 1), ValueError("line too long")):
            with self.subTest(error=type(error).__name__), self.assertRaises(bed.VisionUnavailable):
                await self._infer_with_response(read_error=error)


if __name__ == "__main__":
    unittest.main()
