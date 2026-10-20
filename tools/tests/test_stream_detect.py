"""Камера и адаптер локального vision IPC без сети и ML imports."""

import importlib.util
import io
from pathlib import Path
import tempfile
import threading
import unittest
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

    @unittest.skipUnless(hasattr(module.socket, "AF_UNIX"), "Unix socket runs on target Linux")
    def test_local_vision_echoes_context_and_no_remote_config(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            path = Path(temp) / "vision.sock"
            listener = module.socket.socket(module.socket.AF_UNIX, module.socket.SOCK_STREAM)
            listener.bind(str(path))
            listener.listen(1)
            def reply():
                conn, _ = listener.accept()
                with conn:
                    request = module.json.loads(conn.recv(4096).split(b"\n", 1)[0])
                    response = {"protocol": 1, "request_id": request["request_id"],
                                "task": "spaghetti", "context": request["context"],
                                "worker_generation": "76867cc1-17e4-4cc3-a956-09395f2ba871",
                                "model_version": "spaghetti_presence_v12",
                                "verdict": "normal", "score": .1, "threshold": .3,
                                "boxes": [], "reason": None, "processing_ms": 12.0}
                    conn.sendall(module.json.dumps(response).encode() + b"\n")
            thread = threading.Thread(target=reply)
            thread.start()
            context = {"session_id": "s", "frame_id": "1", "captured_at": "2026-10-20T12:00:00.000Z"}
            result = module.LocalVision(str(path)).send(1, b"\xff\xd8x\xff\xd9", context)
            thread.join()
            listener.close()
            self.assertEqual(result["context"], context)
            self.assertEqual(result["verdict"], "normal")

    def test_reaction_rejects_remote_moonraker(self):
        with self.assertRaises(ValueError):
            module.Reaction("http://192.168.0.197", ROOT, ROOT / "marker", 1)

    @unittest.skipUnless(hasattr(module.socket, "AF_UNIX"), "Unix socket runs on target Linux")
    def test_local_vision_accepts_only_complete_unavailable_contract(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            path = Path(temp) / "vision.sock"
            listener = module.socket.socket(module.socket.AF_UNIX, module.socket.SOCK_STREAM)
            listener.bind(str(path))
            listener.listen(1)
            def reply():
                conn, _ = listener.accept()
                with conn:
                    request = module.json.loads(conn.recv(4096).split(b"\n", 1)[0])
                    response = {"protocol": 1, "request_id": request["request_id"],
                                "task": "spaghetti", "context": request["context"],
                                "worker_generation": "76867cc1-17e4-4cc3-a956-09395f2ba871",
                                "model_version": "spaghetti_presence_v12", "verdict": "unavailable",
                                "score": None, "threshold": .3, "boxes": [], "reason": "warming",
                                "processing_ms": 0.0}
                    conn.sendall(module.json.dumps(response).encode() + b"\n")
            thread = threading.Thread(target=reply)
            thread.start()
            with self.assertRaises(RuntimeError):
                module.LocalVision(str(path)).send(1, b"\xff\xd8x\xff\xd9")
            thread.join()
            listener.close()

    def test_reaction_resets_series_after_session_change(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            root = Path(temp)
            directory = root / "part"
            directory.mkdir()
            marker = root / "marker"
            marker.write_text(str(directory), encoding="utf-8")
            reaction = module.Reaction("http://localhost:7125", root, marker, 1)
            status = {"active": True, "session_id": "print:part", "cancel_requested": False}
            reset = {**status, "counter": 0}
            reaction.request = Mock(side_effect=[status, reset])
            self.assertTrue(reaction.prepare())
            self.assertEqual(reaction.request.call_args_list[1].args,
                             ("/server/treed/detection/reset", {"session_id": "print:part"}))
            self.assertFalse(reaction.reset_pending)

    def test_owned_frame_retention_is_global_and_never_deletes_snapshots(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            root = Path(temp)
            directory = root / "active"
            directory.mkdir()
            archived = root / "previous-session"
            archived.mkdir()
            marker = root / "marker"
            marker.write_text(str(directory), encoding="utf-8")
            reaction = module.Reaction("http://localhost:7125", root, marker, 1)
            older = archived / ("detect_" + "1" * 32 + "_000000000001.jpg")
            newer = directory / ("detect_" + "2" * 32 + "_000000000001.jpg")
            snapshot = directory / "snapshot_001.jpg"
            older.write_bytes(b"12345678")
            newer.write_bytes(b"abcdefgh")
            snapshot.write_bytes(b"user snapshot")
            outside = root / "outside.jpg"
            outside.write_bytes(b"outside user data")
            linked = directory / ("detect_" + "3" * 32 + "_000000000001.jpg")
            try:
                linked.symlink_to(outside)
            except OSError:
                linked = None
            module.os.utime(older, ns=(1_000_000_000, 1_000_000_000))
            original_limit = module.MAX_OWNED_FRAME_BYTES
            try:
                module.MAX_OWNED_FRAME_BYTES = 13
                reaction._prune_owned_frames(incoming=5)
            finally:
                module.MAX_OWNED_FRAME_BYTES = original_limit
            self.assertFalse(older.exists())
            self.assertTrue(newer.exists())
            self.assertEqual(snapshot.read_bytes(), b"user snapshot")
            self.assertEqual(outside.read_bytes(), b"outside user data")
            if linked is not None:
                self.assertTrue(linked.is_symlink())


if __name__ == "__main__":
    unittest.main()
