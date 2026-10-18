"""Локальная очередь AutoPrinter, синхронизация и исполнение при защитных условиях.

Контур: required для учёта очереди; запуск требует execution_enabled и свежего live-доказательства пустого стола.
"""

from __future__ import annotations

# Блок 1: зависимости только от стандартной библиотеки и соседнего хранилища.
import asyncio
import copy
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import random
import re
import stat
import sqlite3
import uuid
from typing import Any, Callable

from .treed_autoprinter_device import MoonrakerDevice
from .treed_autoprinter_bed import E0BedChecker
from .treed_autoprinter_execution import AutoPrinterExecutor
from .treed_autoprinter_store import AutoPrinterStore, StoreError
from .treed_autoprinter_transport import AutoPrinterTransport, TransportError

STATUS_ENDPOINT = "/server/treed/autoprinter/status"
PAIRING_ENDPOINT = "/server/treed/autoprinter/pairing"
CONTROL_ENDPOINT = "/server/treed/autoprinter/control"
MAX_CREDENTIAL_BYTES = 8192
LOGGER = logging.getLogger(__name__)


class TreeDAutoPrinter:
    def __init__(self, config) -> None:
        # Блок 2: выключенный компонент не открывает storage, task или сеть.
        self.server = config.get_server()
        self.enabled = config.getboolean("enabled", False)
        self.execution_enabled = config.getboolean("execution_enabled", False)
        self.bed_check_enabled = config.getboolean("bed_check_enabled", False)
        self._lock = asyncio.Lock()
        self._store = None
        self._transport = None
        self._executor = None
        self._device = None
        self._device_unavailable = False
        self._task = None
        self._execution_task = None
        self._execution_error = None
        self._bed_check_status = None
        self._workers: set[asyncio.Task] = set()
        self._closed = False
        self._config = config
        self._boot_id = str(uuid.uuid4())
        self._snapshot_seq = 0
        self._connection = "disabled" if not self.enabled else "unconfigured"
        self._recovery_required = False
        self.server.register_endpoint(STATUS_ENDPOINT, ["GET"], self._handle_status, wrap_result=False)
        self.server.register_endpoint(PAIRING_ENDPOINT, ["POST"], self._handle_pairing, wrap_result=False)
        self.server.register_endpoint(CONTROL_ENDPOINT, ["POST"], self._handle_control, wrap_result=False)
        self.server.register_event_handler("server:shutdown", self.close)

    async def component_init(self) -> None:
        if not self.enabled or self._closed:
            return
        try:
            self._store, self._transport = await self._run_blocking(self._create_configured)
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
            self._connection = "unconfigured"
            return
        if self._closed:
            await asyncio.to_thread(self._store.close)
            self._store = None
            return
        bed_checker = None
        if self.bed_check_enabled:
            try:
                bed_checker = E0BedChecker(
                    self._config.get("printer_id"),
                    self._config.get("bed_check_model_path", ""),
                    self._config.get("bed_check_python", ""),
                    self._config.get("bed_check_snapshot_url", "http://127.0.0.1:8080/?action=snapshot"),
                )
            except (OSError, ValueError, TypeError):
                self._bed_check_status = {
                    "model_version": "fdm-detail-e0-20261016-8de7e8aa22e2",
                    "allowed_model_versions": ["fdm-detail-e0-20261016-8de7e8aa22e2"],
                    "live_qualified": False,
                    "last_check": {"state": "unavailable", "reason": "configuration_invalid",
                                   "checked_at": None},
                }
        if self.execution_enabled:
            try:
                self._device = MoonrakerDevice(self.server)
            except Exception:
                self._device_unavailable = True
        self._executor = AutoPrinterExecutor(
            self._store, self._device, self._boot_id,
            enabled=self.execution_enabled and self._device is not None,
            bed_checker=bed_checker,
        )
        self._connection = "offline"
        self._task = asyncio.get_event_loop().create_task(self._sync_loop())
        self._execution_task = asyncio.get_event_loop().create_task(self._execution_loop())

    def _create_configured(self):
        # Блок 3: Секрет читается только из отдельного закрытого JSON-файла.
        printer_id = self._config.get("printer_id")
        profile_id = self._config.get("profile_id")
        credential_path = Path(self._config.get("credentials_path"))
        origin = self._config.get("api_origin", "")
        credentials = _read_credentials(credential_path)
        if credentials["printer_id"] != printer_id:
            raise ValueError("identity_mismatch")
        transport = AutoPrinterTransport(origin, credentials["secret"])
        store = AutoPrinterStore(Path(self._config.get("storage_path")), printer_id, profile_id)
        return store, transport

    async def _run_blocking(self, callback: Callable, *args):
        worker = asyncio.create_task(asyncio.to_thread(callback, *args))
        self._workers.add(worker)
        worker.add_done_callback(self._workers.discard)
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            result = await asyncio.gather(worker, return_exceptions=True)
            if callback == self._create_configured and result and not isinstance(result[0], BaseException):
                await asyncio.to_thread(result[0][0].close)
            elif (self._transport is not None and callback == getattr(self._transport, "download", None)
                  and result and not isinstance(result[0], BaseException)):
                await asyncio.to_thread(result[0].close)
            raise

    async def _handle_status(self, web_request) -> dict[str, Any]:
        # Блок 4: endpoint использует стандартную авторизацию Moonraker.
        if self._store is None or self._closed:
            return {"enabled": self.enabled, "configured": False, "connection": self._connection,
                    "binding_state": "unpaired", "physical_execution_enabled": False,
                    "execution_enabled": False, "queue_recovery_required": False,
                    "blocked_reason": "device_unavailable" if self._device_unavailable else None}
        async with self._lock:
            if self._closed or self._store is None:
                return {"enabled": self.enabled, "configured": False, "connection": self._connection,
                        "binding_state": "unpaired", "physical_execution_enabled": False,
                        "execution_enabled": False, "queue_recovery_required": self._recovery_required,
                        "blocked_reason": "queue_recovery_required" if self._recovery_required else None}
            status = await self._run_blocking(self._store.status)
            execution = await self._executor.status() if self._executor is not None else {}
            if status.get("binding_state") == "paired":
                checker = self._executor.bed_checker if self._executor is not None else None
                if checker is not None:
                    execution["bed_check"] = checker.status()
                elif self._bed_check_status is not None:
                    execution["bed_check"] = dict(self._bed_check_status)
        if self._device_unavailable:
            execution["blocked_reason"] = "device_unavailable"
            execution["execution_enabled"] = False
        elif self._execution_error is not None:
            execution["blocked_reason"] = self._execution_error
        elif (self._executor is not None and self._executor.enabled
              and self._executor.bed_checker is None and execution.get("blocked_reason") is None):
            execution["blocked_reason"] = "bed_proof_unavailable"
        status.update({"enabled": True, "configured": True, "connection": self._connection,
                       "physical_execution_enabled": False,
                       "queue_recovery_required": self._recovery_required,
                       **execution,
                       "blocked_reason": ("queue_recovery_required" if self._recovery_required
                                          else execution.get("blocked_reason"))})
        return status

    async def _handle_control(self, web_request) -> dict[str, Any]:
        # Блок 5: endpoint принимает только локальные действия из ограниченного списка.
        if self._closed or self._store is None or self._executor is None:
            raise self.server.error("AutoPrinter is unavailable", 503)
        args = web_request.get_args()
        if (not isinstance(args, dict) or not isinstance(args.get("type"), str) or args["type"] not in {
                "pause_queue", "resume_queue", "stop_after_current", "pause_current_print",
                "cancel_current_print", "recheck_bed"}):
            raise self.server.error("Invalid AutoPrinter control action", 400)
        kind = args["type"]
        physical = kind in {"pause_current_print", "cancel_current_print"}
        expected = {"type", "run_id", "action_id"} if physical else {"type"}
        if (set(args) != expected or (physical and not (
                _is_uuid(args["run_id"]) and _is_uuid(args["action_id"])) )):
            raise self.server.error("Invalid AutoPrinter control payload", 400)
        if physical and not self._executor.enabled:
            raise self.server.error("Physical execution is disabled", 503)
        async with self._lock:
            if self._closed:
                raise self.server.error("AutoPrinter is unavailable", 503)
            try:
                return await self._executor.local_action(args.copy())
            except StoreError:
                raise self.server.error("AutoPrinter control action is unavailable", 409) from None

    async def _handle_pairing(self, web_request) -> dict[str, Any]:
        # Блок 6: возвращается только одноразовый код, credential остаётся локальным.
        if self._closed or self._store is None or self._transport is None:
            raise self.server.error("AutoPrinter is unavailable", 503)
        async with self._lock:
            if self._closed:
                raise self.server.error("AutoPrinter is unavailable", 503)
            try:
                result = await self._run_blocking(self._transport.pairing)
            except TransportError as error:
                raise self.server.error("AutoPrinter pairing is unavailable", 503) from None
            code, expires_at = result.get("code"), result.get("expires_at")
            if not isinstance(code, str) or not 12 <= len(code) <= 128 or not _future_time(expires_at):
                raise self.server.error("AutoPrinter pairing response is invalid", 502)
            return {"code": code, "expires_at": expires_at}

    async def _sync_loop(self) -> None:
        # Блок 7: последовательный sync с ограниченным backoff без логирования деталей.
        delay = 2
        while True:
            if self._recovery_required:
                self._connection = "blocked"
                await asyncio.sleep(60)
                continue
            try:
                await self._sync_once()
                self._connection = "online"
                delay = 2
            except TransportError as error:
                if error.status in {401, 409}:
                    try:
                        async with self._lock:
                            if self._executor is not None:
                                await self._executor.transport_error(error.status)
                            if error.status == 401:
                                await self._run_blocking(self._mark_revoked)
                        if error.status == 401:
                            self._connection = "revoked"
                        else:
                            self._recovery_required = True
                            self._connection = "blocked"
                    except StoreError as store_error:
                        if store_error.code == "queue_recovery_required":
                            self._recovery_required = True
                            self._connection = "blocked"
                        else:
                            self._connection = "offline"
                    except sqlite3.Error:
                        self._connection = "offline"
                        delay = min(delay * 2, 60)
                else:
                    self._connection = "unavailable" if error.status == 503 else "offline"
                delay = min(delay * 2, 60)
            except StoreError as error:
                if error.code == "queue_recovery_required":
                    self._recovery_required = True
                    self._connection = "blocked"
                else:
                    self._connection = "offline"
                delay = min(delay * 2, 60)
            except sqlite3.Error:
                self._connection = "offline"
                delay = min(delay * 2, 60)
            except (ValueError, TypeError, KeyError, OSError):
                self._connection = "offline"
                delay = min(delay * 2, 60)
            await asyncio.sleep(random.uniform(delay * 0.8, delay * 1.2))

    async def _execution_loop(self) -> None:
        # Блок 8: короткий execution poll отделён от сетевого sync/backoff.
        while not self._closed:
            try:
                async with self._lock:
                    await self._executor.tick()
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._execution_error = "execution_loop_error"
                LOGGER.error("AutoPrinter execution loop stopped (%s)", type(error).__name__)
                return

    def _mark_revoked(self) -> None:
        session = self._store.session()
        self._store.update_binding(session["control_epoch"], session["binding_epoch"],
                                   "revoked", session["queue_revision"])

    async def close(self) -> None:
        self._closed = True
        tasks = [task for task in (self._task, self._execution_task) if task is not None and not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        while self._workers:
            await asyncio.gather(*tuple(self._workers), return_exceptions=True)
        async with self._lock:
            while self._workers:
                await asyncio.gather(*tuple(self._workers), return_exceptions=True)
            if self._store is not None:
                await asyncio.to_thread(self._store.close)
                self._store = None

    async def _sync_once(self) -> None:
        async with self._lock:
            session = await self._run_blocking(self._store.session)
            if session.get("queue_recovery_required"):
                self._recovery_required = True
                self._connection = "blocked"
                raise StoreError("queue_recovery_required")
            execution_fields = await self._executor.sync_fields() if self._executor is not None else None
            self._snapshot_seq += 1
            payload = {
                "protocol_version": 1, "printer_id": self._store.printer_id,
                "control_epoch": session["control_epoch"],
                "agent_boot_id": execution_fields["agent_boot_id"] if execution_fields else self._boot_id,
                "snapshot_seq": execution_fields["snapshot_seq"] if execution_fields else self._snapshot_seq,
                "snapshot": execution_fields["snapshot"] if execution_fields else _snapshot(),
                "events": execution_fields["events"] if execution_fields else [],
                "command_results": (execution_fields["command_results"] if execution_fields
                                     else await self._run_blocking(self._store.pending_results, 20)),
                "acknowledged_event_ids": (execution_fields["acknowledged_event_ids"]
                                            if execution_fields else []),
            }
            submitted = copy.deepcopy(payload)
            submitted["binding_epoch"] = session["binding_epoch"]
            response = await self._run_blocking(self._transport.sync, payload)
            _validate_response(response, self._store.printer_id, self._store.profile_id)
            mismatch = response.get("reason") == "control_epoch_mismatch"
            changed_control = response["control_epoch"] != session["control_epoch"]
            revoked = (response["binding_state"] == "revoked"
                       and response["control_epoch"] >= session["control_epoch"]
                       and response["binding_epoch"] >= session["binding_epoch"]
                       and not response["commands"] and not response["transfers"]
                       and response.get("reason") is None)
            if ((mismatch and not changed_control)
                    or (not mismatch and changed_control and not revoked)):
                raise ValueError("invalid_control_epoch")
            await self._run_blocking(
                self._store.update_binding, response["control_epoch"], response["binding_epoch"],
                response["binding_state"], response["applied_queue_revision"],
            )
            if self._executor is not None:
                await self._executor.acknowledge(response, submitted)
            if mismatch or revoked:
                return
            if response["binding_state"] == "paired":
                for transfer in response["transfers"]:
                    if transfer["download_path"] != f"api/autoprinter/device/files/{transfer['asset_id']}":
                        raise ValueError("invalid_download_path")
                    stream = await self._run_blocking(self._transport.download, transfer["download_path"])
                    try:
                        saved = await self._run_blocking(self._store.store_asset, transfer, stream)
                    finally:
                        stream.close()
                    receipt = {"sha256": saved["sha256"], "size_bytes": saved["size_bytes"],
                               "control_epoch": response["control_epoch"]}
                    accepted = await self._run_blocking(
                        self._transport.receipt, saved["asset_id"], receipt
                    )
                    _validate_receipt(
                        accepted, saved["asset_id"], self._store.printer_id,
                        response["binding_epoch"], self._store.profile_id,
                        saved["sha256"], saved["size_bytes"],
                    )
                    await self._run_blocking(self._store.confirm_receipt, saved["asset_id"])
                for pending in await self._run_blocking(self._store.pending_receipts, 100):
                    receipt = {"sha256": pending["sha256"], "size_bytes": pending["size_bytes"],
                               "control_epoch": response["control_epoch"]}
                    accepted = await self._run_blocking(
                        self._transport.receipt, pending["asset_id"], receipt
                    )
                    _validate_receipt(
                        accepted, pending["asset_id"], self._store.printer_id,
                        response["binding_epoch"], self._store.profile_id,
                        pending["sha256"], pending["size_bytes"],
                    )
                    await self._run_blocking(self._store.confirm_receipt, pending["asset_id"])
                for command in response["commands"]:
                    normalized = _validate_command(command, response)
                    if (self._executor is not None and (
                            self._executor.enabled or normalized["type"] not in {
                                "pause_current_print", "cancel_current_print"})):
                        await self._executor.command(normalized)
                    else:
                        await self._run_blocking(self._store.apply_command, normalized)
            if (response["control_epoch"] == session["control_epoch"]
                    and response["binding_epoch"] == session["binding_epoch"]
                    and response["binding_state"] == "paired"
                    and response.get("reason") is None):
                ids = [item["command_id"] for item in payload["command_results"]]
                if ids:
                    await self._run_blocking(self._store.ack_results, ids)


# Блок 8: Валидация ответа и неизменяемое состояние без обращения к Klippy.
def _read_credentials(path: Path) -> dict[str, str]:
    before = path.lstat()
    if path.is_symlink():
        raise ValueError("credential_link_rejected")
    info = before
    if not stat.S_ISREG(info.st_mode) or not 1 <= info.st_size <= MAX_CREDENTIAL_BYTES:
        raise ValueError("credential_file_invalid")
    if os.name != "nt" and info.st_mode & 0o077:
        raise ValueError("credential_permissions_invalid")
    if os.name != "nt" and info.st_uid != os.geteuid():
        raise ValueError("credential_owner_invalid")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        opened = os.fstat(stream.fileno())
        identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns,
                                 item.st_uid, stat.S_IMODE(item.st_mode))
        if not stat.S_ISREG(opened.st_mode) or identity(opened) != identity(before):
            raise ValueError("credential_file_changed")
        if os.name != "nt" and opened.st_uid != os.geteuid():
            raise ValueError("credential_owner_invalid")
        if os.name != "nt" and opened.st_mode & 0o077:
            raise ValueError("credential_permissions_invalid")
        raw = stream.read(MAX_CREDENTIAL_BYTES + 1)
        if len(raw) != opened.st_size or identity(opened) != identity(os.fstat(stream.fileno())):
            raise ValueError("credential_file_changed")
    value = json.loads(raw.decode("utf-8"))
    if (not isinstance(value, dict) or set(value) != {"printer_id", "secret"}
            or not isinstance(value["printer_id"], str) or not isinstance(value["secret"], str)
            or not 32 <= len(value["secret"]) <= 512):
        raise ValueError("credential_file_invalid")
    return value


def _snapshot() -> dict[str, Any]:
    return {"observed_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "bed_epoch": 0, "queue_control": "paused", "current_run_id": None,
            "run_state": None, "local_pause": True, "ready": False, "print_paused": False,
            "capabilities": {"bed_check": False}, "model_version": None}


def _validate_receipt(value: object, asset_id: str, printer_id: str, binding_epoch: int,
                      profile_id: str, sha256: str, size_bytes: int) -> None:
    fields = {"storage_state", "asset_id", "printer_id", "binding_epoch", "sha256",
              "size_bytes", "profile_id"}
    if (not isinstance(value, dict) or not fields <= value.keys()
            or value["storage_state"] != "stored" or value["asset_id"] != asset_id
            or value["printer_id"] != printer_id or value["binding_epoch"] != binding_epoch
            or value["sha256"] != sha256 or value["size_bytes"] != size_bytes
            or value["profile_id"] != profile_id):
        raise ValueError("invalid_receipt")


def _validate_response(value: object, printer_id: str, profile_id: str) -> None:
    fields = {"protocol_version", "printer_id", "control_epoch", "binding_epoch", "binding_state",
              "state_version", "queue_revision", "applied_queue_revision", "execution_authority",
              "storage_authority", "configuration_version", "transfers", "commands", "snapshot",
              "ack_event_ids", "ignored_event_ids"}
    if (not isinstance(value, dict) or not fields <= value.keys()
            or type(value["protocol_version"]) is not int or value["protocol_version"] != 1
            or value["printer_id"] != printer_id or value["execution_authority"] != "printer"
            or value["storage_authority"] != "printer"
            or type(value["configuration_version"]) is not int or value["configuration_version"] != 1
            or value["binding_state"] not in {"unpaired", "paired", "revoked"}
            or any(type(value[key]) is not int or value[key] < 1 for key in
                   ("control_epoch", "binding_epoch", "state_version", "queue_revision", "applied_queue_revision"))
            or value["applied_queue_revision"] > value["queue_revision"]
            or not isinstance(value["transfers"], list) or len(value["transfers"]) > 100
            or not isinstance(value["commands"], list) or len(value["commands"]) > 20
            or not isinstance(value["snapshot"], dict)
            or not isinstance(value["ack_event_ids"], list) or len(value["ack_event_ids"]) > 100
            or not isinstance(value["ignored_event_ids"], list) or len(value["ignored_event_ids"]) > 100):
        raise ValueError("invalid_sync_response")
    if value.get("reason") not in (None, "control_epoch_mismatch"):
        raise ValueError("invalid_sync_response")
    if (value["snapshot"].get("printer_id") != printer_id
            or value["snapshot"].get("profile_id") != profile_id):
        raise ValueError("invalid_sync_response")
    for item in value["transfers"]:
        if (not isinstance(item, dict) or not isinstance(item.get("asset_id"), str)
                or not _is_uuid(item["asset_id"])
                or not isinstance(item.get("sha256"), str)
                or re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) is None
                or type(item.get("size_bytes")) is not int or not 1 <= item["size_bytes"] <= 104857600
                or item.get("profile_id") != profile_id
                or item.get("download_path") != f"api/autoprinter/device/files/{item['asset_id']}"
                or not _future_time(item.get("expires_at"))):
            raise ValueError("invalid_transfer")
    for command in value["commands"]:
        if (not isinstance(command, dict) or not _is_uuid(command.get("command_id"))
                or not isinstance(command.get("type"), str) or not command["type"]
                or type(command.get("control_epoch")) is not int or command["control_epoch"] < 1
                or not _valid_time(command.get("expires_at"))
                or not isinstance(command.get("payload", {}), dict)):
            raise ValueError("invalid_command")
        if value.get("reason") is None and (
                command["control_epoch"] != value["control_epoch"]
                or (command["type"] in {"enqueue_series", "reorder_queue"}
                    and command["payload"].get("binding_epoch") != value["binding_epoch"])):
            raise ValueError("invalid_command")
        if command["type"] in {"enqueue_series", "reorder_queue"}:
            payload = command["payload"]
            previous, revision = payload.get("previous_queue_revision"), payload.get("queue_revision")
            if (type(previous) is not int or type(revision) is not int or revision != previous + 1
                    or type(command.get("queue_revision")) is not int or command["queue_revision"] != revision):
                raise ValueError("invalid_command")


def _validate_command(command: object, response: dict[str, Any]) -> dict[str, Any]:
    if (not isinstance(command, dict) or not isinstance(command.get("command_id"), str)
            or command.get("control_epoch") != response["control_epoch"]
            or not _valid_time(command.get("expires_at"))):
        raise ValueError("invalid_command")
    kind = command.get("type")
    payload = command.get("payload", {})
    if not isinstance(payload, dict):
        raise ValueError("invalid_command")
    if kind in {"enqueue_series", "reorder_queue"}:
        if payload.get("binding_epoch") != response["binding_epoch"]:
            raise ValueError("invalid_command")
    return command


def _is_uuid(value: object) -> bool:
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value.lower()
    except ValueError:
        return False


def _valid_time(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        expiry = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return expiry.tzinfo is not None
    except ValueError:
        return False


def _future_time(value: object) -> bool:
    if not _valid_time(value):
        return False
    expiry = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return expiry > datetime.now(timezone.utc)


def load_component(config):
    return TreeDAutoPrinter(config)
