"""Локальная реакция на результат серверного анализа изображения.

Контур: отмена только после трёх подтверждений для активной сессии печати.
Настройка AI сохраняется на принтере; endpoint принимает нормализованный результат.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime, timezone
import os
import stat
import time
import uuid

from .treed_autoprinter_bed import _capture_cancellable, _parse_snapshot_url
from .treed_vision_client import TreeDVisionClient

LOGGER = logging.getLogger(__name__)
MAX_FRAME_AGE_SECONDS = 6
MAX_POSITIVE_GAP_SECONDS = 6
MAX_POSITIVE_WINDOW_SECONDS = 12
RUNTIME_OBJECTS = {
    "print_stats": ["state", "filename"],
    "gcode_macro _TREED_CAM_STATE": ["enabled", "generation"],
    "gcode_macro _TREED_OPERATION_STATE": ["phase"],
}


# Блок 1: Решение для единственного поддерживаемого критического дефекта.
@dataclass
class DetectionDecision:
    count: int = 0
    first_at: float | None = None
    last_at: float | None = None
    worker_generation: str | None = None
    model_version: str | None = None
    last_frame_id: str | None = None

    def update(self, positive: bool, captured_at: float, worker_generation: str,
               model_version: str, frame_id: str) -> bool:
        fresh_series = (
            self.count > 0 and self.last_at is not None
            and 0 < captured_at - self.last_at <= MAX_POSITIVE_GAP_SECONDS
            and self.first_at is not None and captured_at - self.first_at <= MAX_POSITIVE_WINDOW_SECONDS
            and self.worker_generation == worker_generation
            and self.model_version == model_version
            and self.last_frame_id != frame_id
        )
        if not positive or (self.count > 0 and not fresh_series):
            self.count, self.first_at = 0, None
        if not positive:
            self.last_at = None
            self.worker_generation = self.model_version = None
            return False
        if self.count == 0:
            self.first_at = captured_at
            self.worker_generation, self.model_version = worker_generation, model_version
        self.count += 1
        self.last_at = captured_at
        self.last_frame_id = frame_id
        return self.count >= 3


class TreeDDetection:
    def __init__(self, config) -> None:
        # Блок 2: Конфигурация и штатные Moonraker endpoints с авторизацией.
        self.server = config.get_server()
        self.klippy_apis = self.server.lookup_component("klippy_apis")
        self.frames_root = Path(config.get("frames_root")).resolve()
        self.session_file = Path(config.get("session_file", "/tmp/treed_cam_session_dir"))
        self.settings_file = self.frames_root.parent / "config" / "detection.json"
        self.hold_file = self.frames_root.parent / "config" / "detection-hold.json"
        settings = json.loads(self.settings_file.read_text(encoding="utf-8")) if self.settings_file.exists() else {"enabled": True}
        if not isinstance(settings, dict) or type(settings.get("enabled")) is not bool:
            raise ValueError("Invalid AI detection settings")
        self._enabled = settings["enabled"]
        self._settings_generation = 0
        self._lock = asyncio.Lock()
        self._preview_lock = asyncio.Lock()
        self._preview_camera = _parse_snapshot_url(config.get("snapshot_url", "http://127.0.0.1:8080/?action=snapshot"))
        self._preview_vision = TreeDVisionClient(socket_path=config.get("vision_socket", None))
        self._session_id = None
        self._session_dir = None
        self._generation = 0
        self._last_order = None
        self._decision = DetectionDecision()
        self._analysis_last_result = None
        self._analysis_available = False
        self._analysis_session_started = None
        self._analysis_warning_sent = False
        self._recent_frame = None
        self._recent_frame_dir = None
        self._pause_state = "idle"
        self._pause_error = None
        self._incident = None
        try:
            saved = json.loads(self.hold_file.read_text(encoding="utf-8"))
            if isinstance(saved, dict) and saved.get("pause_state") in {"requested", "confirmed", "failed"}:
                self._incident = saved
                self._pause_state = saved["pause_state"]
                self._pause_error = saved.get("pause_error")
        except (OSError, ValueError, TypeError):
            pass
        self._autoprinter = None
        try:
            self._autoprinter = self.server.lookup_component("treed_autoprinter")
        except Exception:
            pass
        self.server.register_endpoint(
            "/server/treed/detection/status", ["GET"], self._handle_status,
            wrap_result=False,
        )
        self.server.register_endpoint(
            "/server/treed/detection/result", ["POST"], self._handle_result,
            wrap_result=False,
        )
        self.server.register_endpoint(
            "/server/treed/detection/settings", ["GET", "POST"], self._handle_settings,
            wrap_result=False,
        )
        self.server.register_endpoint(
            "/server/treed/detection/frame", ["POST"], self._handle_frame,
            wrap_result=False,
        )
        self.server.register_endpoint(
            "/server/treed/detection/reset", ["POST"], self._handle_reset,
            wrap_result=False,
        )
        self.server.register_endpoint(
            "/server/treed/detection/preview", ["GET"], self._handle_preview,
            wrap_result=False,
        )

    async def _handle_settings(self, web_request) -> dict:
        # Настройка и результаты сериализованы: выключение инвалидирует ответы в пути.
        async with self._lock:
            if web_request.get_action().upper() == "POST":
                enabled = web_request.get("enabled")
                if type(enabled) is not bool:
                    raise self.server.error("enabled must be boolean", 400)
                self.settings_file.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.settings_file.with_suffix(".tmp")
                temporary.write_text(json.dumps({"enabled": enabled}) + "\n", encoding="utf-8")
                temporary.replace(self.settings_file)
                if enabled != self._enabled:
                    self._enabled = enabled
                    self._settings_generation += 1
                    self._session_id = None
                    self._last_order = None
                    self._decision = DetectionDecision()
                    self._pause_error = None
            return {"enabled": self._enabled}

    async def _sync_session(self) -> None:
        # Блок 3: Сессия существует только при подтверждённой активной печати.
        objects = await self.klippy_apis.query_objects(RUNTIME_OBJECTS, default={})
        camera = objects.get("gcode_macro _TREED_CAM_STATE", {})
        active = (
            self._enabled
            and objects.get("print_stats", {}).get("state") == "printing"
            and objects.get("gcode_macro _TREED_OPERATION_STATE", {}).get("phase") == "printing"
            and camera.get("enabled") == 1
        )
        session_id, session_dir, generation = None, None, 0
        if active:
            try:
                generation = int(camera["generation"])
                marker = self.session_file.read_text(encoding="utf-8").strip()
                session_dir = Path(marker).resolve()
                if (not marker or generation < 1
                        or session_dir.parent != self.frames_root
                        or not session_dir.is_dir()):
                    raise ValueError("inactive camera session")
                session_id = f"{generation}:{self._settings_generation}:{session_dir.name}"
            except (OSError, ValueError, KeyError, TypeError):
                session_dir, generation = None, 0
        if session_id != self._session_id:
            self._session_id = session_id
            self._last_order = None
            self._decision = DetectionDecision()
            self._analysis_last_result = None
            self._analysis_available = False
            self._analysis_session_started = time.monotonic() if session_id is not None else None
            self._analysis_warning_sent = False
            if (session_id is not None and
                    (not self._incident or self._incident.get("context", {}).get("session_id") != session_id)):
                self._pause_state, self._pause_error = "idle", None
            # Пауза является инцидентом задания и переживает выключение камеры.
        self._session_dir, self._generation = session_dir, generation

    def _status(self) -> dict:
        reference_time = self._analysis_last_result or self._analysis_session_started
        if (self._session_id is not None and reference_time is not None
                and time.monotonic() - reference_time > 30 and not self._analysis_warning_sent):
            LOGGER.warning("treed_detection: локальный анализ недоступен более 30 секунд")
            self._analysis_warning_sent = True
        return {
            "enabled": self._enabled,
            "active": self._session_id is not None,
            "session_id": self._session_id,
            "counter": self._decision.count,
            "threshold": 3,
            "cancel_requested": False,
            "pause_state": self._pause_state,
            "pause_error": self._pause_error,
            "analysis_state": ("idle" if self._session_id is None else
                               "available" if self._analysis_available and self._analysis_last_result is not None
                               and time.monotonic() - self._analysis_last_result <= 30 else "unavailable"),
            "incident": self._incident,
            "context": self._incident.get("context") if self._incident else None,
            "model_version": self._decision.model_version,
            "worker_generation": self._decision.worker_generation,
            "recent_frame": self._recent_frame,
        }

    async def _handle_status(self, _web_request) -> dict:
        async with self._lock:
            await self._sync_session()
            return self._status()

    async def _handle_result(self, web_request) -> dict:
        # Блок 4: Принимается только ответ локальной службы для свежего кадра.
        async with self._lock:
            await self._sync_session()
            session_id = web_request.get_str("session_id")
            if self._session_id is None or session_id != self._session_id:
                raise self.server.error("Detection session is not active", 409)
            frame_id = web_request.get_str("frame_id")
            frame = self._session_dir / frame_id
            if (not frame_id or Path(frame_id).name != frame_id
                    or frame.suffix.lower() != ".jpg" or frame.is_symlink()
                    or not frame.is_file() or frame.resolve().parent != self._session_dir):
                raise self.server.error("Detection frame is not in the active session", 400)
            metadata = frame.stat()
            order = (metadata.st_mtime_ns, frame_id)
            if self._last_order is not None and order <= self._last_order:
                raise self.server.error("Duplicate or out-of-order detection frame", 409)
            captured_at = web_request.get("captured_at")
            worker_generation = web_request.get("worker_generation")
            model_version = web_request.get("model_version")
            verdict = web_request.get("verdict")
            score = web_request.get("score")
            threshold = web_request.get("threshold")
            try:
                source_time = datetime.fromisoformat(captured_at.replace("Z", "+00:00")).timestamp()
            except (AttributeError, TypeError, ValueError):
                self._last_order = order
                self._decision = DetectionDecision()
                raise self.server.error("Invalid source timestamp", 400)
            if (not isinstance(captured_at, str) or not captured_at.endswith("Z")
                    or not _is_uuid(worker_generation)
                    or not isinstance(model_version, str) or not model_version or len(model_version) > 128
                    or not isinstance(verdict, str)
                    or verdict not in {"spaghetti", "normal", "unknown", "unavailable"}
                    or (score is not None and (isinstance(score, bool)
                        or not isinstance(score, (int, float)) or not 0 <= score <= 1))
                    or (threshold is not None and (type(threshold) not in {int, float}
                        or not math.isfinite(threshold) or not 0 <= threshold <= 1))
                    or abs(source_time - metadata.st_mtime) > 3
                    or abs(time.time() - source_time) > MAX_FRAME_AGE_SECONDS):
                self._last_order = order
                self._decision = DetectionDecision()
                raise self.server.error("Invalid local detection result", 400)
            self._last_order = order
            if (metadata.st_size > 8 * 1024 * 1024 or metadata.st_size < 4):
                self._decision = DetectionDecision()
                raise self.server.error("Detection frame size is invalid", 400)
            with frame.open("rb") as stream:
                signature = stream.read(2)
                stream.seek(-2, os.SEEK_END)
                trailer = stream.read(2)
            if signature != b"\xff\xd8" or trailer != b"\xff\xd9":
                self._decision = DetectionDecision()
                raise self.server.error("Detection frame is not a JPEG", 400)
            positive = verdict == "spaghetti"
            self._analysis_last_result = time.monotonic()
            self._analysis_available = verdict not in {"unavailable", "unknown"}
            self._recent_frame = {"session_id": self._session_id, "frame_id": frame_id,
                                  "captured_at": captured_at, "verdict": verdict,
                                  "model_version": model_version, "worker_generation": worker_generation,
                                  "score": score, "threshold": threshold}
            self._recent_frame_dir = self._session_dir
            confirmed = self._decision.update(
                positive, source_time, worker_generation, model_version, frame_id)
            self._recent_frame["consecutive_count"] = self._decision.count
            LOGGER.info("treed_detection: counter=%s, pause_state=%s",
                        self._decision.count, self._pause_state)
            if confirmed and self._pause_state == "idle":
                context = {"session_id": self._session_id, "frame_id": frame_id,
                           "captured_at": captured_at, "worker_generation": worker_generation,
                           "model_version": model_version, "score": score}
                await self._request_pause(context)
                await self._sync_session()
            return self._status()

    async def _handle_preview(self, _web_request) -> dict:
        # Просмотр использует штатные результаты печати и не переключает её модель на bed.
        if self._preview_lock.locked():
            raise self.server.error("Camera preview is busy", 503)
        async with self._preview_lock:
            async with self._lock:
                await self._sync_session()
                enabled = self._enabled
                recent = dict(self._recent_frame) if self._recent_frame else None
            objects = await self.klippy_apis.query_objects(RUNTIME_OBJECTS, default={})
            idle = (objects.get("gcode_macro _TREED_OPERATION_STATE", {}).get("phase") == "idle"
                    and objects.get("print_stats", {}).get("state") in {"standby", "complete", "cancelled", "error"})
            if enabled and not idle and recent:
                age = time.time() - datetime.fromisoformat(recent["captured_at"].replace("Z", "+00:00")).timestamp()
                if -1 <= age <= MAX_FRAME_AGE_SECONDS:
                    class FrameRequest:
                        def get_str(self, key):
                            return recent[key]
                    try:
                        frame = await self._handle_frame(FrameRequest())
                    except Exception:
                        pass
                    else:
                        return {**frame, "task": "spaghetti", "boxes": [], "reason": None}
            try:
                jpeg = await _capture_cancellable(self._preview_camera)
            except asyncio.CancelledError:
                raise
            except Exception:
                raise self.server.error("Camera snapshot is unavailable", 503) from None
            captured_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            preview = {"jpeg_base64": base64.b64encode(jpeg).decode("ascii"),
                       "captured_at": captured_at, "task": None, "verdict": "unavailable",
                       "score": None, "threshold": None, "boxes": [], "model_version": None,
                       "reason": "ai_disabled" if not enabled else "awaiting_detection"}
            if not enabled or not idle:
                return preview
            # После захвата повторно проверяем простой перед диагностическим анализом.
            objects = await self.klippy_apis.query_objects(RUNTIME_OBJECTS, default={})
            if (not self._enabled or objects.get("gcode_macro _TREED_OPERATION_STATE", {}).get("phase") != "idle"
                    or objects.get("print_stats", {}).get("state") not in {"standby", "complete", "cancelled", "error"}):
                preview["reason"] = "ai_disabled" if not self._enabled else "awaiting_detection"
                return preview
            preview["task"] = "bed"
            settings_generation = self._settings_generation
            try:
                result = await self._preview_vision.infer("bed", {"captured_at": captured_at}, jpeg)
                objects = await self.klippy_apis.query_objects(RUNTIME_OBJECTS, default={})
                if (settings_generation != self._settings_generation or not self._enabled
                        or objects.get("gcode_macro _TREED_OPERATION_STATE", {}).get("phase") != "idle"
                        or objects.get("print_stats", {}).get("state") not in {"standby", "complete", "cancelled", "error"}):
                    preview["task"] = None
                    preview["reason"] = "ai_disabled" if not self._enabled else "awaiting_detection"
                    return preview
                boxes = result["boxes"]
                if (result["verdict"] not in {"occupied", "unknown", "unavailable"}
                        or any(not isinstance(box, list) or len(box) != 4
                               or any(type(value) not in {int, float} or not math.isfinite(value) for value in box)
                               or not (0 <= box[0] < box[2] <= 8192 and 0 <= box[1] < box[3] <= 8192)
                               for box in boxes)
                        or (result["verdict"] == "occupied") != bool(boxes)):
                    raise ValueError("invalid_preview")
                # Полный ответ ограничен IPC; для экрана достаточно первых ста рамок.
                preview.update({key: result[key] for key in ("verdict", "score", "threshold", "model_version", "reason")})
                preview["boxes"] = boxes[:100]
            except asyncio.CancelledError:
                raise
            except Exception:
                preview["reason"] = "vision_unavailable"
            return preview

    async def _handle_frame(self, web_request) -> dict:
        async with self._lock:
            session_id = web_request.get_str("session_id")
            frame_id = web_request.get_str("frame_id")
            if (not self._recent_frame or self._recent_frame.get("session_id") != session_id
                    or self._recent_frame.get("frame_id") != frame_id
                    or not self._recent_frame_dir):
                raise self.server.error("Detection frame is not current", 404)
            directory = self._recent_frame_dir
            frame = directory / frame_id
            if (Path(frame_id).name != frame_id or directory.parent != self.frames_root
                    or frame.resolve().parent != directory):
                raise self.server.error("Detection frame is unavailable", 404)
            directory_fd = file_fd = None
            try:
                directory_fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                                       | getattr(os, "O_NOFOLLOW", 0))
                file_fd = os.open(frame_id, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                                  dir_fd=directory_fd)
                metadata = os.fstat(file_fd)
                if not stat.S_ISREG(metadata.st_mode):
                    raise self.server.error("Detection frame is unavailable", 404)
                if metadata.st_size > 8 * 1024 * 1024:
                    raise self.server.error("Detection frame is too large", 413)
                chunks = bytearray()
                while len(chunks) <= 8 * 1024 * 1024:
                    chunk = os.read(file_fd, min(65536, 8 * 1024 * 1024 + 1 - len(chunks)))
                    if not chunk:
                        break
                    chunks.extend(chunk)
                data = bytes(chunks)
                if len(data) > 8 * 1024 * 1024 or len(data) < 4:
                    raise self.server.error("Detection frame size is invalid", 413)
                if data[:2] != b"\xff\xd8" or data[-2:] != b"\xff\xd9":
                    raise self.server.error("Detection frame is invalid", 404)
            except OSError:
                raise self.server.error("Detection frame is unavailable", 404) from None
            finally:
                if file_fd is not None:
                    os.close(file_fd)
                if directory_fd is not None:
                    os.close(directory_fd)
            return {**self._recent_frame, "jpeg_base64": base64.b64encode(data).decode("ascii")}

    async def _handle_reset(self, web_request) -> dict:
        async with self._lock:
            await self._sync_session()
            if self._session_id is None or web_request.get_str("session_id") != self._session_id:
                raise self.server.error("Detection session is not active", 409)
            self._decision = DetectionDecision()
            return self._status()

    async def _request_pause(self, context: dict) -> None:
        # Инцидент фиксируется до любой внешней команды, чтобы сбой не терял доказательство.
        self._incident = {"incident_id": str(uuid.uuid4()), "context": context,
                          "pause_state": "requested", "requested_at": datetime.now(timezone.utc).isoformat()}
        self._pause_state, self._pause_error = "requested", None
        try:
            self._persist_incident()
        except OSError:
            self._pause_error = "Не удалось сохранить локальное удержание"
            LOGGER.exception("treed_detection: local incident persistence failed")
        old_generation = self._generation
        deadline = time.monotonic() + 6
        last_error = None
        pause_dispatched = False
        original_filename = None
        for attempt in range(3):
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                current = await asyncio.wait_for(
                    self.klippy_apis.query_objects(RUNTIME_OBJECTS, default={}), timeout=remaining)
                current_camera = current.get("gcode_macro _TREED_CAM_STATE", {})
                stats = current.get("print_stats", {})
                print_state = stats.get("state")
                filename = stats.get("filename")
                if print_state == "paused":
                    if pause_dispatched:
                        self._pause_state = "confirmed"
                        self._incident["pause_state"] = "confirmed"
                        self._persist_incident()
                        return
                    last_error = "Принтер был приостановлен до команды AI-паузы"
                    break
                if (print_state != "printing" or current_camera.get("generation") != old_generation
                                or current.get("gcode_macro _TREED_OPERATION_STATE", {}).get("phase") != "printing"):
                    last_error = "Печать возобновлена или сменилось поколение камеры"
                    break
                if original_filename is None:
                    original_filename = filename
                elif filename != original_filename:
                    last_error = "Сменилось задание печати"
                    break
                if self._autoprinter is not None and hasattr(self._autoprinter, "hold_for_detection"):
                    try:
                        await asyncio.wait_for(
                            self._autoprinter.hold_for_detection("ai_detection", context),
                            timeout=max(0.01, min(2.0, deadline - time.monotonic())),
                        )
                    except Exception:
                        last_error = "Не удалось записать удержание AutoPrinter"
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                pause_dispatched = True
                await asyncio.wait_for(self.klippy_apis.run_gcode(
                    f"_TREED_DETECTION_PAUSE GENERATION={old_generation}"), timeout=remaining)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                state = await asyncio.wait_for(self.klippy_apis.query_objects(
                    {"print_stats": ["state"]}, default={}), timeout=remaining)
                paused = state.get("print_stats", {}).get("state") == "paused"
                if paused:
                    self._pause_state = "confirmed"
                    self._incident["pause_state"] = "confirmed"
                    self._incident["pause_error"] = last_error or self._pause_error
                    try:
                        self._persist_incident()
                    except OSError:
                        self._pause_error = "Пауза выполнена, но статус не сохранён локально"
                    return
                last_error = "Пауза не подтверждена состоянием принтера"
            except Exception:
                last_error = "Не удалось подтвердить паузу принтера"
                LOGGER.exception("treed_detection: pause attempt failed")
            if attempt < 2 and time.monotonic() < deadline:
                await asyncio.sleep(min(0.25 * (attempt + 1), max(0, deadline - time.monotonic())))
        self._pause_state, self._pause_error = "failed", last_error
        self._incident.update(pause_state="failed", pause_error=last_error)
        try:
            self._persist_incident()
        except OSError:
            self._pause_error = "Пауза не подтверждена; инцидент не удалось сохранить"
            LOGGER.exception("treed_detection: failed incident persistence")

    def _persist_incident(self) -> None:
        self.hold_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.hold_file.with_suffix(".tmp")
        encoded = (json.dumps(self._incident, ensure_ascii=False) + "\n").encode("utf-8")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, self.hold_file)
        if os.name == "posix":
            directory_fd = os.open(self.hold_file.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)


# Блок 6: Entry-point штатной загрузки компонента.
def load_component(config) -> TreeDDetection:
    return TreeDDetection(config)


def _is_uuid(value) -> bool:
    if not isinstance(value, str):
        return False
    try:
        uuid.UUID(value)
    except (ValueError, TypeError, AttributeError):
        return False
    return True
