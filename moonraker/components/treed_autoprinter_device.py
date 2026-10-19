"""MOONRAKER COMPONENT: TREED AUTOPRINTER DEVICE
Назначение: ограниченный адаптер приватного G-code и штатных Klippy API.
Контур: required для физического исполнения; неизвестное состояние блокирует запуск.
API сверены с Moonraker 1cfb0c41e468645951a371621f06d32777b6107c.
"""

from __future__ import annotations

# Блок 1: Проверка неизменности spool-файла и строгого контракта END_PRINT.
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
from typing import Any
import uuid


MAX_ASSET_BYTES = 100 * 1024 * 1024
MAX_SPOOL_BYTES = 1024 * 1024 * 1024
MIN_FREE_BYTES = 16 * 1024 * 1024
_HASH = re.compile(r"^[0-9a-f]{64}$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_END_PRINT = re.compile(r"^\s*(?:N\d+\s+)?END_PRINT(?:\s+|$)(.*)$", re.IGNORECASE)
_AUTO_REMOVE = re.compile(r"(?:^|\s)AUTO_REMOVE\s*=\s*([^\s]+)", re.IGNORECASE)


class DeviceError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class MoonrakerDevice:
    is_live = True

    def __init__(self, server: Any) -> None:
        self.server = server
        self.klippy = server.lookup_component("klippy_apis")
        self.file_manager = server.lookup_component("file_manager")

    # Блок 2: Снимок runtime, очереди и операций обслуживания с fail-closed gates.
    async def inspect(self) -> dict[str, Any]:
        # A missing optional component or incomplete API response must fail closed.
        result = _observation()
        try:
            queue = self.server.lookup_component("job_queue")
            objects = await self.klippy.query_objects({
                "print_stats": None, "virtual_sdcard": None,
                "gcode_macro _TREED_OPERATION_STATE": ["phase"],
            })
            stats = objects["print_stats"]
            sd = objects["virtual_sdcard"]
            macro = objects["gcode_macro _TREED_OPERATION_STATE"]
            update = self.server.lookup_component("treed_update")
            update_state = await asyncio.to_thread(_read_update_state, update.state_file)
            operation_phase = macro.get("phase") if isinstance(macro, dict) else None
            if operation_phase not in {"idle", "printing", "paused", "preparing", "auto_remove"} or update_state is None:
                result["phase"] = "service_state_unknown"
                return result
            result["service_active"] = update_state["busy"] or operation_phase in {"preparing", "auto_remove"}
            result["queue_empty"] = not queue.queued_jobs
            result["queue_safe"] = (
                not queue.load_on_start and not queue.automatic and not queue.queued_jobs
                and queue.queue_state in {"paused", "ready"}
            )
            if (not isinstance(stats, dict) or not isinstance(sd, dict)
                    or type(sd.get("is_active")) is not bool):
                return result
            filename = stats.get("filename")
            state = stats.get("state")
            if not isinstance(filename, str) or state not in {"standby", "printing", "paused", "complete", "error", "cancelled"}:
                return result
            result["filename"] = filename or None
            result["state"] = {"standby": "idle"}.get(state, state)
            if sd["is_active"] and result["state"] in {"idle", "complete", "error", "cancelled"}:
                result["phase"] = "virtual_sdcard_active"
                return result
            result["ready"] = True
            result["phase"] = operation_phase
        except Exception:
            return result
        return result

    # Блок 3: Валидация manifest и атомарная публикация неизменного G-code.
    async def prepare(self, run: dict[str, Any], asset_path: Path) -> str:
        return await _finish_thread_on_cancel(self._prepare, run, Path(asset_path))

    def _prepare(self, run: dict[str, Any], asset_path: Path) -> str:
        try:
            binding = run["binding_epoch"]
            run_id = run["run_id"]
            digest = run["sha256"]
            size = run["size_bytes"]
            profile = run["completion_profile"]
        except (KeyError, TypeError):
            raise DeviceError("invalid_run") from None
        if (type(binding) is not int or not 1 <= binding <= 9223372036854775807
                or not isinstance(run_id, str) or not _UUID.fullmatch(run_id)
                or not isinstance(digest, str) or not _HASH.fullmatch(digest)
                or type(size) is not int or not 1 <= size <= MAX_ASSET_BYTES
                or not isinstance(profile, dict) or set(profile) != {"version", "profile_id", "auto_remove"}
                or type(profile["version"]) is not int or profile["version"] != 1
                or not isinstance(profile["profile_id"], str) or run.get("profile_id") != profile["profile_id"]
                or type(profile["auto_remove"]) is not bool):
            raise DeviceError("invalid_run")
        source = _read_verified(asset_path, digest, size)
        auto_remove = _end_print_auto_remove(source)
        if auto_remove != profile["auto_remove"]:
            raise DeviceError("completion_profile_mismatch")
        root = self.file_manager.get_directory("gcodes")
        if not root:
            raise DeviceError("gcode_root_unavailable")
        root_path = Path(os.path.abspath(root))
        _reject_links(root_path)
        filename = f"treed-autoprinter/{binding}-{run_id}-{digest}.gcode"
        target = root_path / filename
        _reject_links(target.parent)
        try:
            if target.parent.exists() and not target.parent.is_dir():
                raise DeviceError("unsafe_storage_path")
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        except DeviceError:
            raise
        except OSError:
            raise DeviceError("spool_quota_unknown") from None
        _reject_links(target.parent)
        if target.exists() or target.is_symlink():
            if target.is_symlink() or _read_verified(target, digest, size) != source:
                raise DeviceError("spool_conflict")
            return filename
        _check_spool_quota(target.parent, size)
        temp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = None
        try:
            fd = os.open(temp, flags, 0o600)
            with os.fdopen(fd, "wb") as stream:
                fd = None
                stream.write(source)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temp, target, follow_symlinks=False)
            except FileExistsError:
                if _read_verified(target, digest, size) != source:
                    raise DeviceError("spool_conflict") from None
            _fsync_dir(target.parent)
            return filename
        except OSError:
            raise DeviceError("spool_write_failed") from None
        finally:
            if fd is not None:
                os.close(fd)
            try:
                temp.unlink()
            except FileNotFoundError:
                pass

    # Блок 4: Повторная проверка непосредственно перед физическими API-вызовами.
    async def start(self, filename: str) -> None:
        await self._recheck_file(filename)
        obs = await self.inspect()
        if (not obs["ready"] or not obs["queue_safe"] or obs["service_active"] or obs["phase"] != "idle"
                or obs["state"] not in {"idle", "complete", "cancelled", "error"}):
            raise DeviceError("runtime_not_safe")
        await self.klippy.start_print(filename, wait_klippy_started=False, user=None)

    async def pause(self) -> None:
        await self.klippy.pause_print()

    async def cancel(self) -> None:
        await self.klippy.cancel_print()

    # Блок 5: Связь только по уникальному имени задания; job_id не используется.
    async def reconcile(self, run: dict[str, Any]) -> dict[str, Any]:
        obs = await self.inspect()
        expected = run.get("filename")
        if not isinstance(expected, str) or not expected:
            obs["evidence_source"] = None
            return obs
        if obs["filename"] == expected and obs["state"] in {"printing", "paused", "complete", "error", "cancelled"}:
            obs["job_id"] = None
            obs["evidence_source"] = "runtime"
            return obs
        obs["state"] = "unknown"
        obs["ready"] = False
        obs["evidence_source"] = None
        return obs

    async def _recheck_file(self, filename: str) -> None:
        await _finish_thread_on_cancel(self._recheck_file_sync, filename)

    def _recheck_file_sync(self, filename: str) -> None:
        match = re.fullmatch(r"treed-autoprinter/([1-9][0-9]*)-([0-9a-f-]{36})-([0-9a-f]{64})\.gcode", filename) if isinstance(filename, str) else None
        if not match or not _UUID.fullmatch(match.group(2)):
            raise DeviceError("invalid_filename")
        root = self.file_manager.get_directory("gcodes")
        if not root:
            raise DeviceError("gcode_root_unavailable")
        root_path = Path(os.path.abspath(root))
        _reject_links(root_path)
        target = root_path / filename
        if os.path.commonpath((str(root_path), str(target)) ) != str(root_path):
            raise DeviceError("invalid_filename")
        _read_verified(target, match.group(3), target.stat().st_size)


def _observation() -> dict[str, Any]:
    return {"ready": False, "state": "unknown", "filename": None, "job_id": None,
            "queue_empty": False, "queue_safe": False, "service_active": True, "phase": None}


# Блок 7: Чтение ограниченного updater-состояния и подтверждение точных байтов.
def _read_update_state(path: Path) -> dict[str, Any] | None:
    try:
        _reject_links(path)
        if not path.exists():
            # treed_update._read_state() defines a missing state file as idle.
            return {"busy": False}
        if path.stat().st_size > 1024 * 1024:
            return None
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            value = json.loads(stream.read(1024 * 1024 + 1).decode("utf-8"))
        return value if isinstance(value, dict) and type(value.get("busy")) is bool else None
    except (OSError, UnicodeError, ValueError):
        return None


def _read_verified(path: Path, digest: str, size: int) -> bytes:
    try:
        _reject_links(path)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as stream:
            fd = None
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size != size or size > MAX_ASSET_BYTES:
                raise DeviceError("asset_integrity_error")
            chunks = []
            hasher = hashlib.sha256()
            total = 0
            while chunk := stream.read(65536):
                total += len(chunk)
                if total > size:
                    raise DeviceError("asset_integrity_error")
                hasher.update(chunk)
                chunks.append(chunk)
            if total != size or hasher.hexdigest() != digest:
                raise DeviceError("asset_integrity_error")
            return b"".join(chunks)
    except DeviceError:
        raise
    except OSError:
        raise DeviceError("unsafe_asset_path") from None


def _check_spool_quota(directory: Path, incoming_size: int) -> None:
    total_size = 0
    try:
        _reject_links(directory)
        if not directory.is_dir():
            raise DeviceError("unsafe_storage_path")
        with os.scandir(directory) as entries:
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                if entry.is_symlink() or not stat.S_ISREG(info.st_mode):
                    raise DeviceError("unsafe_storage_path")
                total_size += info.st_size
        free_bytes = shutil.disk_usage(directory).free
    except DeviceError:
        raise
    except OSError:
        raise DeviceError("spool_quota_unknown") from None
    if total_size + incoming_size > MAX_SPOOL_BYTES or free_bytes - incoming_size < MIN_FREE_BYTES:
        raise DeviceError("spool_quota_exceeded")


def _end_print_auto_remove(data: bytes) -> bool:
    # Блок 6: Macro-профиль должен быть однозначным; G-code не исправляется.
    found: list[bool] = []
    try:
        lines = data.decode("utf-8").splitlines()
    except UnicodeError:
        raise DeviceError("invalid_gcode") from None
    for line in lines:
        command = line.split(";", 1)[0].strip()
        match = _END_PRINT.fullmatch(command)
        if not match:
            continue
        values = _AUTO_REMOVE.findall(match.group(1))
        if len(values) > 1 or (values and values[0] not in {"0", "1"}):
            raise DeviceError("ambiguous_end_print")
        found.append(values[0] == "1" if values else False)
    if len(found) != 1:
        raise DeviceError("ambiguous_end_print")
    return found[0]


def _reject_links(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise DeviceError("unsafe_storage_path")


def _fsync_dir(path: Path) -> None:
    if os.name == "nt":
        return
    fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


async def _finish_thread_on_cancel(function, *args):
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        except Exception:
            pass
        raise
