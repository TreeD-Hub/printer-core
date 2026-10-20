#!/usr/bin/env python3
"""Ограниченный локальный IPC для последовательного CPU-инференса TreeD."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
import importlib.metadata
import math
import multiprocessing
import os
from pathlib import Path
import socket
import stat
import threading
import time
import uuid

MAX_JPEG = 8 * 1024 * 1024
MAX_JSON = 12 * 1024 * 1024
MAX_RESPONSE = 64 * 1024
MAX_CONTEXT = 8 * 1024
SOCKET = Path.home() / "treed/vision/run/vision.sock"
MODELS = Path.home() / "treed/vision/models"
TIMEOUT = 6.0
BED_TIMEOUT = 25.0
STARTUP_TIMEOUT = 60.0
MANIFEST = json.loads(Path(__file__).with_name("models.json").read_text(encoding="utf-8"))
if MANIFEST.get("schema_version") != 1:
    raise RuntimeError("invalid_model_manifest")
SPAGHETTI = MANIFEST["models"]["spaghetti"]
BED = MANIFEST["models"]["bed"]
_loaded_task = None
_loaded_model = None
_spaghetti_transform = None
_torch_threads_configured = False


# Блок 1: Сетевая граница процесса запрещена даже внутри ML worker.
def _disable_network():
    blocked = ("connect", "connect_ex", "sendto", "create_connection", "getaddrinfo",
               "gethostbyname", "gethostbyname_ex", "gethostbyaddr")
    originals = {name: getattr(socket.socket, name, None) if hasattr(socket.socket, name)
                 else getattr(socket, name, None) for name in blocked}
    def deny(*_args, **_kwargs):
        raise RuntimeError("network_disabled")
    for name in blocked:
        target = socket.socket if hasattr(socket.socket, name) else socket
        setattr(target, name, deny)
    return originals


def _restore_network(originals):
    for name, fn in originals.items():
        target = socket.socket if hasattr(socket.socket, name) else socket
        if fn is not None:
            setattr(target, name, fn)


# Блок 2: Проверка файлов модели по descriptor закрывает symlink/TOCTOU замену.
def _open_verified_model(path, metadata):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size != metadata["size"]:
            raise ValueError("invalid_model")
        digest = __import__("hashlib").sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        if digest.hexdigest() != metadata["sha256"] or not Path("/proc/self/fd").is_dir():
            raise ValueError("invalid_model")
        return descriptor, f"/proc/self/fd/{descriptor}"
    except BaseException:
        if "descriptor" in locals():
            os.close(descriptor)
        raise


def _line(conn, limit):
    data = bytearray()
    while len(data) <= limit:
        chunk = conn.recv(min(65536, limit + 1 - len(data)))
        if not chunk:
            break
        if b"\n" in chunk:
            head, tail = chunk.split(b"\n", 1)
            data.extend(head)
            if tail:
                raise ValueError("protocol")
            return bytes(data)
        data.extend(chunk)
    if len(data) > limit:
        raise ValueError("too_large")
    raise ValueError("protocol")


# Блок 3: Изолированная CPU-загрузка и предметный контракт двух моделей.
def _infer_worker(task, model_path, jpeg):
    global _loaded_task, _loaded_model, _spaghetti_transform
    # Imports remain inside the worker so unavailable models do not break IPC startup.
    import io
    from PIL import Image, ImageOps
    import torch
    if task == "spaghetti":
        from torchvision import models, transforms
        if _loaded_task != task:
            _loaded_model = None
            descriptor, verified_path = _open_verified_model(model_path, SPAGHETTI)
            try:
                checkpoint = torch.load(verified_path, map_location="cpu", weights_only=True)
            finally:
                os.close(descriptor)
            if (checkpoint.get("architecture") != "resnet18" or checkpoint.get("input_size") != 384
                    or checkpoint.get("positive_label") != 1
                    or checkpoint.get("class_names") != ["норма", "спагетти"]):
                raise ValueError("invalid_model")
            _loaded_model = models.resnet18(weights=None)
            from torch import nn
            _loaded_model.fc = nn.Linear(_loaded_model.fc.in_features, 1)
            _loaded_model.load_state_dict(checkpoint["model_state"], strict=True)
            _loaded_model.eval()
            _spaghetti_transform = transforms.Compose([transforms.ToTensor(), transforms.Normalize(
                [.485, .456, .406], [.229, .224, .225])])
            _loaded_task = task
        with Image.open(io.BytesIO(jpeg)) as image:
            if image.format != "JPEG" or image.width * image.height > 16_000_000:
                raise ValueError("invalid_input")
            image.load()
            frame = ImageOps.pad(ImageOps.exif_transpose(image).convert("RGB"), (384, 384),
                                 Image.Resampling.BILINEAR, color=(124, 116, 104))
        tensor = _spaghetti_transform(frame).unsqueeze(0)
        with torch.inference_mode():
            score = float(_loaded_model(tensor).flatten()[0].sigmoid())
        return {"verdict": "spaghetti" if score >= SPAGHETTI["threshold"] else "normal", "score": score, "boxes": []}
    # Bed model details and validation are pinned to the existing E0 contract.
    from PIL import Image
    from rfdetr import RFDETRSmall
    if _loaded_task != task:
        _loaded_model = None
        descriptor, verified_path = _open_verified_model(model_path, BED)
        try:
            _loaded_model = RFDETRSmall(pretrain_weights=verified_path, num_classes=1, resolution=512,
                                        device="cpu", trust_checkpoint=False)
        finally:
            os.close(descriptor)
        _validate_bed_class_names(getattr(_loaded_model, "class_names", None))
        if BED["positive_class"] != 0:
            _loaded_model = None
            raise ValueError("invalid_model")
        _loaded_task = task
    with Image.open(io.BytesIO(jpeg)) as image:
        if image.format != "JPEG" or not 1 <= image.width <= 8192 or not 1 <= image.height <= 8192 or image.width * image.height > 16_000_000:
            raise ValueError("invalid_input")
        image.load()
        image = image.convert("RGB")
        width, height = image.size
        with torch.inference_mode():
            prediction = _loaded_model.predict(image, threshold=BED["threshold"],
                                       include_source_image=False).with_nms(threshold=BED["nms"])
    class_ids = getattr(prediction, "class_id", None)
    confidences = getattr(prediction, "confidence", None)
    predicted_boxes = getattr(prediction, "xyxy", None)
    if class_ids is None or confidences is None or predicted_boxes is None:
        raise ValueError("invalid_prediction")
    if len(class_ids) != len(confidences) or len(class_ids) != len(predicted_boxes) or len(class_ids) > 10000:
        raise ValueError("invalid_prediction")
    boxes, scores = [], []
    for class_id, confidence, box in zip(class_ids, confidences, predicted_boxes):
        score = float(confidence)
        if isinstance(class_id, bool) or int(class_id) != BED["positive_class"] or not math.isfinite(score) or not BED["threshold"] <= score <= 1:
            raise ValueError("invalid_prediction")
        if len(box) != 4:
            raise ValueError("invalid_prediction")
        x1, y1, x2, y2 = map(float, box)
        if not all(math.isfinite(value) for value in (x1, y1, x2, y2)) or not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
            raise ValueError("invalid_prediction")
        boxes.append([x1, y1, x2, y2])
        scores.append(score)
    return {"verdict": "occupied" if boxes else "unknown", "score": max(scores, default=None), "boxes": boxes}


def _worker(task, model_path, jpeg):
    global _torch_threads_configured
    originals = _disable_network()
    try:
        for package, version in (("torch", "2.11.0"), ("torchvision", "0.26.0"),
                                 ("rfdetr", "1.11.0"), ("Pillow", "12.3.0")):
            if importlib.metadata.version(package).split("+", 1)[0] != version:
                raise RuntimeError("runtime_unavailable")
        import torch
        if not _torch_threads_configured:
            torch.set_num_threads(2)
            torch.set_num_interop_threads(1)
            _torch_threads_configured = True
        return _infer_worker(task, model_path, jpeg)
    finally:
        _restore_network(originals)


def _validate_bed_class_names(class_names):
    expected = BED.get("checkpoint_class_names")
    if not isinstance(expected, list) or not expected or class_names != expected:
        raise ValueError("invalid_model")


def _worker_loop(pipe):
    while True:
        item = pipe.recv()
        if item is None:
            return
        try:
            pipe.send((True, _worker(*item)))
        except BaseException:
            pipe.send((False, "inference_failed"))


def unavailable_response(request, vision, error):
    task = request.get("task")
    task = task if task in {"spaghetti", "bed"} else "spaghetti"
    model = SPAGHETTI if task == "spaghetti" else BED
    return {"protocol": 1, "request_id": request.get("request_id"), "task": task,
            "context": request.get("context"), "worker_generation": vision.generation or str(uuid.uuid4()),
            "model_version": model["version"], "verdict": "unavailable", "score": None,
            "threshold": model["threshold"], "boxes": [],
            "reason": str(error) if isinstance(error, RuntimeError) else "inference_unavailable",
            "processing_ms": 0.0}


# Блок 4: Один worker с отдельным startup watchdog и bounded terminate/kill/reap.
class Vision:
    def __init__(self, infer=None):
        self.infer = infer
        self.task = None
        self.generation = None
        self.busy = threading.Lock()
        self.process = None
        self.pipe = None
        self.worker_ready = False
        self.worker_started = None
        self.unavailable_task = None
        self.watchdog_stop = threading.Event()
        self.watchdog = threading.Thread(target=self._watch_startup, daemon=True)
        self.watchdog.start()

    def _watch_startup(self):
        # Ограничивает зависший import/model load, даже если запросов больше нет.
        while not self.watchdog_stop.wait(1.0):
            if self.worker_started is None or self.worker_ready:
                continue
            if time.monotonic() - self.worker_started <= STARTUP_TIMEOUT:
                continue
            if not self.busy.acquire(blocking=False):
                continue
            try:
                if self.worker_started is not None and not self.worker_ready and \
                        time.monotonic() - self.worker_started > STARTUP_TIMEOUT:
                    task = self.task
                    self._stop_worker()
                    self.unavailable_task = task
            except RuntimeError:
                # Невозможность reap означает fail-stop: этот процесс не создаёт новый worker.
                self.unavailable_task = self.task
            finally:
                self.busy.release()

    def _stop_worker(self):
        if self.process is not None:
            if self.process.is_alive():
                self.process.terminate()
            self.process.join(timeout=2)
            if self.process.is_alive():
                self.process.kill()
                self.process.join(timeout=2)
            if self.process.is_alive():
                raise RuntimeError("worker_not_reaped")
        if self.pipe is not None:
            self.pipe.close()
        self.process = self.pipe = None
        self.worker_ready = False
        self.worker_started = None

    def _run_worker(self, task, model, frame):
        if self.unavailable_task == task:
            raise RuntimeError("runtime_unavailable")
        started = False
        if self.process is None or not self.process.is_alive():
            self._stop_worker()
            # Не наследует watchdog thread и сетевые monkeypatch из родителя.
            ctx = multiprocessing.get_context("spawn")
            parent_pipe, child_pipe = ctx.Pipe()
            self.process = ctx.Process(target=_worker_loop, args=(child_pipe,), daemon=True)
            self.process.start()
            child_pipe.close()
            self.pipe = parent_pipe
            self.generation = str(uuid.uuid4())
            self.worker_started = time.monotonic()
            started = True
        elif not self.worker_ready:
            if time.monotonic() - self.worker_started > STARTUP_TIMEOUT:
                self._stop_worker()
                self.unavailable_task = task
                raise RuntimeError("startup_timeout")
            if not self.pipe.poll(0):
                raise RuntimeError("warming")
            try:
                ok, _discarded_startup_result = self.pipe.recv()
            except EOFError:
                self._stop_worker()
                raise RuntimeError("worker_crashed") from None
            if not ok:
                self._stop_worker()
                self.unavailable_task = task
                raise RuntimeError("runtime_unavailable")
            self.worker_ready = True
        try:
            self.pipe.send((task, model, frame))
        except (BrokenPipeError, EOFError, OSError):
            self._stop_worker()
            raise RuntimeError("worker_crashed") from None
        if started:
            raise RuntimeError("warming")
        deadline = BED_TIMEOUT if task == "bed" else TIMEOUT
        if not self.pipe.poll(deadline):
            self._stop_worker()
            raise TimeoutError("timeout")
        try:
            ok, result = self.pipe.recv()
        except EOFError:
            self._stop_worker()
            raise RuntimeError("worker_crashed") from None
        if not ok:
            self._stop_worker()
            raise RuntimeError(result)
        return result

    def request(self, request):
        if (not isinstance(request, dict) or type(request.get("protocol")) is not int or request.get("protocol") != 1
                or request.get("task") not in {"spaghetti", "bed"}
                or not isinstance(request.get("context"), dict)
                or not isinstance(request.get("request_id"), str)):
            raise ValueError("invalid_request")
        try:
            if len(json.dumps(request["context"], allow_nan=False).encode()) > MAX_CONTEXT:
                raise ValueError("context_too_large")
        except (TypeError, ValueError):
            raise ValueError("invalid_context") from None
        try:
            uuid.UUID(request["request_id"])
            frame = base64.b64decode(request["jpeg_base64"], validate=True)
        except (KeyError, ValueError, TypeError):
            raise ValueError("invalid_request") from None
        if not 4 <= len(frame) <= MAX_JPEG or not frame.startswith(b"\xff\xd8") or not frame.endswith(b"\xff\xd9"):
            raise ValueError("invalid_request")
        captured_at = request["context"].get("captured_at")
        if request["task"] == "spaghetti" and captured_at is not None:
            try:
                if not isinstance(captured_at, str) or not captured_at.endswith("Z"):
                    raise ValueError
                captured = datetime.fromisoformat(captured_at[:-1] + "+00:00")
                age = (datetime.now(timezone.utc) - captured).total_seconds()
            except (TypeError, ValueError):
                raise ValueError("invalid_capture_time") from None
            if age < -1 or age > TIMEOUT:
                raise ValueError("stale_frame")
        if not self.busy.acquire(blocking=False):
            raise TimeoutError("busy")
        try:
            task = request["task"]
            if task != self.task:
                self._stop_worker()
                self.task = task
                self.unavailable_task = None
                self.generation = str(uuid.uuid4())
            model = MODELS / (SPAGHETTI["filename"] if task == "spaghetti" else BED["filename"])
            started = time.monotonic()
            result = (self.infer(task, str(model), frame) if self.infer is not None
                      else self._run_worker(task, str(model), frame))
            elapsed = (time.monotonic() - started) * 1000
            if task == "bed" and result.get("verdict") not in {"occupied", "unknown"}:
                raise ValueError("invalid_prediction")
            score = result.get("score")
            if score is not None and (type(score) not in {float, int} or not math.isfinite(score) or not 0 <= score <= 1):
                raise ValueError("invalid_prediction")
            response = {"protocol": 1, "request_id": request["request_id"], "task": task,
                        "context": request["context"], "worker_generation": self.generation,
                        "model_version": (SPAGHETTI["version"] if task == "spaghetti" else BED["version"]),
                        "verdict": result["verdict"], "score": result.get("score"),
                        "threshold": SPAGHETTI["threshold"] if task == "spaghetti" else BED["threshold"],
                        "boxes": result.get("boxes", []), "reason": None,
                        "processing_ms": round(elapsed, 1)}
            try:
                encoded_response = json.dumps(response, allow_nan=False, separators=(",", ":")).encode()
            except (TypeError, ValueError):
                raise ValueError("invalid_prediction") from None
            if len(encoded_response) + 1 > MAX_RESPONSE:
                raise ValueError("prediction_too_large")
            return response
        finally:
            self.busy.release()


# Блок 5: Приватный Unix-сокет и ограниченный цикл JSON-запросов.
def serve(path=SOCKET, vision=None):
    vision = vision or Vision()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    path.unlink(missing_ok=True)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    os.chmod(path, 0o600)
    server.listen(1)
    while True:
        conn, _ = server.accept()
        with conn:
            conn.settimeout(TIMEOUT)
            try:
                raw = _line(conn, MAX_JSON)
                request = json.loads(raw)
                response = vision.request(request)
            except Exception as exc:
                try:
                    response = unavailable_response(request, vision, exc)
                except (NameError, AttributeError):
                    response = {"protocol": 1, "error": "unavailable"}
            encoded = json.dumps(response, separators=(",", ":")).encode() + b"\n"
            try:
                conn.sendall(encoded if len(encoded) <= MAX_RESPONSE else b'{"protocol":1,"error":"unavailable"}\n')
            except (BrokenPipeError, ConnectionResetError, OSError):
                # Клиент мог закрыть сокет по своему deadline; это не останавливает accept loop.
                continue


if __name__ == "__main__":
    serve()
