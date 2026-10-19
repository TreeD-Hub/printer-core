"""Bounded HTTPS transport for the disabled-by-default AutoPrinter adapter.
Контур: required для синхронизации; не следует redirect и не принимает URL клиента.
"""

from __future__ import annotations

import json
from contextlib import closing
import re
import time
from typing import Any
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


# Блок 1: Жёсткие лимиты запроса, JSON и бинарной передачи.
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_TRANSFER_BYTES = 100 * 1024 * 1024
TIMEOUT_SECONDS = 10
TOTAL_TIMEOUT_SECONDS = 120
PAIRING_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class TransportError(ValueError):
    def __init__(self, code: str, status: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        raise TransportError("redirect_rejected", code)


class _BoundedResponse:
    def __init__(self, response, limit: int, deadline: float) -> None:
        self._response = response
        self._limit = limit
        self._deadline = deadline
        self._read = 0

    def read(self, size: int = -1) -> bytes:
        if time.monotonic() >= self._deadline:
            self.close()
            raise TransportError("request_timeout")
        size = 65536 if size < 0 else min(size, 65536)
        # read1 не пытается заполнить весь буфер, поэтому trickle-ответ не удерживает worker.
        chunk = self._response.read1(size)
        if time.monotonic() >= self._deadline:
            self.close()
            raise TransportError("request_timeout")
        self._read += len(chunk)
        if self._read > self._limit:
            self.close()
            raise TransportError("transfer_too_large")
        return chunk

    def close(self) -> None:
        self._response.close()


# Блок 2: Единственный HTTPS origin и безопасное выполнение запросов.
class AutoPrinterTransport:
    def __init__(self, origin: str, secret: str, frontend_origin: str | None = None) -> None:
        parsed = urlsplit(origin)
        try:
            parsed.port
        except ValueError:
            raise TransportError("invalid_origin") from None
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
            raise TransportError("invalid_origin")
        self._origin = f"https://{parsed.netloc}"
        frontend = urlsplit(frontend_origin or origin)
        try:
            frontend.port
        except ValueError:
            raise TransportError("invalid_frontend_origin") from None
        if (frontend.scheme != "https" or not frontend.hostname or frontend.username
                or frontend.password or frontend.query or frontend.fragment
                or frontend.path not in ("", "/")):
            raise TransportError("invalid_frontend_origin")
        self._frontend_origin = f"https://{frontend.netloc}"
        self._secret = secret
        self._opener = build_opener(ProxyHandler({}), _NoRedirect)

    def _request(self, path: str, method: str = "GET", body: bytes | None = None,
                 limit: int = MAX_JSON_BYTES) -> bytes:
        if (not path.startswith("api/autoprinter/device/") or path.startswith("//")
                or "?" in path or "#" in path or ".." in path or "\\" in path):
            raise TransportError("invalid_path")
        request = Request(self._origin + "/" + path, data=body, method=method, headers={
            "Authorization": f"Bearer {self._secret}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        try:
            with closing(self._opener.open(request, timeout=TIMEOUT_SECONDS)) as response:
                stream = _BoundedResponse(response, limit, time.monotonic() + 30)
                chunks = []
                total = 0
                while True:
                    chunk = stream.read(min(65536, limit - total + 1))
                    if not chunk:
                        return b"".join(chunks)
                    total += len(chunk)
                    if total > limit:
                        raise TransportError("response_too_large")
                    chunks.append(chunk)
        except HTTPError as error:
            raise TransportError("http_error", error.code) from None
        except (URLError, TimeoutError, OSError) as error:
            raise TransportError("network_error") from None

    def _json(self, path: str, method: str = "GET", value: dict | None = None) -> dict[str, Any]:
        data = self._request(path, method, json.dumps(value, separators=(",", ":")).encode() if value is not None else None)
        try:
            result = json.loads(data.decode("utf-8"))
        except (UnicodeError, ValueError):
            raise TransportError("invalid_json") from None
        if not isinstance(result, dict):
            raise TransportError("invalid_json")
        return result.get("data", result) if isinstance(result.get("data", result), dict) else result

    def sync(self, payload: dict) -> dict[str, Any]:
        return self._json("api/autoprinter/device/sync", "POST", payload)

    def pairing(self) -> dict[str, Any]:
        result = self._json("api/autoprinter/device/pairing", "POST")
        code, verification_url = result.get("code"), result.get("verification_url")
        if (not _valid_pairing_code(code) or not isinstance(verification_url, str)
                or not self._valid_verification_url(verification_url, code)):
            raise TransportError("invalid_pairing_response")
        return {"code": code, "expires_at": result.get("expires_at"),
                "verification_url": verification_url}

    def _valid_verification_url(self, value: str, code: str) -> bool:
        parsed = urlsplit(value)
        try:
            port = parsed.port
        except ValueError:
            return False
        expected = urlsplit(self._frontend_origin)
        origin_matches = (parsed.scheme == expected.scheme
                          and (parsed.hostname or "").lower() == (expected.hostname or "").lower()
                          and (port or 443) == (expected.port or 443))
        return (origin_matches and parsed.path == "/autoprinter/connect"
                and not parsed.query and parsed.fragment == f"code={code}"
                and not parsed.username and not parsed.password)

    def unlink(self, binding_epoch: int, confirm: bool) -> dict[str, Any]:
        if type(binding_epoch) is not int or binding_epoch < 1 or type(confirm) is not bool:
            raise TransportError("invalid_unlink_request")
        return self._json("api/autoprinter/device/unlink", "POST",
                          {"binding_epoch": binding_epoch, "confirm": confirm})

    def receipt(self, asset_id: str, receipt: dict) -> dict[str, Any]:
        asset_id = _validate_asset_id(asset_id)
        return self._json(f"api/autoprinter/device/files/{asset_id}/receipt", "POST", receipt)

    # Блок 3: Потоковая загрузка с общим сроком и лимитом байтов.
    def download(self, path: str):
        match = re.fullmatch(r"api/autoprinter/device/files/([0-9a-fA-F-]{36})", path)
        if match is None:
            raise TransportError("invalid_download_path")
        _validate_asset_id(match.group(1))
        try:
            request = Request(self._origin + "/" + path, headers={"Authorization": f"Bearer {self._secret}"})
            response = self._opener.open(request, timeout=TIMEOUT_SECONDS)
            length = response.headers.get("Content-Length")
            if length is not None and (not length.isdigit() or int(length) > MAX_TRANSFER_BYTES):
                response.close()
                raise TransportError("transfer_too_large")
            return _BoundedResponse(response, MAX_TRANSFER_BYTES, time.monotonic() + TOTAL_TIMEOUT_SECONDS)
        except HTTPError as error:
            raise TransportError("http_error", error.code) from None
        except (URLError, TimeoutError, OSError) as error:
            raise TransportError("network_error") from None


def _validate_asset_id(value: str) -> str:
    try:
        if str(uuid.UUID(value)) == value.lower():
            return value
    except (ValueError, AttributeError, TypeError):
        pass
    raise TransportError("invalid_asset_id")


def _valid_pairing_code(value: Any) -> bool:
    return (isinstance(value, str) and value.isascii() and value == value.upper()
            and ((len(value) == 8 and all(char in PAIRING_ALPHABET for char in value))
                 or (len(value) == 20 and value.isalnum())))
