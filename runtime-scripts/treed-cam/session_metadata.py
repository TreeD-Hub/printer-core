"""Метаданные сессии кадров для оценки печати; контур best-effort.

Ошибка возвращается вызывающему скрипту, который сохраняет контур снимков.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
from datetime import datetime, timezone


def parse_token(value: str) -> int | None:
    if value == "":
        return None
    if (len(value) > 16 or not value.isascii() or not value.isdecimal()
            or not 1 <= int(value) < 9007199254740991):
        raise ValueError("Некорректный токен сессии")
    return int(value)


# Блок 1: Полный относительный путь задания и прямой каталог без ссылок.
def validate_filename(filename: object) -> str:
    if (not isinstance(filename, str) or not filename or len(filename) > 4096
            or "\\" in filename or ":" in filename
            or any(ord(char) < 32 for char in filename)
            or any(part in ("", ".", "..") for part in filename.split("/"))):
        raise ValueError("Некорректный путь задания")
    return filename


def session_directory(root: Path, directory: Path) -> Path:
    if root.is_symlink() or directory.is_symlink():
        raise ValueError("Ссылки вместо каталога сессии запрещены")
    root = root.resolve(strict=True)
    directory = directory.resolve(strict=True)
    if directory.parent != root or not directory.is_dir():
        raise ValueError("Сессия должна быть прямым каталогом внутри prints")
    return directory


# Блок 2: Атомарная публикация JSON без промежуточного частичного файла.
def write_json(path: Path, data: dict) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("Метаданные должны быть обычным файлом")
    fd, temporary = tempfile.mkstemp(prefix=".session-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


# Блок 3: Latest меняется только при старте; закрытие сохраняет прежнюю привязку.
def start_session(root: Path, directory: Path, filename: str, token: int | None = None) -> None:
    directory = session_directory(root, directory)
    metadata = {
        "sessionId": directory.name,
        "filename": validate_filename(filename),
        "sessionToken": token,
        "startedAt": datetime.now(timezone.utc).isoformat(),
        "finishedAt": None,
        "closed": False,
    }
    write_json(directory / "session.json", metadata)
    if token is not None:
        write_json(directory.parent / ".latest_session", {"sessionId": directory.name})


def stop_session(root: Path, directory: Path, token: int | None = None) -> bool:
    directory = session_directory(root, directory)
    path = directory / "session.json"
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 32768:
        raise ValueError("Метаданные сессии недоступны")
    metadata = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(metadata, dict) or metadata.get("sessionId") != directory.name
            or type(metadata.get("closed")) is not bool):
        raise ValueError("Некорректные метаданные сессии")
    validate_filename(metadata.get("filename"))
    if metadata.get("sessionToken") != token:
        return False
    if metadata["closed"]:
        return True
    metadata.update(closed=True, finishedAt=datetime.now(timezone.utc).isoformat())
    write_json(path, metadata)
    return True


# Блок 4: CLI вызывается только runtime-скриптами start/stop.
if __name__ == "__main__":
    try:
        action, root_arg, directory_arg, *args = sys.argv[1:]
        if action == "start" and len(args) == 2:
            token = parse_token(args[1])
            start_session(Path(root_arg), Path(directory_arg), args[0], token)
            if token is None:
                print("[treed-cam] Ручная сессия без токена: оценка недоступна", file=sys.stderr)
                raise SystemExit(3)
        elif action == "stop" and len(args) == 1:
            if not stop_session(Path(root_arg), Path(directory_arg), parse_token(args[0])):
                raise SystemExit(3)
        else:
            raise ValueError("Ожидался start ROOT DIRECTORY FILENAME TOKEN или stop ROOT DIRECTORY TOKEN")
    except (OSError, ValueError, TypeError) as error:
        print(f"[treed-cam] {error}", file=sys.stderr)
        raise SystemExit(1)
