"""Ограниченный клиент локальной службы анализа изображений."""

from __future__ import annotations

import asyncio
import base64
import json
import math
import os
from pathlib import Path
import re
import uuid

PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 8 * 1024 * 1024
MAX_REQUEST_BYTES = 12 * 1024 * 1024
MAX_RESPONSE_BYTES = 64 * 1024
SOCKET_PATH = Path.home() / "treed/vision/run/vision.sock"


class VisionUnavailable(Exception):
    pass


class TreeDVisionClient:
    """Отправляет только локальный JPEG и ограниченный контекст через Unix socket."""

    def __init__(self, socket_path: str | os.PathLike | None = None,
                 timeout: float = 25.0) -> None:
        selected_path = SOCKET_PATH if socket_path is None else Path(socket_path)
        if "\0" in str(selected_path) or not selected_path.is_absolute():
            raise ValueError("invalid_socket_path")
        self.socket_path = str(selected_path)
        self.timeout = timeout

    async def infer(self, task: str, context: dict, jpeg: bytes) -> dict:
        if task not in {"spaghetti", "bed"} or not isinstance(context, dict):
            raise ValueError("invalid_request")
        if not isinstance(jpeg, bytes) or not 4 <= len(jpeg) <= MAX_FRAME_BYTES:
            raise ValueError("invalid_frame")
        try:
            context_size = len(json.dumps(context, ensure_ascii=True, allow_nan=False).encode("utf-8"))
        except (TypeError, ValueError, UnicodeError):
            raise ValueError("invalid_context") from None
        if context_size > 16 * 1024:
            raise ValueError("context_too_large")
        request_id = str(uuid.uuid4())
        payload = json.dumps({
            "protocol": PROTOCOL_VERSION,
            "request_id": request_id,
            "task": task,
            "context": context,
            "jpeg_base64": base64.b64encode(jpeg).decode("ascii"),
        }, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("utf-8") + b"\n"
        if len(payload) > MAX_REQUEST_BYTES:
            raise ValueError("request_too_large")

        async def exchange():
            reader, writer = await asyncio.open_unix_connection(
                self.socket_path, limit=MAX_RESPONSE_BYTES)
            try:
                writer.write(payload)
                await writer.drain()
                line = await reader.readline()
                if not line or len(line) > MAX_RESPONSE_BYTES or not line.endswith(b"\n"):
                    raise VisionUnavailable("invalid_response")
                return json.loads(line)
            finally:
                writer.close()
                await writer.wait_closed()

        try:
            response = await asyncio.wait_for(exchange(), timeout=self.timeout)
        except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError,
                asyncio.LimitOverrunError, json.JSONDecodeError, ValueError):
            raise VisionUnavailable("service_unavailable") from None
        expected = {"protocol", "request_id", "task", "context", "worker_generation",
                    "model_version", "verdict", "score", "threshold", "boxes", "reason", "processing_ms"}
        if (not isinstance(response, dict) or set(response) != expected
                or response.get("protocol") != PROTOCOL_VERSION
                or response.get("request_id") != request_id
                or response.get("context") != context
                or not isinstance(response.get("task"), str)
                or response["task"] != task
                or not isinstance(response.get("worker_generation"), str)
                or not response["worker_generation"]
                or not isinstance(response.get("model_version"), str)
                or not response["model_version"] or len(response["model_version"]) > 128
                or response.get("verdict") not in {"spaghetti", "normal", "occupied", "unknown", "unavailable"}
                or (response.get("score") is not None and (
                    isinstance(response["score"], bool)
                    or not isinstance(response["score"], (int, float))
                    or not math.isfinite(response["score"])
                    or not 0 <= response["score"] <= 1))
                or isinstance(response.get("threshold"), bool)
                or not isinstance(response.get("threshold"), (int, float))
                or not math.isfinite(response["threshold"])
                or not 0 <= response["threshold"] <= 1
                or not isinstance(response.get("boxes"), list)
                or len(response["boxes"]) > 10000
                or (response.get("reason") is not None and not isinstance(response["reason"], str))
                or isinstance(response.get("processing_ms"), bool)
                or not isinstance(response.get("processing_ms"), (int, float))
                or not math.isfinite(response["processing_ms"])
                or response["processing_ms"] < 0):
            raise VisionUnavailable("invalid_response")
        try:
            uuid.UUID(response["worker_generation"])
        except (ValueError, TypeError, AttributeError):
            raise VisionUnavailable("invalid_response") from None
        if response.get("reason") is not None and not re.fullmatch(r"[a-z0-9_]{1,64}", response["reason"]):
            raise VisionUnavailable("invalid_response")
        return response
