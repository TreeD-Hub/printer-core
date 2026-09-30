"""Передача свежих кадров MJPEG на сервер; диагностический контур без G-code.

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
from urllib.parse import urlsplit
from urllib.request import urlopen

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


# Блок 4: Явный диагностический запуск и восстановление после отказа.
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default=os.getenv("TREED_DETECT_SERVER_URL"))
    parser.add_argument("--stream", default=os.getenv("TREED_CAM_STREAM_URL", "http://127.0.0.1:8080/?action=stream"))
    parser.add_argument("--frames", type=int, default=0, help="Число ответов до выхода; 0 — непрерывно")
    parser.add_argument("--timeout", type=float, default=10)
    args = parser.parse_args()
    key = os.getenv("DETECT_API_KEY", "")
    if not args.server or not key or args.frames < 0 or not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("Нужны --server, DETECT_API_KEY, неотрицательные --frames и положительный --timeout")
    camera = Camera(args.stream, args.timeout)
    session = f"diagnostic-{uuid.uuid4().hex}"
    sent, previous = 0, 0
    try:
        while not args.frames or sent < args.frames:
            server = Server(args.server, key, session, args.timeout)
            try:
                print(json.dumps({"type": "ready", **server.handshake()}), flush=True)
                if not camera.thread.is_alive():
                    camera.thread.start()
                while not args.frames or sent < args.frames:
                    sequence, captured, frame = camera.next(previous)
                    result = server.send(sequence, frame)
                    result["roundtrip_ms"] = round((time.monotonic() - captured) * 1000, 1)
                    result["skipped_frames"] = sequence - previous - 1
                    print(json.dumps({"type": "result", **result}, ensure_ascii=False), flush=True)
                    previous = sequence
                    sent += 1
            except (OSError, ValueError, RuntimeError, http.client.HTTPException) as exc:
                print(f"Поток прерван: {type(exc).__name__}: {exc}", file=sys.stderr)
                time.sleep(2)
            finally:
                server.connection.close()
    except KeyboardInterrupt:
        pass
    finally:
        camera.stop.set()
        if camera.thread.is_alive():
            camera.thread.join(timeout=args.timeout + 1)


if __name__ == "__main__":
    main()
