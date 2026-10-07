"""Локальная реакция на результат серверного анализа изображения.

Контур: отмена только после трёх подтверждений для активной сессии печати.
Настройка AI сохраняется на принтере; endpoint принимает нормализованный результат.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path

LOGGER = logging.getLogger(__name__)
RUNTIME_OBJECTS = {
    "print_stats": ["state"],
    "gcode_macro _TREED_CAM_STATE": ["enabled", "generation"],
    "gcode_macro _TREED_OPERATION_STATE": ["phase"],
}


# Блок 1: Решение для единственного поддерживаемого критического дефекта.
@dataclass
class DetectionDecision:
    count: int = 0
    cancel_requested: bool = False

    def update(self, critical: bool | None, defect: str) -> bool:
        if self.cancel_requested:
            return False
        self.count = self.count + 1 if critical is True and defect == "spaghetti" else 0
        if self.count == 3:
            self.cancel_requested = True
            return True
        return False


class TreeDDetection:
    def __init__(self, config) -> None:
        # Блок 2: Конфигурация и штатные Moonraker endpoints с авторизацией.
        self.server = config.get_server()
        self.klippy_apis = self.server.lookup_component("klippy_apis")
        self.frames_root = Path(config.get("frames_root")).resolve()
        self.session_file = Path(config.get("session_file", "/tmp/treed_cam_session_dir"))
        self.settings_file = self.frames_root.parent / "config" / "detection.json"
        settings = json.loads(self.settings_file.read_text(encoding="utf-8")) if self.settings_file.exists() else {"enabled": True}
        if not isinstance(settings, dict) or type(settings.get("enabled")) is not bool:
            raise ValueError("Invalid AI detection settings")
        self._enabled = settings["enabled"]
        self._settings_generation = 0
        self._lock = asyncio.Lock()
        self._session_id = None
        self._session_dir = None
        self._generation = 0
        self._last_order = None
        self._decision = DetectionDecision()
        self._cancel_error = None
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
                    self._cancel_error = None
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
            self._cancel_error = None
        self._session_dir, self._generation = session_dir, generation

    def _status(self) -> dict:
        return {
            "enabled": self._enabled,
            "active": self._session_id is not None,
            "session_id": self._session_id,
            "counter": self._decision.count,
            "threshold": 3,
            "cancel_requested": self._decision.cancel_requested,
            "cancel_error": self._cancel_error,
        }

    async def _handle_status(self, _web_request) -> dict:
        async with self._lock:
            await self._sync_session()
            return self._status()

    async def _handle_result(self, web_request) -> dict:
        # Блок 4: Один результат на сохранённый кадр, без старых сессий и повторов.
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
            order = (frame.stat().st_mtime_ns, frame_id)
            if self._last_order is not None and order <= self._last_order:
                raise self.server.error("Duplicate or out-of-order detection frame", 409)

            critical = web_request.get("critical", "missing")
            defect = web_request.get("defect", "spaghetti")
            if ((critical is not None and type(critical) is not bool)
                    or not isinstance(defect, str)):
                self._last_order = order
                self._decision.update(None, "")
                raise self.server.error("critical must be boolean/null; defect must be a string", 400)
            self._last_order = order
            cancel = self._decision.update(critical, defect)
            LOGGER.info("treed_detection: counter=%s, cancel_requested=%s",
                        self._decision.count, self._decision.cancel_requested)

            # Блок 5: Klipper повторно проверяет поколение непосредственно перед отменой.
            if cancel:
                try:
                    await self.klippy_apis.run_gcode(
                        f"_TREED_DETECTION_CANCEL GENERATION={self._generation}"
                    )
                except Exception:
                    self._cancel_error = "Не удалось передать команду отмены в Klipper"
                    LOGGER.exception("treed_detection: cancel command failed")
                    raise self.server.error(self._cancel_error, 503)
            return self._status()


# Блок 6: Entry-point штатной загрузки компонента.
def load_component(config) -> TreeDDetection:
    return TreeDDetection(config)
