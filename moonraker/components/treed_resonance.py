"""Чтение результатов input shaper для TreeD Shell, контур read-only.

Компонент не запускает калибровку и не меняет файлы или настройки принтера.
"""

from __future__ import annotations

# Блок 1: Фиксированные результаты штатных макросов и граница размера.
import asyncio
import csv
import hashlib
import io
import math
import os
from pathlib import Path
import stat
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..confighelper import ConfigHelper

MAX_CSV_BYTES = 2 * 1024 * 1024
RESULT_FILES = {
    "full": {"x": Path("/tmp/calibration_data_x_treed_full.csv"),
             "y": Path("/tmp/calibration_data_y_treed_full.csv")},
    "light": {"x": Path("/tmp/calibration_data_x_treed_light_x.csv"),
              "y": Path("/tmp/calibration_data_y_treed_light_y.csv")},
}


# Блок 2: Проверка формата Klipper без преобразования исходного CSV.
def _validate_csv(text: str) -> None:
    rows = csv.reader(io.StringIO(text))
    header = next(rows, [])
    if header[:5] != ["freq", "psd_x", "psd_y", "psd_z", "psd_xyz"]:
        raise ValueError("Некорректный заголовок CSV резонансов")
    previous_frequency = -1.0
    count = 0
    for row in rows:
        if len(row) != len(header):
            raise ValueError("Неполная строка CSV резонансов")
        values = [float(value) for value in row[:5]]
        if not all(math.isfinite(value) and value >= 0 for value in values):
            raise ValueError("Некорректные значения CSV резонансов")
        if values[0] <= previous_frequency:
            raise ValueError("Частоты CSV резонансов должны возрастать")
        previous_frequency = values[0]
        count += 1
    if count < 2:
        raise ValueError("Недостаточно данных CSV резонансов")


# Блок 3: Один файловый дескриптор, без ссылок и блокирующего чтения FIFO.
def _file_identity(info: os.stat_result) -> tuple:
    # На Windows lstat/fstat трактуют ctime по-разному; Linux также проверяет смену метаданных.
    change_time = info.st_ctime_ns if os.name == "posix" else None
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, change_time)


def _read_axis(path: Path) -> dict | None:
    try:
        before = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("Результат резонансов должен быть обычным файлом")
    if not 0 < before.st_size <= MAX_CSV_BYTES:
        raise ValueError("CSV резонансов пуст или превышает 2 МиБ")
    flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
             | getattr(os, "O_BINARY", 0))
    fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or _file_identity(before) != _file_identity(opened):
            raise ValueError("Файл резонансов изменился перед чтением")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(MAX_CSV_BYTES + 1)
        if (len(data) != opened.st_size or len(data) > MAX_CSV_BYTES
                or _file_identity(opened) != _file_identity(os.fstat(fd))
                or _file_identity(opened) != _file_identity(path.lstat())):
            raise ValueError("Файл резонансов изменился во время чтения")
    finally:
        os.close(fd)
    text = data.decode("utf-8")
    _validate_csv(text)
    revision = f"{opened.st_mtime_ns}:{hashlib.sha256(data).hexdigest()}"
    return {"revision": revision, "csv": text}


# Блок 4: GET со штатной авторизацией Moonraker и явными ошибками чтения.
class TreeDResonance:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.server.register_endpoint(
            "/server/treed/resonances", ["GET"], self._handle_resonances
        )

    async def _handle_resonances(self, web_request: object) -> dict:
        mode = getattr(web_request, "get_str")("mode", "full")
        if mode not in RESULT_FILES:
            raise self.server.error("mode должен быть full или light", 400)
        try:
            axes = await asyncio.get_running_loop().run_in_executor(
                None, lambda: {axis: _read_axis(path) for axis, path in RESULT_FILES[mode].items()}
            )
        except (OSError, ValueError, csv.Error) as error:
            raise self.server.error(f"Результаты резонансов недоступны: {error}", 409) from error
        return {"mode": mode, "axes": axes}


def load_component(config: ConfigHelper) -> TreeDResonance:
    return TreeDResonance(config)
