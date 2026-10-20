"""Передача свежих кадров MJPEG на сервер и опциональная реакция на дефект.

Использует только стандартную библиотеку Python. Отказ сети не влияет на печать.
"""

import argparse
import base64
from datetime import datetime, timezone
import json
import math
import os
import sys
import threading
import time
import uuid
import socket
from collections import deque
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

MAX_JPEG_BYTES = 8 * 1024 * 1024
MAX_OWNED_FRAME_BYTES = 128 * 1024 * 1024


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def local_open(request, timeout):
    return build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=timeout)


# Блок 1: Ограниченный разбор multipart MJPEG ustreamer с Content-Length.
def jpeg_frames(stream):
    while True:
        line = stream.readline(8193)
        if not line:
            raise EOFError("Камера закрыла поток")
        if len(line) > 8192:
            raise ValueError("Слишком длинный заголовок MJPEG")
        if not line.startswith(b"--"):
            if line.strip():
                raise ValueError("Нет границы MJPEG")
            continue
        headers = {}
        for _ in range(16):
            line = stream.readline(8193)
            if not line or len(line) > 8192:
                raise ValueError("Повреждённый заголовок MJPEG")
            if not line.strip():
                break
            key, separator, value = line.partition(b":")
            if not separator:
                raise ValueError("Повреждённый заголовок MJPEG")
            headers[key.strip().lower()] = value.strip().lower()
        else:
            raise ValueError("Слишком много заголовков MJPEG")
        size = int(headers.get(b"content-length", b"0"))
        if not 0 < size <= MAX_JPEG_BYTES or headers.get(b"content-type") != b"image/jpeg":
            raise ValueError("Недопустимый JPEG в MJPEG")
        frame = stream.read(size)
        if len(frame) != size:
            raise EOFError("Неполный кадр камеры")
        yield frame


def http_url(value):
    url = urlsplit(value)
    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
        raise ValueError("Нужен HTTP(S) URL без встроенных паролей")
    if url.fragment:
        raise ValueError("URL не должен содержать fragment")
    return url


# Блок 2: Чтение непрерывного видео; хранится только самый свежий кадр.
class Camera:
    def __init__(self, url, timeout):
        parsed = http_url(url)
        if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Камера должна быть доступна через loopback")
        self.url, self.timeout = url, timeout
        self.condition = threading.Condition()
        self.stop = threading.Event()
        self.latest = None
        self.sequence = 0
        self.revision = 0
        self.thread = threading.Thread(target=self.read, daemon=True)

    def read(self):
        while not self.stop.is_set():
            try:
                with local_open(self.url, timeout=self.timeout) as stream:
                    if stream.headers.get_content_type() != "multipart/x-mixed-replace":
                        raise ValueError("Камера не возвращает multipart MJPEG")
                    for frame in jpeg_frames(stream):
                        if self.stop.is_set():
                            return
                        with self.condition:
                            self.sequence += 1
                            self.latest = (self.sequence, time.monotonic(), frame)
                            self.condition.notify_all()
            except Exception as exc:
                with self.condition:
                    self.latest = None
                    self.revision += 1
                print(f"Камера недоступна: {type(exc).__name__}", file=sys.stderr)
                self.stop.wait(2)

    def next(self, previous):
        with self.condition:
            fresh = lambda: (self.latest is not None and self.latest[0] > previous
                             and time.monotonic() - self.latest[1] < self.timeout)
            if not self.condition.wait_for(fresh, timeout=self.timeout):
                raise TimeoutError("Нет свежего кадра")
            return self.latest


# Блок 3: Один ограниченный запрос к приватному локальному сокету.
class LocalVision:
    def __init__(self, path=None, timeout=6):
        self.path = path or os.getenv("TREED_VISION_SOCKET", str(Path.home() / "treed/vision/run/vision.sock"))
        self.timeout = timeout

    def send(self, sequence, frame, context=None, task="spaghetti"):
        if len(frame) > MAX_JPEG_BYTES:
            raise ValueError("Кадр превышает локальный лимит")
        request_id = str(uuid.uuid4())
        context = context if context is not None else {"frame_id": sequence}
        payload = json.dumps({"protocol": 1, "request_id": request_id, "task": task,
                              "context": context,
                              "jpeg_base64": base64.b64encode(frame).decode("ascii")},
                             separators=(",", ":")).encode() + b"\n"
        if len(payload) > 12 * 1024 * 1024:
            raise ValueError("Запрос превышает локальный лимит")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(self.timeout)
            conn.connect(self.path)
            conn.sendall(payload)
            data = bytearray()
            while len(data) <= 65536:
                chunk = conn.recv(65537 - len(data))
                if not chunk:
                    break
                if b"\n" in chunk:
                    data.extend(chunk.split(b"\n", 1)[0])
                    break
                data.extend(chunk)
        if not data or len(data) > 65536:
            raise ValueError("Слишком большой или пустой ответ локального сервиса")
        result = json.loads(data)
        if not isinstance(result, dict):
            raise ValueError("Локальный сервис вернул ответ не в виде объекта")
        if (result.get("protocol") != 1 or result.get("request_id") != request_id
                or result.get("task") != task or result.get("context") != context):
            raise ValueError("Локальный ответ не совпадает с запросом")
        required = {"worker_generation", "model_version", "verdict", "score", "threshold",
                    "boxes", "reason", "processing_ms"}
        if not required <= result.keys():
            raise ValueError("Локальный ответ не содержит полного IPC-контракта")
        if (not isinstance(result["worker_generation"], str)
                or not isinstance(result["model_version"], str)
                or not isinstance(result["verdict"], str)):
            raise ValueError("Некорректный локальный ответ")
        try:
            uuid.UUID(result["worker_generation"])
        except (ValueError, AttributeError):
            raise ValueError("Некорректное поколение локального worker") from None
        if (type(result["processing_ms"]) not in {int, float}
                or not math.isfinite(result["processing_ms"]) or result["processing_ms"] < 0
                or not isinstance(result["reason"], (str, type(None)))
                or not isinstance(result["boxes"], list) or len(result["boxes"]) > 10000
                or type(result["threshold"]) not in {int, float}
                or not math.isfinite(result["threshold"]) or not 0 <= result["threshold"] <= 1
                or result["score"] is not None and (
                    type(result["score"]) not in {int, float} or not math.isfinite(result["score"])
                )):
            raise ValueError("Некорректные поля ответа локального сервиса")
        if result["verdict"] == "unavailable":
            if result["score"] is not None or result["boxes"] or result["processing_ms"] != 0.0 or not result["reason"]:
                raise ValueError("Некорректный ответ недоступного локального сервиса")
            raise RuntimeError("Локальный анализ недоступен")
        if task == "spaghetti" and result["verdict"] not in {"spaghetti", "normal"}:
            raise ValueError("Недопустимый класс локального ответа")
        return result


# Блок 4: Адаптер к локальному счётчику Moonraker; G-code остаётся на принтере.
class Reaction:
    def __init__(self, url, frames_root, session_file, timeout):
        parsed = http_url(url)
        if parsed.path not in {"", "/"} or parsed.query or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Контур реакции должен использовать локальный Moonraker")
        self.url, self.timeout = url.rstrip("/"), timeout
        self.frames_root = Path(os.path.abspath(frames_root))
        self.session_file = Path(session_file)
        self.session = self.directory = self.frame = None
        self.files = deque()
        self.prefix = f"detect_{uuid.uuid4().hex}"
        self.sequence = 0
        self.reset_pending = False

    def request(self, endpoint, data=None):
        body = json.dumps(data).encode() if data is not None else None
        headers = {"Content-Type": "application/json"}
        key = os.getenv("MOONRAKER_API_KEY")
        if key:
            headers["X-Api-Key"] = key
        with local_open(Request(self.url + endpoint, body, headers), timeout=self.timeout) as response:
            payload = response.read(65537)
        if len(payload) > 65536:
            raise ValueError("Слишком большой ответ Moonraker")
        return json.loads(payload)

    def prepare(self):
        status = self.request("/server/treed/detection/status")
        if not isinstance(status, dict) or type(status.get("active")) is not bool:
            raise ValueError("Moonraker не подтвердил состояние печати")
        session = status.get("session_id") if status["active"] else None
        if session != self.session:
            self.session, self.directory, self.frame = None, None, None
            self.reset_pending = True
        if not status["active"]:
            return False
        directory = Path(os.path.abspath(self.session_file.read_text(encoding="utf-8").strip()))
        if (directory.is_symlink() or directory.resolve().parent != self.frames_root or not directory.is_dir()
                or not isinstance(session, str) or not session.endswith(":" + directory.name)):
            raise ValueError("Каталог не соответствует активной сессии Moonraker")
        directory = directory.resolve()
        self.session, self.directory = session, directory
        if self.reset_pending:
            if not self.reset_series():
                return False
        return True

    def save_frame(self, frame):
        if self.session is None or frame is None:
            return
        if self.directory.resolve().parent != self.frames_root:
            raise ValueError("Каталог сессии изменился")
        self.frame = frame
        self.sequence += 1
        path = self.directory / f"{self.prefix}_{self.sequence:012d}.jpg"
        self._prune_owned_frames(len(frame))
        with path.open("xb") as stream:
            stream.write(frame)
        self.files.append(path)
        # ponytail: три последних кадра, полный видеоархив нужен только по отдельной задаче.
        while len(self.files) > 3:
            old = self.files.popleft()
            if old.resolve().parent.parent == self.frames_root:
                old.unlink(missing_ok=True)
        return path.name

    def _prune_owned_frames(self, incoming=0):
        # Удаляет только обычные файлы с точным адаптерным именем; обход не следует symlink.
        if (not self.frames_root.is_dir()
                or any(parent.is_symlink() for parent in (self.frames_root, *self.frames_root.parents))):
            raise ValueError("Корень кадров недоступен")
        candidates = []
        pattern = __import__("re").compile(r"^detect_[0-9a-f]{32}_[0-9]{12}\.jpg$")
        for current, dirs, names in os.walk(self.frames_root, followlinks=False):
            dirs[:] = [name for name in dirs if not (Path(current) / name).is_symlink()]
            for name in names:
                if not pattern.fullmatch(name):
                    continue
                candidate = Path(current) / name
                try:
                    info = candidate.lstat()
                except FileNotFoundError:
                    continue
                if candidate.is_symlink() or not candidate.is_file():
                    continue
                candidates.append((info.st_mtime_ns, str(candidate), candidate, info.st_size))
        total = sum(row[3] for row in candidates)
        for _mtime, _name, candidate, size in sorted(candidates):
            if total + incoming <= MAX_OWNED_FRAME_BYTES:
                break
            try:
                info = candidate.lstat()
                if candidate.is_symlink() or not candidate.is_file() or info.st_size != size:
                    continue
                candidate.unlink()
                total -= size
            except FileNotFoundError:
                total -= size
        if total + incoming > MAX_OWNED_FRAME_BYTES:
            raise ValueError("Недостаточно места для ограниченного набора кадров")

    def publish(self, result, frame):
        if self.session is None or frame is None:
            return
        if self.directory.resolve().parent != self.frames_root:
            raise ValueError("Каталог сессии изменился")
        frame_id = result["context"]["frame_id"]
        path = self.directory / frame_id
        if path.is_symlink() or path.resolve().parent != self.directory.resolve() or not path.is_file():
            raise ValueError("Кадр больше не принадлежит активной сессии")
        self.frame = frame
        result = self.request("/server/treed/detection/result", {
            "session_id": self.session, "frame_id": frame_id,
            "captured_at": result["captured_at"],
            "worker_generation": result["worker_generation"],
            "model_version": result["model_version"], "verdict": result["verdict"],
            "score": result["score"],
        })
        confirmed = (isinstance(result, dict) and result.get("pause_state") == "confirmed"
                     and isinstance(result.get("context"), dict)
                     and result["context"].get("session_id") == self.session)
        active = (isinstance(result, dict) and result.get("session_id") == self.session
                  and result.get("active") is True)
        if (not isinstance(result, dict) or not (active or confirmed)
                or type(result.get("counter")) is not int
                or not 0 <= result["counter"] <= 3
                or result.get("pause_state") not in {"idle", "requested", "confirmed", "failed"}):
            raise ValueError("Moonraker не подтвердил результат для активной сессии")
        self.reset_pending = False
        return result

    def reset_series(self):
        self.reset_pending = True
        if self.session is None:
            return False
        try:
            result = self.request("/server/treed/detection/reset", {"session_id": self.session})
            if (not isinstance(result, dict) or result.get("active") is not True
                    or result.get("session_id") != self.session or result.get("counter") != 0):
                raise ValueError("Moonraker не подтвердил сброс серии")
            self.reset_pending = False
            return True
        except (OSError, ValueError):
            return False

    def break_series(self):
        self.reset_series()


# Блок 5: Диагностический запуск или локальная реакция и восстановление после отказа.
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stream", default=os.getenv("TREED_CAM_STREAM_URL", "http://127.0.0.1:8080/?action=stream"))
    parser.add_argument("--frames", type=int, default=0, help="Число ответов до выхода; 0 — непрерывно")
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument("--react", action="store_true", help="Передавать результаты локальному treed_detection")
    parser.add_argument("--fps", type=float, default=5)
    parser.add_argument("--vision-socket", default=None)
    parser.add_argument("--moonraker", default=os.getenv("TREED_DETECT_MOONRAKER_URL", "http://127.0.0.1:7125"))
    parser.add_argument("--frames-root", type=Path, default=Path(__file__).resolve().parents[1] / "prints")
    parser.add_argument("--session-file", type=Path, default=Path("/tmp/treed_cam_session_dir"))
    args = parser.parse_args()
    if (args.frames < 0 or not math.isfinite(args.timeout) or args.timeout <= 0
            or not math.isfinite(args.fps) or not .2 <= args.fps <= 10):
        parser.error("Нужны неотрицательные --frames и положительный --timeout")
    camera = Camera(args.stream, args.timeout)
    reaction = Reaction(args.moonraker, args.frames_root, args.session_file, min(args.timeout, 2)) if args.react else None
    session = f"diagnostic-{uuid.uuid4().hex}"
    sent, previous, camera_revision, last_warning = 0, 0, 0, 0.0
    try:
        while not args.frames or sent < args.frames:
            try:
                vision = LocalVision(path=args.vision_socket, timeout=min(args.timeout, 6))
                print(json.dumps({"type": "ready", "protocol": 1, "socket": vision.path}), flush=True)
                if not camera.thread.is_alive():
                    camera.thread.start()
                while not args.frames or sent < args.frames:
                    if reaction is not None:
                        if camera.revision != camera_revision:
                            reaction.break_series()
                            camera_revision = camera.revision
                        if not reaction.prepare():
                            time.sleep(1)
                            continue
                    sequence, captured, frame = camera.next(previous)
                    previous_before_send, previous = previous, sequence
                    captured_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
                    frame_id = reaction.save_frame(frame) if reaction is not None else str(sequence)
                    context = {"session_id": reaction.session if reaction else session,
                               "frame_id": frame_id, "captured_at": captured_at}
                    result = vision.send(sequence, frame, context)
                    if result["verdict"] not in {"spaghetti", "normal"}:
                        raise ValueError("Недопустимый результат классификации спагетти")
                    result["spaghetti"] = result["verdict"] == "spaghetti"
                    if reaction is not None:
                        if time.monotonic() - captured > 6 or camera.revision != camera_revision:
                            raise TimeoutError("Результат относится к устаревшему потоку")
                        result["captured_at"] = captured_at
                        result["reaction"] = reaction.publish(result, frame)
                    result["roundtrip_ms"] = round((time.monotonic() - captured) * 1000, 1)
                    result["skipped_frames"] = sequence - previous_before_send - 1
                    print(json.dumps({"type": "result", **result}, ensure_ascii=False), flush=True)
                    sent += 1
                    time.sleep(max(0, 1 / args.fps - (time.monotonic() - captured)))
            except (OSError, ValueError, RuntimeError) as exc:
                if reaction is not None:
                    reaction.break_series()
                if time.monotonic() - last_warning >= 300:
                    print(f"Поток прерван: {type(exc).__name__}: {exc}", file=sys.stderr)
                    last_warning = time.monotonic()
                time.sleep(2)
    except KeyboardInterrupt:
        pass
    finally:
        if reaction is not None:
            reaction.break_series()
        camera.stop.set()
        if camera.thread.is_alive():
            camera.thread.join(timeout=args.timeout + 1)


if __name__ == "__main__":
    main()
