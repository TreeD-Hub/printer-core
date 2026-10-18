"""Одноразовый офлайн-процесс RF-DETR для кандидата E0.

Контур: только чтение модели и кадра; сеть, временные файлы и состояние служб не используются.
"""

from __future__ import annotations

# Блок 1: стандартная библиотека до входа в изолированную ML-зону.
from contextlib import contextmanager, redirect_stdout
import hashlib
import importlib.metadata
import io
import json
import math
import numbers
import os
from pathlib import Path
import socket
import stat
import sys
from typing import Any

try:
    from .treed_autoprinter_bed import (
        MODEL_CLASS, MODEL_SHA256, MODEL_SIZE, MODEL_THRESHOLD, MODEL_VERSION, MODEL_NMS,
    )
except ImportError:
    from treed_autoprinter_bed import (
        MODEL_CLASS, MODEL_SHA256, MODEL_SIZE, MODEL_THRESHOLD, MODEL_VERSION, MODEL_NMS,
    )

MAX_FRAME_BYTES = 8 * 1024 * 1024
MAX_FRAME_PIXELS = 16_000_000
MAX_DIMENSION = 8192
MAX_JSON_BYTES = 4096
PINNED_VERSIONS = {
    "rfdetr": "1.11.0",
    "torch": "2.11.0",
    "torchvision": "0.26.0",
    "Pillow": "12.3.0",
}


class _NetworkAttempt(RuntimeError):
    pass


@contextmanager
def _network_guard():
    """Сохраняет socket.socket классом и блокирует соединения и DNS-запросы."""
    original = {
        "connect": socket.socket.connect,
        "connect_ex": socket.socket.connect_ex,
        "sendto": socket.socket.sendto,
        "create_connection": socket.create_connection,
        "getaddrinfo": socket.getaddrinfo,
        "gethostbyname": socket.gethostbyname,
        "gethostbyname_ex": socket.gethostbyname_ex,
        "gethostbyaddr": socket.gethostbyaddr,
    }
    state = {"attempted": False}

    def denied(*_args, **_kwargs):
        state["attempted"] = True
        raise _NetworkAttempt("network_disabled")

    try:
        socket.socket.connect = denied
        socket.socket.connect_ex = denied
        socket.socket.sendto = denied
        socket.create_connection = denied
        socket.getaddrinfo = denied
        socket.gethostbyname = denied
        socket.gethostbyname_ex = denied
        socket.gethostbyaddr = denied
        yield state
    finally:
        socket.socket.connect = original["connect"]
        socket.socket.connect_ex = original["connect_ex"]
        socket.socket.sendto = original["sendto"]
        socket.create_connection = original["create_connection"]
        socket.getaddrinfo = original["getaddrinfo"]
        socket.gethostbyname = original["gethostbyname"]
        socket.gethostbyname_ex = original["gethostbyname_ex"]
        socket.gethostbyaddr = original["gethostbyaddr"]


def _read_bounded_stdin() -> bytes:
    frame = sys.stdin.buffer.read(MAX_FRAME_BYTES + 1)
    if len(frame) > MAX_FRAME_BYTES:
        raise ValueError("invalid_input")
    return frame


def _validate_frame(frame: bytes, Image) -> tuple[Any, int, int]:
    if len(frame) < 4 or not frame.startswith(b"\xff\xd8") or not frame.endswith(b"\xff\xd9"):
        raise ValueError("invalid_input")
    image = Image.open(io.BytesIO(frame))
    if image.format != "JPEG":
        raise ValueError("invalid_input")
    width, height = image.size
    if (not 1 <= width <= MAX_DIMENSION or not 1 <= height <= MAX_DIMENSION
            or width * height > MAX_FRAME_PIXELS):
        raise ValueError("invalid_input")
    image.load()
    return image.convert("RGB"), width, height


def _open_verified_model(path: str):
    if not os.path.isabs(path):
        raise ValueError("invalid_model")
    candidate = Path(path)
    current = Path(candidate.anchor)
    for part in candidate.parts[1:]:
        current /= part
        try:
            if stat.S_ISLNK(current.lstat().st_mode):
                raise ValueError("invalid_model")
        except OSError:
            raise ValueError("invalid_model") from None
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise ValueError("invalid_model") from None
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != MODEL_SIZE:
            raise ValueError("invalid_model")
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        if digest.hexdigest() != MODEL_SHA256:
            raise ValueError("invalid_model")
        # shortcut: hash-stable descriptor loading needs Linux procfs; use a verified-fd API if runtime moves elsewhere.
        if not Path("/proc/self/fd").is_dir():
            raise ValueError("invalid_model")
        return descriptor, f"/proc/self/fd/{descriptor}"
    except BaseException:
        os.close(descriptor)
        raise


def _check_versions() -> None:
    for package, expected in PINNED_VERSIONS.items():
        installed = importlib.metadata.version(package).split("+", 1)[0]
        if installed != expected:
            raise RuntimeError("runtime_unavailable")


def _validate_prediction(prediction, width: int, height: int) -> tuple[int, float | None]:
    class_ids = getattr(prediction, "class_id", None)
    confidences = getattr(prediction, "confidence", None)
    boxes = getattr(prediction, "xyxy", None)
    if class_ids is None or confidences is None or boxes is None:
        raise ValueError("invalid_prediction")
    count = len(class_ids)
    if count != len(confidences) or count != len(boxes) or count > 10000:
        raise ValueError("invalid_prediction")
    highest = None
    for class_id, confidence, box in zip(class_ids, confidences, boxes):
        if isinstance(class_id, bool) or not isinstance(class_id, numbers.Integral) or class_id != 0:
            raise ValueError("invalid_prediction")
        if isinstance(confidence, bool) or not isinstance(confidence, numbers.Real):
            raise ValueError("invalid_prediction")
        score = float(confidence)
        if not math.isfinite(score) or not MODEL_THRESHOLD <= score <= 1:
            raise ValueError("invalid_prediction")
        if len(box) != 4:
            raise ValueError("invalid_prediction")
        x1, y1, x2, y2 = (float(value) for value in box)
        if (not all(math.isfinite(value) for value in (x1, y1, x2, y2))
                or not 0 <= x1 < x2 <= width or not 0 <= y1 < y2 <= height):
            raise ValueError("invalid_prediction")
        highest = score if highest is None else max(highest, score)
    return count, highest


def _infer(model_path: str, frame: bytes) -> dict[str, Any]:
    descriptor, verified_path = _open_verified_model(model_path)
    try:
        with _network_guard() as network:
            try:
                _check_versions()
                from PIL import Image
                import torch
                import torchvision  # noqa: F401 - version gate is explicit
                from rfdetr import RFDETRSmall

                decoded, width, height = _validate_frame(frame, Image)
                model = RFDETRSmall(
                    pretrain_weights=verified_path,
                    num_classes=1,
                    resolution=512,
                    device="cpu",
                    trust_checkpoint=False,
                )
                names = getattr(model, "class_names", None)
                if names != [MODEL_CLASS]:
                    raise ValueError("invalid_prediction")
                with torch.inference_mode():
                    prediction = model.predict(
                        decoded, threshold=MODEL_THRESHOLD, include_source_image=False,
                    ).with_nms(threshold=MODEL_NMS)
                count, confidence = _validate_prediction(prediction, width, height)
                if network["attempted"]:
                    raise _NetworkAttempt("network_disabled")
            except _NetworkAttempt:
                raise
            except (ImportError, RuntimeError):
                if network["attempted"]:
                    raise _NetworkAttempt("network_disabled") from None
                raise RuntimeError("runtime_unavailable") from None
            except ValueError:
                if network["attempted"]:
                    raise _NetworkAttempt("network_disabled") from None
                raise
            if network["attempted"]:
                raise _NetworkAttempt("network_disabled")
    finally:
        os.close(descriptor)
    return {
        "schema_version": 1,
        "model_version": MODEL_VERSION,
        "model_sha256": MODEL_SHA256,
        "frame_sha256": hashlib.sha256(frame).hexdigest(),
        "width": width,
        "height": height,
        "detail_count": count,
        "confidence": confidence,
    }


def _emit(value: dict[str, Any]) -> None:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_JSON_BYTES:
        encoded = b'{"schema_version":1,"error":"inference_failed"}'
    sys.stdout.buffer.write(encoded + b"\n")
    sys.stdout.buffer.flush()


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        _emit({"schema_version": 1, "error": "invalid_model"})
        return 0
    try:
        frame = _read_bounded_stdin()
        with open(os.devnull, "w", encoding="utf-8") as sink, redirect_stdout(sink):
            result = _infer(args[0], frame)
        _emit(result)
    except ValueError as exc:
        code = str(exc) if str(exc) in {"invalid_input", "invalid_model", "invalid_prediction"} else "inference_failed"
        _emit({"schema_version": 1, "error": code})
    except _NetworkAttempt:
        _emit({"schema_version": 1, "error": "network_disabled"})
    except Exception:
        _emit({"schema_version": 1, "error": "runtime_unavailable"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
