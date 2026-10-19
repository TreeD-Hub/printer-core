"""Провайдер кандидата E0 для определения занятости стола AutoPrinter.

Контур: только чтение; допуск пустого стола этой моделью недоступен.
"""

from __future__ import annotations

# Блок 1: host зависимости ограничены стандартной библиотекой.
import asyncio
from datetime import datetime, timezone
import hashlib
import http.client
import ipaddress
import json
import math
import os
from pathlib import Path
import subprocess
import socket
import threading
import time
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import uuid


MODEL_VERSION = "fdm-detail-e0-20261016-8de7e8aa22e2"
MODEL_SHA256 = "8de7e8aa22e20893a563c0c04d28f0019dcad9ef8d805818df6fd2ec8b10866e"
MODEL_SIZE = 127479855
MODEL_RESOLUTION = 512
MODEL_CLASS = "деталь"
MODEL_THRESHOLD = 0.5191324353218079
MODEL_NMS = 0.8
PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 8 * 1024 * 1024
MAX_RESULT_BYTES = 4096
MAX_CAPTURE_SECONDS = 8.0
MAX_CHECK_SECONDS = 25.0
MAX_FRAME_PIXELS = 16_000_000
INFERENCE_SCRIPT = Path(__file__).with_name("treed_autoprinter_bed_inference.py")


class E0BedChecker:
    """Возвращает только occupied/unknown от неквалифицированной модели E0."""

    allowed_model_versions = frozenset({MODEL_VERSION})

    def __init__(self, printer_id: str, model_path: str, python_path: str,
                 snapshot_url: str) -> None:
        # Блок 2: только проверка конфигурации, без файлов, камеры и процессов.
        if not _is_uuid(printer_id):
            raise ValueError("invalid_printer_id")
        if not isinstance(model_path, str) or not os.path.isabs(model_path):
            raise ValueError("invalid_model_path")
        if not isinstance(python_path, str) or not os.path.isabs(python_path):
            raise ValueError("invalid_python_path")
        self._snapshot_parts = _parse_snapshot_url(snapshot_url)
        self.printer_id = printer_id
        self.model_path = model_path
        self.python_path = python_path
        self.model_version = MODEL_VERSION
        self._lock = asyncio.Lock()
        self._last_status: dict[str, Any] = {
            "state": "unavailable", "reason": "not_checked", "checked_at": None,
        }

    @property
    def live_qualified(self) -> bool:
        return False

    def status(self) -> dict[str, Any]:
        return {
            "model_version": MODEL_VERSION,
            "allowed_model_versions": [MODEL_VERSION],
            "live_qualified": False,
            "last_check": dict(self._last_status),
        }

    async def check(self, after_run_id: str | None, bed_epoch: int,
                    boot_id: str) -> dict[str, Any]:
        # Блок 3: одна свежая сериализованная проба получает собственную идентичность.
        _validate_context(after_run_id, bed_epoch, boot_id)
        async with self._lock:
            check_id = str(uuid.uuid4())
            started = time.monotonic()
            stage = "camera"
            # При ошибке до кадра это время проверки, а не доказательство съёмки.
            captured_at = _utc_now()
            try:
                jpeg = await asyncio.wait_for(
                    _capture_cancellable(self._snapshot_parts),
                    timeout=MAX_CAPTURE_SECONDS,
                )
                captured_at = _utc_now()
                frame_sha256 = hashlib.sha256(jpeg).hexdigest()
                stage = "inference"
                remaining = MAX_CHECK_SECONDS - (time.monotonic() - started)
                if remaining <= 0:
                    raise _CheckFailure("capture_timeout")
                result = await asyncio.wait_for(
                    self._infer(jpeg, remaining), timeout=remaining,
                )
                if time.monotonic() - started > MAX_CHECK_SECONDS:
                    verdict, confidence, reason = "unknown", None, "check_timeout"
                else:
                    verdict, confidence, reason = _interpret_result(result, frame_sha256)
            except asyncio.CancelledError:
                self._record("unavailable", "cancelled")
                raise
            except _CheckFailure as error:
                verdict, confidence, reason = "unknown", None, error.code
            except asyncio.TimeoutError:
                verdict, confidence, reason = "unknown", None, "check_timeout"
            except OSError:
                verdict, confidence, reason = "unknown", None, "camera_error" if stage == "camera" else "inference_error"
            except Exception:
                verdict, confidence, reason = "unknown", None, "inference_error"
            self._record(verdict, reason)
            return {
                "protocol_version": PROTOCOL_VERSION,
                "printer_id": self.printer_id,
                "after_run_id": after_run_id,
                "check_id": check_id,
                "bed_epoch": bed_epoch,
                "agent_boot_id": boot_id,
                "verdict": verdict,
                "model_version": MODEL_VERSION,
                "captured_at": captured_at,
                "confidence": confidence,
                "reason": reason,
            }

    async def _infer(self, jpeg: bytes, timeout: float) -> dict[str, Any]:
        # Блок 4: только штатный соседний runner получает ограниченный JPEG и путь к весам.
        env = _offline_environment()
        process = await asyncio.create_subprocess_exec(
            self.python_path, "-B", "-s", str(INFERENCE_SCRIPT), self.model_path,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=env, limit=MAX_RESULT_BYTES + 1,
        )
        writer = asyncio.create_task(_write_stdin(process, jpeg))
        reader = asyncio.create_task(_read_bounded_stdout(process))
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
            await asyncio.wait_for(asyncio.gather(writer, reader), timeout=max(0.01, timeout))
        except BaseException:
            if process.returncode is None:
                process.kill()
                await process.wait()
            for task in (writer, reader):
                if not task.done():
                    task.cancel()
            await asyncio.gather(writer, reader, return_exceptions=True)
            raise
        if process.returncode != 0:
            raise _CheckFailure("runner_failed")
        try:
            result = json.loads(reader.result())
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise _CheckFailure("invalid_result") from None
        if not isinstance(result, dict):
            raise _CheckFailure("invalid_result")
        return result

    def _record(self, state: str, reason: str | None) -> None:
        self._last_status = {
            "state": state,
            "reason": reason,
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        }


class _CheckFailure(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _validate_context(after_run_id: str | None, bed_epoch: int, boot_id: str) -> None:
    if after_run_id is not None and not _is_uuid(after_run_id):
        raise ValueError("invalid_run_id")
    if isinstance(bed_epoch, bool) or not isinstance(bed_epoch, int) or bed_epoch < 0:
        raise ValueError("invalid_bed_epoch")
    if not _is_uuid(boot_id):
        raise ValueError("invalid_boot_id")


def _is_uuid(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_snapshot_url(value: str) -> tuple[str, int, str]:
    if (not isinstance(value, str) or len(value) > 2048
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError("invalid_snapshot_url")
    try:
        parts = urlsplit(value)
        address = ipaddress.ip_address(parts.hostname or "")
        port = 80 if parts.port is None else parts.port
    except (ValueError, TypeError):
        raise ValueError("invalid_snapshot_url") from None
    if (parts.scheme.lower() != "http" or not address.is_loopback or
            parts.username is not None or parts.password is not None or parts.fragment or
            not 1 <= port <= 65535):
        raise ValueError("invalid_snapshot_url")
    path = parts.path or "/"
    return address.compressed, port, urlunsplit(("", "", path, parts.query, ""))


async def _capture_cancellable(parts: tuple[str, int, str]) -> bytes:
    control = _CaptureControl()
    worker = asyncio.create_task(asyncio.to_thread(_capture_jpeg, parts, control))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        control.abort()
        await asyncio.gather(worker, return_exceptions=True)
        raise


class _CaptureControl:
    def __init__(self) -> None:
        self.cancelled = threading.Event()
        self._lock = threading.Lock()
        self._socket = None

    def attach(self, sock) -> None:
        with self._lock:
            self._socket = sock
        if self.cancelled.is_set():
            self.abort()

    def abort(self) -> None:
        self.cancelled.set()
        with self._lock:
            sock = self._socket
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass


def _capture_jpeg(parts: tuple[str, int, str], control: _CaptureControl) -> bytes:
    host, port, target = parts
    deadline = time.monotonic() + MAX_CAPTURE_SECONDS
    query = parse_qsl(urlsplit(target).query, keep_blank_values=True)
    query.append(("_treed_capture", uuid.uuid4().hex))
    url = urlsplit(target)
    request_target = urlunsplit(("", "", url.path or "/", urlencode(query), ""))
    connection = http.client.HTTPConnection(host, port, timeout=MAX_CAPTURE_SECONDS)
    deadline_timer = threading.Timer(MAX_CAPTURE_SECONDS, control.abort)
    try:
        deadline_timer.start()
        if control.cancelled.is_set():
            raise _CheckFailure("capture_cancelled")
        connection.connect()
        control.attach(connection.sock)
        raw_socket = connection.sock
        raw_socket.settimeout(max(0.01, deadline - time.monotonic()))
        connection.putrequest("GET", request_target, skip_accept_encoding=True)
        connection.putheader("Cache-Control", "no-cache, no-store, max-age=0")
        connection.putheader("Pragma", "no-cache")
        connection.putheader("Accept", "image/jpeg")
        connection.endheaders()
        response = http.client.HTTPResponse(raw_socket, method="GET")
        response.begin()
        if response.status != 200:
            raise _CheckFailure("camera_http_error")
        content_type = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "image/jpeg":
            raise _CheckFailure("invalid_frame")
        age = response.getheader("Age")
        if age is not None and (not age.isdecimal() or int(age) != 0):
            raise _CheckFailure("stale_frame")
        length = response.getheader("Content-Length")
        if length is not None and (not length.isdecimal() or int(length) > MAX_FRAME_BYTES):
            raise _CheckFailure("frame_too_large")
        chunks = bytearray()
        while len(chunks) <= MAX_FRAME_BYTES:
            if time.monotonic() >= deadline:
                raise _CheckFailure("capture_timeout")
            chunk = response.read1(min(65536, MAX_FRAME_BYTES + 1 - len(chunks)))
            if not chunk:
                break
            chunks.extend(chunk)
        jpeg = bytes(chunks)
        if len(jpeg) > MAX_FRAME_BYTES:
            raise _CheckFailure("frame_too_large")
        if len(jpeg) < 4 or not jpeg.startswith(b"\xff\xd8") or not jpeg.endswith(b"\xff\xd9"):
            raise _CheckFailure("invalid_frame")
        return jpeg
    finally:
        deadline_timer.cancel()
        deadline_timer.join()
        if "response" in locals():
            response.close()
        connection.close()


async def _write_stdin(process, jpeg: bytes) -> None:
    process.stdin.write(jpeg)
    await process.stdin.drain()
    process.stdin.close()
    await process.stdin.wait_closed()


async def _read_bounded_stdout(process) -> str:
    chunks = bytearray()
    while True:
        chunk = await process.stdout.read(512)
        if not chunk:
            break
        chunks.extend(chunk)
        if len(chunks) > MAX_RESULT_BYTES:
            raise _CheckFailure("result_too_large")
    return chunks.decode("utf-8")


def _interpret_result(result: dict[str, Any], frame_sha256: str) -> tuple[str, float | None, str | None]:
    if set(result) == {"schema_version", "error"}:
        error = result.get("error")
        if result.get("schema_version") == 1 and error in {
                "invalid_input", "invalid_model", "runtime_unavailable", "inference_failed",
                "network_disabled", "invalid_prediction"}:
            raise _CheckFailure(error)
        raise _CheckFailure("invalid_result")
    expected = {"schema_version", "model_version", "model_sha256", "frame_sha256",
                "width", "height", "detail_count", "confidence"}
    if set(result) != expected or result.get("schema_version") != 1:
        raise _CheckFailure("invalid_result")
    if (result.get("model_version") != MODEL_VERSION or result.get("model_sha256") != MODEL_SHA256
            or result.get("frame_sha256") != frame_sha256):
        raise _CheckFailure("identity_mismatch")
    width, height, count = result["width"], result["height"], result["detail_count"]
    if (any(isinstance(item, bool) or not isinstance(item, int) for item in (width, height, count))
            or not 1 <= width <= 8192 or not 1 <= height <= 8192
            or width * height > MAX_FRAME_PIXELS or count < 0 or count > 10000):
        raise _CheckFailure("invalid_result")
    confidence = result["confidence"]
    if count == 0:
        if confidence is not None:
            raise _CheckFailure("invalid_result")
        return "unknown", None, "model_not_qualified"
    if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence) or not MODEL_THRESHOLD <= confidence <= 1):
        raise _CheckFailure("invalid_result")
    return "occupied", float(confidence), None


def _offline_environment() -> dict[str, str]:
    allowed = ("PATH", "HOME", "LANG", "LC_ALL", "SYSTEMROOT", "WINDIR", "TEMP", "TMP")
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    env.update({
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_DATASETS_OFFLINE": "1",
    })
    return env
