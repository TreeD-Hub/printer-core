"""
MOONRAKER COMPONENT: TREED HOST NETWORK
=======================================
Назначение:
- Предоставляет TreeD Shell host-side Wi-Fi endpoints поверх nmcli.
- Возвращает общий HostNetworkStatus без UI-сортировки и выбора сети.
Контур:
- best-effort для операций nmcli: endpoint отвечает статусом и message;
- transport-level ошибка остается только для отсутствующего компонента/API.
"""

from __future__ import annotations

# Блок 1: Импорты и базовые типы.
import asyncio
import logging
import os
import re
import shutil
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Set, TYPE_CHECKING

LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..confighelper import ConfigHelper


# Блок 2: Внутренняя модель результата nmcli.
@dataclass
class NmcliResult:
    returncode: int
    stdout: str
    stderr: str


class TreeDHostNetwork:
    def __init__(self, config: ConfigHelper) -> None:
        # Блок 3: Регистрация публичных endpoints для TreeD Shell.
        self.server = config.get_server()
        self.server.register_endpoint(
            "/server/treed/network/status",
            ["GET"],
            self._handle_status,
            wrap_result=False,
        )
        self.server.register_endpoint(
            "/server/treed/network/scan",
            ["POST"],
            self._handle_scan,
            wrap_result=False,
        )
        self.server.register_endpoint(
            "/server/treed/network/connect",
            ["POST"],
            self._handle_connect,
            wrap_result=False,
        )
        self.server.register_endpoint(
            "/server/treed/network/forget",
            ["POST"],
            self._handle_forget,
            wrap_result=False,
        )

    async def _handle_status(self, _web_request: object) -> Dict[str, Any]:
        # Блок 4: Read-only статус без принудительного rescan.
        return await self._read_status()

    async def _handle_scan(self, _web_request: object) -> Dict[str, Any]:
        # Блок 5: nmcli ждет завершения scan и возвращает его фактический список.
        return await self._read_status("scan complete", rescan=True)

    async def _handle_connect(self, web_request: object) -> Dict[str, Any]:
        # Блок 6: Подключение к сети по ssid/password из WebRequest.
        ssid = _request_string(web_request, "ssid").strip()
        if not ssid:
            raise self.server.error("ssid is required")

        password = _request_value(web_request, "password", None)
        args: List[str] = ["device", "wifi", "connect", ssid]
        if password is not None and str(password):
            args.extend(["password", str(password)])

        result = await self._run_nmcli(*args)
        if result.returncode != 0:
            return await self._read_status(_nmcli_message("connect failed", result))
        return await self._read_status("connected")

    async def _handle_forget(self, web_request: object) -> Dict[str, Any]:
        # Блок 7: Удаление saved connection по ssid/connection name.
        ssid = _request_string(web_request, "ssid").strip()
        if not ssid:
            raise self.server.error("ssid is required")

        result = await self._run_nmcli("connection", "delete", ssid)
        if result.returncode != 0:
            return await self._read_status(_nmcli_message("forget failed", result))
        return await self._read_status("forgotten")

    async def _read_status(
        self,
        message: Optional[str] = None,
        rescan: bool = False,
    ) -> Dict[str, Any]:
        # Блок 8: Сбор HostNetworkStatus из NetworkManager.
        if shutil.which("nmcli") is None:
            return _unavailable_status("nmcli unavailable")

        device_result = await self._run_nmcli(
            "-t", "--escape", "yes", "-f", "DEVICE,TYPE,STATE,CONNECTION",
            "device", "status",
        )
        if device_result.returncode != 0:
            return _unavailable_status(_nmcli_message("nmcli device status failed", device_result))

        wifi_devices = _parse_wifi_devices(device_result.stdout)
        if not wifi_devices:
            return _unavailable_status(message or "wifi device unavailable")

        active_device = _select_wifi_device(wifi_devices)
        ip_address = await self._read_ip_address(active_device.get("device"))
        saved_networks = await self._read_saved_networks()
        networks_result = await self._run_nmcli(
            "-t", "--escape", "yes", "-f", "ACTIVE,SSID,SIGNAL,SECURITY",
            "device", "wifi", "list", "--rescan", "yes" if rescan else "no",
        )

        networks: List[Dict[str, Any]] = []
        connected_ssid: Optional[str] = None
        status_message = message
        if networks_result.returncode == 0:
            networks = _parse_wifi_networks(networks_result.stdout, saved_networks)
            connected_ssid = _connected_ssid(networks)
        else:
            status_message = status_message or _nmcli_message("wifi list failed", networks_result)

        if connected_ssid is None:
            connection = active_device.get("connection")
            if connection and connection != "--":
                connected_ssid = connection

        return {
            "available": True,
            "ssid": connected_ssid,
            "ipAddress": ip_address,
            "message": status_message or ("connected" if connected_ssid else "disconnected"),
            "networks": networks,
        }

    async def _read_ip_address(self, device: Optional[str]) -> Optional[str]:
        # Блок 9: IP берется только для выбранного Wi-Fi device.
        if not device:
            return None
        result = await self._run_nmcli("-g", "IP4.ADDRESS", "device", "show", device)
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            value = _strip_cidr(line.strip())
            if value:
                return value
        return None

    async def _read_saved_networks(self) -> Set[str]:
        # Блок 10: Saved-флаг строится из NetworkManager connections.
        result = await self._run_nmcli(
            "-t", "--escape", "yes", "-f", "NAME,TYPE",
            "connection", "show",
        )
        if result.returncode != 0:
            return set()

        saved: Set[str] = set()
        for line in result.stdout.splitlines():
            fields = _split_nmcli_terse_line(line)
            if len(fields) < 2:
                continue
            name, connection_type = fields[0].strip(), fields[1].strip().lower()
            if name and ("wireless" in connection_type or "wifi" in connection_type):
                saved.add(name)
        return saved

    async def _run_nmcli(self, *args: str) -> NmcliResult:
        # Блок 11: nmcli запускается async, чтобы не блокировать event loop Moonraker.
        if shutil.which("nmcli") is None:
            return NmcliResult(127, "", "nmcli unavailable")

        env = os.environ.copy()
        env["LC_ALL"] = "C.UTF-8"
        try:
            process = await asyncio.create_subprocess_exec(
                "nmcli",
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, stderr = await process.communicate()
        except FileNotFoundError:
            return NmcliResult(127, "", "nmcli unavailable")
        except Exception as err:
            LOGGER.exception("treed_host_network: nmcli execution failed")
            return NmcliResult(1, "", str(err))

        return NmcliResult(
            process.returncode,
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
        )


# Блок 12: Pure helpers для парсинга nmcli и HostNetworkStatus.
def _request_string(web_request: object, key: str) -> str:
    get_str = getattr(web_request, "get_str")
    return str(get_str(key))


def _request_value(web_request: object, key: str, default: Any = None) -> Any:
    get_value = getattr(web_request, "get")
    return get_value(key, default)


def _unavailable_status(message: str) -> Dict[str, Any]:
    return {
        "available": False,
        "ssid": None,
        "ipAddress": None,
        "message": message,
        "networks": [],
    }


def _nmcli_message(prefix: str, result: NmcliResult) -> str:
    details = (result.stderr or result.stdout).strip()
    return f"{prefix}: {details}" if details else prefix


def _parse_wifi_devices(output: str) -> List[Dict[str, str]]:
    devices: List[Dict[str, str]] = []
    for line in output.splitlines():
        fields = _split_nmcli_terse_line(line)
        if len(fields) < 4:
            continue
        device, device_type, state, connection = [field.strip() for field in fields[:4]]
        if device_type.lower() == "wifi":
            devices.append({
                "device": device,
                "state": state.lower(),
                "connection": connection,
            })
    return devices


def _select_wifi_device(devices: Sequence[Dict[str, str]]) -> Dict[str, str]:
    for device in devices:
        if device.get("state") == "connected":
            return device
    return devices[0]


def _parse_wifi_networks(output: str, saved_networks: Set[str]) -> List[Dict[str, Any]]:
    networks: List[Dict[str, Any]] = []
    used_ids: Dict[str, int] = {}

    for index, line in enumerate(output.splitlines()):
        fields = _split_nmcli_terse_line(line)
        if len(fields) < 4:
            continue

        active, ssid, signal, security = [field.strip() for field in fields[:4]]
        if not ssid:
            continue
        network_id = _network_id(ssid, index, used_ids)
        networks.append({
            "id": network_id,
            "ssid": ssid,
            "signalPercent": _signal_percent(signal),
            "security": _normalize_security(security),
            "saved": ssid in saved_networks,
            "connected": active.lower() == "yes",
        })

    return networks


def _connected_ssid(networks: Sequence[Dict[str, Any]]) -> Optional[str]:
    for network in networks:
        if network.get("connected"):
            ssid = network.get("ssid")
            return str(ssid) if ssid is not None else None
    return None


def _network_id(ssid: str, index: int, used_ids: Dict[str, int]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", ssid.lower()).strip("-")
    if not base:
        base = f"network-{index + 1}"

    used_count = used_ids.get(base, 0)
    used_ids[base] = used_count + 1
    if used_count:
        return f"{base}-{used_count + 1}"
    return base


def _signal_percent(value: str) -> int:
    try:
        signal = int(value)
    except ValueError:
        return 0
    return max(0, min(100, signal))


def _normalize_security(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized or normalized == "--":
        return "open"
    if "wpa3" in normalized:
        return "wpa3"
    if "wpa" in normalized or "wep" in normalized:
        return "wpa2"
    return "wpa2"


def _strip_cidr(value: str) -> Optional[str]:
    if not value:
        return None
    return value.split("/", 1)[0]


def _split_nmcli_terse_line(line: str) -> List[str]:
    fields: List[str] = []
    current: List[str] = []
    escaped = False

    for char in line:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(char)

    if escaped:
        current.append("\\")
    fields.append("".join(current))
    return fields


# Блок 13: Entry-point загрузки компонента Moonraker.
def load_component(config: ConfigHelper) -> TreeDHostNetwork:
    return TreeDHostNetwork(config)
