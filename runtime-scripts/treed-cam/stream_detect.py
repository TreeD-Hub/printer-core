"""Передача свежих кадров MJPEG на сервер и опциональная реакция на дефект.

Использует только стандартную библиотеку Python. Отказ сети не влияет на печать.
"""

import argparse
import http.client
import json
import math
import os
import sys
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

MAX_JPEG_BYTES = 10 * 1024 * 1024


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
        http_url(url)
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
                with urlopen(self.url, timeout=self.timeout) as stream:
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


# Блок 3: Handshake и один запрос на кадр по постоянному HTTP-соединению.
# ponytail: один запрос ограничивает FPS задержкой сети; конвейер нужен при требовании обработки всех кадров.
class Server:
    def __init__(self, url, key, session, timeout):
        parsed = http_url(url)
        if parsed.path not in {"", "/"} or parsed.query:
            raise ValueError("URL сервера должен указывать на корень API")
        connection = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        self.connection = connection(parsed.hostname, parsed.port, timeout=timeout)
        self.key, self.session = key, session
        self.hello = None

    def post(self, path, body, content_type):
        self.connection.request("POST", path, body, {
            "X-API-Key": self.key, "Content-Type": content_type,
        })
        response = self.connection.getresponse()
        data = response.read(65537)
        if len(data) > 65536:
            raise ValueError("Слишком большой ответ сервера")
        if response.status != 200:
            raise RuntimeError(f"Сервер вернул HTTP {response.status}")
        return json.loads(data)

    def handshake(self):
        self.hello = None
        hello = self.post("/stream/hello", json.dumps({
            "protocol_version": 1, "session_id": self.session,
        }).encode(), "application/json")
        if (hello.get("ready") is not True or hello.get("protocol_version") != 1
                or hello.get("session_id") != self.session
                or hello.get("model_kind") != "spaghetti_presence"
                or hello.get("frame_endpoint") != "/classify"
                or not isinstance(hello.get("model_version"), str) or not hello["model_version"]
                or not isinstance(hello.get("threshold"), (int, float))
                or not 0 < hello["threshold"] < 1
                or type(hello.get("max_jpeg_bytes")) is not int
                or not 0 < hello["max_jpeg_bytes"] <= MAX_JPEG_BYTES):
            raise ValueError("Сервер не подтвердил контракт потока")
        self.hello = hello
        return hello

    def send(self, sequence, frame):
        if self.hello is None:
            raise ValueError("Handshake не выполнен")
        if len(frame) > self.hello["max_jpeg_bytes"]:
            raise ValueError("Кадр превышает лимит сервера")
        boundary = uuid.uuid4().hex
        body = bytearray()
        for name, value in (("session_id", self.session), ("frame_id", str(sequence))):
            body.extend((f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n'
                         f'\r\n{value}\r\n').encode())
        body.extend((f'--{boundary}\r\nContent-Disposition: form-data; name="image"; '
                     'filename="frame.jpg"\r\nContent-Type: image/jpeg\r\n\r\n').encode())
        body.extend(frame)
        body.extend(f"\r\n--{boundary}--\r\n".encode())
        result = self.post("/classify", body, f"multipart/form-data; boundary={boundary}")
        score = result.get("score")
        if (result.get("session_id") != self.session or result.get("frame_id") != str(sequence)
                or result.get("protocol_version") != 1
                or result.get("model_version") != self.hello["model_version"]
                or result.get("threshold") != self.hello["threshold"]
                or type(result.get("spaghetti")) is not bool
                or type(score) not in {int, float} or not math.isfinite(score) or not 0 <= score <= 1
                or result["spaghetti"] != (score >= self.hello["threshold"])):
            raise ValueError("Ответ не соответствует кадру или handshake")
        return result


# Блок 4: Адаптер к локальному счётчику Moonraker; G-code остаётся на принтере.
class Reaction:
    def __init__(self, url, frames_root, session_file, timeout):
        parsed = http_url(url)
        if parsed.path not in {"", "/"} or parsed.query or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Контур реакции должен использовать локальный Moonraker")
        self.url, self.timeout = url.rstrip("/"), timeout
        self.frames_root, self.session_file = Path(frames_root).resolve(), Path(session_file)
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
        with urlopen(Request(self.url + endpoint, body, headers), timeout=self.timeout) as response:
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
        if not status["active"] or status.get("cancel_requested") is True:
            return False
        directory = Path(self.session_file.read_text(encoding="utf-8").strip()).resolve()
        if (directory.parent != self.frames_root or not directory.is_dir()
                or not isinstance(session, str) or not session.endswith(":" + directory.name)):
            raise ValueError("Каталог не соответствует активной сессии Moonraker")
        self.session, self.directory = session, directory
        if self.reset_pending and self.frame is not None:
            self.publish(None, self.frame)
        return True

    def publish(self, critical, frame):
        if self.session is None or frame is None:
            return
        if self.directory.resolve().parent != self.frames_root:
            raise ValueError("Каталог сессии изменился")
        self.frame = frame
        self.sequence += 1
        path = self.directory / f"{self.prefix}_{self.sequence:012d}.jpg"
        with path.open("xb") as stream:
            stream.write(frame)
        self.files.append(path)
        # ponytail: три последних кадра, полный видеоархив нужен только по отдельной задаче.
        while len(self.files) > 3:
            old = self.files.popleft()
            if old.resolve().parent.parent == self.frames_root:
                old.unlink(missing_ok=True)
        result = self.request("/server/treed/detection/result", {
            "session_id": self.session, "frame_id": path.name,
            "critical": critical, "defect": "spaghetti",
        })
        if (not isinstance(result, dict) or result.get("session_id") != self.session
                or result.get("active") is not True or type(result.get("counter")) is not int
                or not 0 <= result["counter"] <= 3 or type(result.get("cancel_requested")) is not bool):
            raise ValueError("Moonraker не подтвердил результат для активной сессии")
        self.reset_pending = False
        return result

    def break_series(self):
        self.reset_pending = True
        try:
            self.publish(None, self.frame)
        except (OSError, ValueError):
            # При недоступном Moonraker следующий положительный кадр ждёт сброса.
            print("Сброс серии пока не подтверждён Moonraker", file=sys.stderr)


# Блок 5: Диагностический запуск или локальная реакция и восстановление после отказа.
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default=os.getenv("TREED_DETECT_SERVER_URL"))
    parser.add_argument("--stream", default=os.getenv("TREED_CAM_STREAM_URL", "http://127.0.0.1:8080/?action=stream"))
    parser.add_argument("--frames", type=int, default=0, help="Число ответов до выхода; 0 — непрерывно")
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument("--react", action="store_true", help="Передавать результаты локальному treed_detection")
    parser.add_argument("--fps", type=float, default=5)
    parser.add_argument("--moonraker", default=os.getenv("TREED_DETECT_MOONRAKER_URL", "http://127.0.0.1:7125"))
    parser.add_argument("--frames-root", type=Path, default=Path(__file__).resolve().parents[1] / "prints")
    parser.add_argument("--session-file", type=Path, default=Path("/tmp/treed_cam_session_dir"))
    args = parser.parse_args()
    key = os.getenv("DETECT_API_KEY", "")
    if (not args.server or not key or args.frames < 0 or not math.isfinite(args.timeout) or args.timeout <= 0
            or not math.isfinite(args.fps) or not .2 <= args.fps <= 10):
        parser.error("Нужны --server, DETECT_API_KEY, неотрицательные --frames и положительный --timeout")
    camera = Camera(args.stream, args.timeout)
    reaction = Reaction(args.moonraker, args.frames_root, args.session_file, min(args.timeout, 2)) if args.react else None
    session = f"diagnostic-{uuid.uuid4().hex}"
    sent, previous, camera_revision = 0, 0, 0
    try:
        while not args.frames or sent < args.frames:
            server = Server(args.server, key, session, args.timeout)
            try:
                hello = server.handshake()
                if reaction is not None and hello["threshold"] != .3:
                    raise ValueError("Для автоотмены нужен серверный порог спагетти 0.3")
                print(json.dumps({"type": "ready", **hello}), flush=True)
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
                    if reaction is not None and reaction.reset_pending:
                        reaction.publish(None, frame)
                    result = server.send(sequence, frame)
                    if reaction is not None:
                        if time.monotonic() - captured >= 3 or camera.revision != camera_revision:
                            raise TimeoutError("Результат относится к устаревшему потоку")
                        result["reaction"] = reaction.publish(result["spaghetti"], frame)
                    result["roundtrip_ms"] = round((time.monotonic() - captured) * 1000, 1)
                    result["skipped_frames"] = sequence - previous_before_send - 1
                    print(json.dumps({"type": "result", **result}, ensure_ascii=False), flush=True)
                    sent += 1
                    time.sleep(max(0, 1 / args.fps - (time.monotonic() - captured)))
            except (OSError, ValueError, RuntimeError, http.client.HTTPException) as exc:
                if reaction is not None:
                    reaction.break_series()
                print(f"Поток прерван: {type(exc).__name__}: {exc}", file=sys.stderr)
                time.sleep(2)
            finally:
                server.connection.close()
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
