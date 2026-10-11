"""Оценка закрытой сессии кадров TreeD; контур required API.

GET связывает завершённое задание с сессией, POST сохраняет выбранный ID.
Компонент не запускает печать и не преобразует кадры.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import stat
import tempfile

MAX_JSON_BYTES = 32768
RUNTIME_OBJECTS = {
    "print_stats": ["state", "filename"],
    "gcode_macro _TREED_CAM_STATE": ["enabled", "session_token"],
}


# Блок 1: Недоверенные идентификаторы не становятся произвольными путями.
def _validate_filename(filename: object) -> str:
    if (not isinstance(filename, str) or not filename or len(filename) > 4096
            or "\\" in filename or ":" in filename
            or any(ord(char) < 32 for char in filename)
            or any(part in ("", ".", "..") for part in filename.split("/"))):
        raise ValueError("Некорректный путь задания")
    return filename


def _validate_session_id(session_id: object) -> str:
    if (not isinstance(session_id, str)
            or re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9._-]{0,239}", session_id) is None):
        raise ValueError("Некорректный ID сессии")
    return session_id


# Блок 2: Ограниченное чтение обычного файла, без ссылок и FIFO.
def _read_json(path: Path) -> dict:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_JSON_BYTES:
        raise ValueError("Метаданные должны быть обычным непустым JSON-файлом")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        opened = os.fstat(stream.fileno())
        identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
        if not stat.S_ISREG(opened.st_mode) or identity(before) != identity(opened):
            raise ValueError("Метаданные изменились перед чтением")
        data = stream.read(MAX_JSON_BYTES + 1)
        if (len(data) != opened.st_size or identity(opened) != identity(os.fstat(stream.fileno()))
                or identity(opened) != identity(path.lstat())):
            raise ValueError("Метаданные изменились во время чтения")
    result = json.loads(data.decode("utf-8"))
    if not isinstance(result, dict):
        raise ValueError("Ожидался JSON-объект")
    return result


# Блок 3: Авторизованный endpoint; конфиг совпадает с runtime путями камеры.
class TreeDPrintFeedback:
    def __init__(self, config) -> None:
        self.server = config.get_server()
        self.klippy_apis = self.server.lookup_component("klippy_apis")
        self.frames_root = Path(config.get("frames_root")).absolute()
        self.session_file = Path(config.get("session_file", "/tmp/treed_cam_session_dir"))
        self.unavailable_file = self.session_file.with_name(self.session_file.name + ".feedback-unavailable")
        self._lock = asyncio.Lock()
        self.server.register_endpoint(
            "/server/treed/print-feedback", ["GET", "POST"], self._handle_feedback,
            wrap_result=False,
        )

    def _session(self, session_id: str, filename: str, token: int | None = None) -> Path:
        directory = self.frames_root / _validate_session_id(session_id)
        if (self.frames_root.is_symlink() or directory.is_symlink()
                or not directory.is_dir()
                or directory.resolve().parent != self.frames_root.resolve()):
            raise ValueError("Сессия должна быть прямым каталогом внутри prints без ссылок")
        metadata = _read_json(directory / "session.json")
        saved_token = metadata.get("sessionToken")
        if (metadata.get("sessionId") != session_id or metadata.get("filename") != filename
                or type(saved_token) is not int or not 1 <= saved_token < 9007199254740991
                or (token is not None and saved_token != token)
                or metadata.get("closed") is not True
                or not isinstance(metadata.get("startedAt"), str)
                or not metadata["startedAt"]
                or not isinstance(metadata.get("finishedAt"), str)
                or not metadata["finishedAt"]):
            raise ValueError("Закрытая сессия не соответствует заданию")
        if not any(frame.is_file() and not frame.is_symlink() for frame in directory.glob("img_*.jpg")):
            raise ValueError("В сессии нет сохранённых кадров")
        return directory

    def _result(self, directory: Path, filename: str) -> dict:
        result = {"sessionId": directory.name, "filename": filename, "rating": None}
        rating_path = directory / "rating.json"
        if rating_path.exists() or rating_path.is_symlink():
            saved = _read_json(rating_path)
            if (saved.get("sessionId") != directory.name or saved.get("filename") != filename
                    or saved.get("rating") not in ("good", "bad")):
                raise ValueError("Некорректная сохранённая оценка")
            result["rating"] = saved["rating"]
        return result

    def _save(self, directory: Path, result: dict) -> None:
        # Блок 4: Уникальный временный файл; сбой replace сохраняет прежнюю оценку.
        path = directory / "rating.json"
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError("Оценка должна быть обычным файлом")
        fd, temporary = tempfile.mkstemp(prefix=".rating-", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(result, stream, ensure_ascii=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            # Повторная проверка перед публикацией защищает от подмены пути сессии.
            self._session(directory.name, result["filename"])
            if path.is_symlink():
                raise ValueError("Ссылка вместо оценки запрещена")
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    async def _handle_feedback(self, web_request) -> dict:
        filename = web_request.get("filename", None)
        action = web_request.get_action().upper()
        try:
            filename = _validate_filename(filename)
            if action == "POST":
                session_id = _validate_session_id(web_request.get("sessionId", None))
                rating = web_request.get("rating", None)
                if rating not in ("good", "bad"):
                    raise ValueError("rating должен быть good или bad")
        except ValueError as error:
            raise self.server.error(str(error), 400) from error

        async with self._lock:
            # Блок 5: Latest читается только для complete; POST всегда использует явный ID.
            if action == "GET":
                objects = await self.klippy_apis.query_objects(RUNTIME_OBJECTS, default={})
                stats = objects.get("print_stats", {})
                camera = objects.get("gcode_macro _TREED_CAM_STATE", {})
                token = camera.get("session_token")
                if (stats.get("state") != "complete" or stats.get("filename") != filename
                        or camera.get("enabled") != 0
                        or type(token) is not int or not 1 <= token < 9007199254740991
                        or self.session_file.exists() or self.session_file.is_symlink()
                        or self.unavailable_file.exists() or self.unavailable_file.is_symlink()):
                    raise self.server.error("Оценка доступна после завершения печати и закрытия камеры", 409)
            try:
                if action == "GET":
                    session_id = _validate_session_id(_read_json(self.frames_root / ".latest_session").get("sessionId"))
                directory = self._session(session_id, filename, token if action == "GET" else None)
                result = self._result(directory, filename)
                if action == "POST":
                    result["rating"] = rating
                    self._save(directory, result)
                return result
            except (OSError, ValueError, TypeError) as error:
                raise self.server.error(f"Оценка сессии недоступна: {error}", 409) from error


# Блок 6: Entry-point штатной загрузки Moonraker.
def load_component(config) -> TreeDPrintFeedback:
    return TreeDPrintFeedback(config)
