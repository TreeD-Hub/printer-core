"""Проверка MJPEG и handshake без камеры, сети и команд принтера."""

import importlib.util
import io
from pathlib import Path
import unittest
import tempfile
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("stream_detect", ROOT / "runtime-scripts/treed-cam/stream_detect.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class StreamTests(unittest.TestCase):
    def test_mjpeg_lengths_and_truncation(self):
        body = b"--cam\r\nContent-Type: image/jpeg\r\nContent-Length: 3\r\n\r\nabc\r\n"
        stream = module.jpeg_frames(io.BytesIO(body * 2))
        self.assertEqual(next(stream), b"abc")
        self.assertEqual(next(stream), b"abc")
        with self.assertRaises(EOFError):
            next(stream)
        for invalid in (body.replace(b"Length: 3", b"Length: 999999999"), body[:body.index(b"abc")] + b"ab"):
            with self.assertRaises((ValueError, EOFError)):
                next(module.jpeg_frames(io.BytesIO(invalid)))

    def test_handshake_and_response_binding(self):
        server = module.Server("http://127.0.0.1:8000", "secret", "session", 1)
        self.addCleanup(server.connection.close)
        ready = {"ready": True, "protocol_version": 1, "session_id": "session",
                 "model_kind": "spaghetti_presence", "model_version": "v5",
                 "threshold": .5, "max_jpeg_bytes": 1024, "frame_endpoint": "/classify"}
        server.post = lambda *args: ready
        self.assertEqual(server.handshake(), ready)
        response = {"protocol_version": 1, "session_id": "session", "frame_id": "1",
                    "model_version": "v5", "threshold": .5, "spaghetti": True, "score": .9}
        server.post = lambda *args: response
        self.assertEqual(server.send(1, b"jpeg"), response)
        for changes in ({"session_id": "old"}, {"frame_id": "2"}, {"score": float("nan")},
                        {"spaghetti": "true"}, {"threshold": .7}, {"model_version": "other"}):
            server.post = lambda *args: {**response, **changes}
            with self.assertRaises(ValueError):
                server.send(1, b"jpeg")
        server.post = lambda *args: {**ready, "protocol_version": 2}
        with self.assertRaises(ValueError):
            server.handshake()

    def test_latest_frame_and_no_stale_frame(self):
        camera = module.Camera("http://127.0.0.1:8080/?action=stream", .01)
        camera.latest = (3, module.time.monotonic(), b"latest")
        self.assertEqual(camera.next(1)[0], 3)
        camera.latest = (4, module.time.monotonic() - 1, b"old")
        with self.assertRaises(TimeoutError):
            camera.next(3)

    def test_reaction_is_local_and_retries_unconfirmed_reset(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            directory = root / "part"
            directory.mkdir()
            marker = root / "marker"
            marker.write_text(str(directory), encoding="utf-8")
            with self.assertRaises(ValueError):
                module.Reaction("http://192.168.0.197", root, marker, 1)
            reaction = module.Reaction("http://localhost:7125", root, marker, 1)
            status = {"active": True, "session_id": "1:part", "cancel_requested": False}
            reaction.request = Mock(return_value=status)
            self.assertTrue(reaction.prepare())
            reaction.frame = b"JPEG"
            reaction.request = Mock(side_effect=OSError("offline"))
            reaction.break_series()
            self.assertTrue(reaction.reset_pending)
            reaction.request = Mock(side_effect=[status, OSError("offline")])
            with self.assertRaises(OSError):
                reaction.prepare()
            self.assertTrue(reaction.reset_pending)
            marker.write_text(str(root.parent), encoding="utf-8")
            reaction.request = Mock(return_value=status)
            with self.assertRaises(ValueError):
                reaction.prepare()


if __name__ == "__main__":
    unittest.main()
