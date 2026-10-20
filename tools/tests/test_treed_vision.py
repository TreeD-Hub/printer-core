"""IPC validation and serialized model selection without ML dependencies."""

import importlib.util
import base64
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("treed_vision_service", ROOT / "runtime-scripts/treed-vision/service.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


# Блок 1: Контракт IPC и lifecycle worker проверяются без импорта ML-библиотек.
class VisionTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.vision = module.Vision(lambda task, path, jpeg: self.calls.append(task) or {
            "verdict": "occupied" if task == "bed" else "normal", "score": .8, "boxes": []})

    def request(self, task="spaghetti", jpeg=b"\xff\xd8x\xff\xd9"):
        return {"protocol": 1, "request_id": "76867cc1-17e4-4cc3-a956-09395f2ba871",
                "task": task, "context": {"frame": 1},
                "jpeg_base64": base64.b64encode(jpeg).decode()}

    def test_echo_bounds_and_model_generation(self):
        first = self.vision.request(self.request())
        self.assertEqual(first["context"], {"frame": 1})
        self.assertEqual(first["verdict"], "normal")
        again = self.vision.request(self.request())
        self.assertEqual(first["worker_generation"], again["worker_generation"])
        switched = self.vision.request(self.request("bed"))
        self.assertNotEqual(again["worker_generation"], switched["worker_generation"])
        self.assertEqual(switched["verdict"], "occupied")
        self.assertEqual(self.calls, ["spaghetti", "spaghetti", "bed"])

    def test_bad_jpeg_and_bed_empty_are_rejected(self):
        with self.assertRaises(ValueError):
            self.vision.request(self.request(jpeg=b"not jpeg"))
        self.vision.infer = lambda *_args: {"verdict": "empty", "score": None, "boxes": []}
        with self.assertRaises(ValueError):
            self.vision.request(self.request("bed"))

    def test_bed_checkpoint_label_is_pinned_without_accepting_other_labels(self):
        expected = json.loads(
            r'["\u0420\u0491\u0420\u00b5\u0421\u201a\u0420\u00b0\u0420\u00bb\u0421\u040a"]'
        )
        self.assertEqual(expected, module.BED["checkpoint_class_names"])
        self.assertEqual(0, module.BED["positive_class"])
        module._validate_bed_class_names(expected)
        for class_names in (["деталь"], ["other"], [""]):
            with self.subTest(class_names=class_names), self.assertRaisesRegex(ValueError, "invalid_model"):
                module._validate_bed_class_names(class_names)

    def test_unavailable_contract_echoes_context_and_has_finite_processing_time(self):
        request = self.request("spaghetti")
        response = module.unavailable_response(request, self.vision, RuntimeError("warming"))
        self.assertEqual(response["request_id"], request["request_id"])
        self.assertEqual(response["context"], request["context"])
        self.assertEqual(response["verdict"], "unavailable")
        self.assertIsNone(response["score"])
        self.assertEqual(response["processing_ms"], 0.0)

    def test_stale_camera_request_is_rejected_before_worker_invocation(self):
        request = self.request()
        request["context"]["captured_at"] = (
            datetime.now(timezone.utc) - timedelta(seconds=module.TIMEOUT + 1)
        ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        with self.assertRaisesRegex(ValueError, "stale_frame"):
            self.vision.request(request)
        self.assertEqual(self.calls, [])

    def test_timeout_kills_and_reaps_worker(self):
        events = []
        class Process:
            alive = True
            def is_alive(self): return self.alive
            def terminate(self): events.append("terminate")
            def join(self, timeout=None): events.append("join")
            def kill(self): events.append("kill"); self.alive = False
        vision = module.Vision()
        vision.process = Process()
        vision.pipe = type("Pipe", (), {"close": lambda _self: events.append("pipe.close")})()
        vision._stop_worker()
        self.assertEqual(events, ["terminate", "join", "kill", "join", "pipe.close"])

    def test_worker_that_survives_bounded_kill_prevents_replacement(self):
        class Process:
            def __init__(self): self.joins = []
            def is_alive(self): return True
            def terminate(self): pass
            def join(self, timeout=None): self.joins.append(timeout)
            def kill(self): pass
        vision = module.Vision()
        process = Process()
        vision.process = process
        with self.assertRaisesRegex(RuntimeError, "worker_not_reaped"):
            vision._stop_worker()
        self.assertEqual(process.joins, [2, 2])
        self.assertIs(vision.process, process)

    def test_bed_has_server_budget_distinct_from_spaghetti_deadline(self):
        class Process:
            def __init__(self): self.alive = True
            def is_alive(self): return self.alive
            def terminate(self): self.alive = False
            def join(self, timeout=None): pass
            def kill(self): pass
        class Pipe:
            def __init__(self): self.deadline = None
            def send(self, _item): pass
            def poll(self, timeout): self.deadline = timeout; return False
            def close(self): pass
        vision = module.Vision()
        pipe = Pipe()
        vision.process, vision.pipe = Process(), pipe
        vision.worker_ready = True
        with self.assertRaises(TimeoutError):
            vision._run_worker("bed", "unused", b"frame")
        self.assertEqual(pipe.deadline, module.BED_TIMEOUT)

    def test_switch_reaps_previous_worker_before_starting_next(self):
        events, processes, pipes = [], [], []
        class Pipe:
            def __init__(self): self.results = []
            def send(self, item): events.append("send:" + item[0])
            def poll(self, _timeout): return bool(self.results)
            def recv(self): return self.results.pop(0)
            def close(self): pass
        class Process:
            def __init__(self, target, args, daemon): self.alive = False
            def start(self): events.append("start"); self.alive = True
            def is_alive(self): return self.alive
            def terminate(self): events.append("terminate"); self.alive = False
            def join(self, timeout=None): events.append("join")
            def kill(self): events.append("kill"); self.alive = False
        class Context:
            Pipe = staticmethod(lambda: (pipes.append(Pipe()) or pipes[-1], Pipe()))
        Context.Process = Process
        vision = module.Vision()
        with patch.object(module.multiprocessing, "get_context", return_value=Context()):
            with self.assertRaisesRegex(RuntimeError, "warming"):
                vision.request(self.request("spaghetti"))
            pipes[-1].results.append((True, {"stale": True}))
            # Give the next same-task request an immediate mocked result.
            pipes[-1].results.append((True, {"verdict": "normal", "score": .1, "boxes": []}))
            vision.request(self.request("spaghetti"))
            with self.assertRaisesRegex(RuntimeError, "warming"):
                vision.request(self.request("bed"))
        first_start = events.index("start")
        next_start = events.index("start", first_start + 1)
        previous_join = events.index("join", events.index("terminate"))
        self.assertLess(events.index("terminate"), previous_join)
        self.assertLess(previous_join, next_start)
        self.assertEqual(events[-2:], ["start", "send:bed"])

    def test_worker_process_uses_spawn_context(self):
        events = []
        class Pipe:
            def send(self, _item): pass
            def close(self): pass
        class Process:
            def __init__(self, target, args, daemon):
                self.target, self.args, self.daemon = target, args, daemon
            def start(self): events.append("start")
            def is_alive(self): return True
        class Context:
            def Pipe(self): return Pipe(), Pipe()
        Context.Process = Process
        vision = module.Vision()
        with patch.object(module.multiprocessing, "get_context", side_effect=lambda method: (
                events.append(method) or Context())) as get_context:
            with self.assertRaisesRegex(RuntimeError, "warming"):
                vision.request(self.request())
        get_context.assert_called_once_with("spawn")
        self.assertEqual(events, ["spawn", "start"])

    def test_worker_pipe_eof_is_reported_and_reaped(self):
        events = []
        class Process:
            def __init__(self): self.alive = True
            def is_alive(self): return self.alive
            def terminate(self): events.append("terminate"); self.alive = False
            def join(self, timeout=None): events.append("join")
            def kill(self): events.append("kill")
        class Pipe:
            def poll(self, _timeout): return True
            def recv(self): raise EOFError
            def close(self): events.append("pipe.close")
        vision = module.Vision()
        vision.process, vision.pipe = Process(), Pipe()
        vision.task, vision.generation = "spaghetti", "generation"
        vision.worker_started = module.time.monotonic()
        with self.assertRaisesRegex(RuntimeError, "worker_crashed"):
            vision._run_worker("spaghetti", "unused", b"frame")
        self.assertIn("terminate", events)
        self.assertIn("pipe.close", events)


if __name__ == "__main__":
    unittest.main()
