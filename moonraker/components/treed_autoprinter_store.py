"""MOONRAKER COMPONENT: TREED AUTOPRINTER STORE
Назначение: долговременная локальная очередь и приватное хранилище G-code.
Контур: required для синхронизации; не выполняет физические действия.
"""

from __future__ import annotations

# Блок 1: Стандартная библиотека для атомарного spool и SQLite-журнала.
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, BinaryIO, Dict, List, Mapping
import uuid


class StoreError(ValueError):
    """Безопасная ошибка контракта с машинно-читаемым кодом."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class AutoPrinterStore:
    def __init__(
        self,
        root: Path,
        printer_id: str,
        profile_id: str,
        max_asset_bytes: int = 104857600,
        max_total_bytes: int = 1073741824,
        max_runs: int = 1000,
    ) -> None:
        # Блок 2: Валидация идентичности и закрытые каталоги конкретного принтера.
        self.printer_id = _uuid(printer_id)
        if not isinstance(profile_id, str) or not profile_id or len(profile_id) > 128:
            raise StoreError("invalid_profile")
        if any(type(v) is not int or v < 1 for v in (max_asset_bytes, max_total_bytes, max_runs)):
            raise StoreError("invalid_limit")
        self.profile_id = profile_id
        self.max_asset_bytes = max_asset_bytes
        self.max_total_bytes = max_total_bytes
        self.max_runs = max_runs
        requested_root = Path(os.path.abspath(root))
        for ancestor in reversed((requested_root, *requested_root.parents)):
            if _is_link(ancestor):
                raise StoreError("unsafe_storage_path")
        existed = requested_root.exists()
        requested_root.mkdir(parents=True, exist_ok=True)
        self.root = requested_root
        _private_dir(self.root)
        if not existed:
            _fsync_dir(self.root.parent)
        self._lock = threading.RLock()
        self._closed = False
        self._process_lock = _acquire_process_lock(self.root / "store.lock")
        self._conn = None
        try:
            self._initialize_database(profile_id)
        except Exception:
            try:
                if self._conn is not None:
                    self._conn.close()
            finally:
                _release_process_lock(self._process_lock)
            raise

    def _initialize_database(self, profile_id: str) -> None:
        # Блок 3: Схема и восстановление только зарегистрированных временных файлов.
        self.db_path = self.root / "store.sqlite3"
        if _is_link(self.db_path):
            raise StoreError("unsafe_storage_path")
        if not self.db_path.exists():
            descriptor = os.open(str(self.db_path), os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_BINARY", 0), 0o600)
            os.close(descriptor)
        else:
            _private_file(self.db_path)
        self._conn = sqlite3.connect(str(self.db_path), timeout=30, isolation_level=None, check_same_thread=False)
        _private_file(self.db_path)
        self._conn.execute("PRAGMA journal_mode=DELETE")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS assets (
                binding_epoch INTEGER NOT NULL, asset_id TEXT NOT NULL, sha256 TEXT NOT NULL,
                size_bytes INTEGER NOT NULL, path TEXT NOT NULL, confirmed INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(binding_epoch, asset_id));
            CREATE TABLE IF NOT EXISTS runs (
                binding_epoch INTEGER NOT NULL, run_id TEXT NOT NULL, payload TEXT NOT NULL,
                position INTEGER NOT NULL, PRIMARY KEY(binding_epoch, run_id));
            CREATE TABLE IF NOT EXISTS commands (
                command_id TEXT PRIMARY KEY, body_hash TEXT NOT NULL, state TEXT NOT NULL,
                result TEXT NOT NULL, binding_epoch INTEGER NOT NULL, reported INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS staging (
                binding_epoch INTEGER NOT NULL, asset_id TEXT NOT NULL, path TEXT NOT NULL,
                PRIMARY KEY(binding_epoch, asset_id));
            """
        )
        defaults = {"printer_id": self.printer_id, "profile_id": profile_id, "control_epoch": "1",
                    "binding_epoch": "1", "binding_state": "unpaired", "queue_revision": "1",
                    "applied_queue_revision": "1", "local_pause": "1"}
        with self._transaction():
            for key, value in defaults.items():
                self._conn.execute("INSERT OR IGNORE INTO meta(key,value) VALUES(?,?)", (key, value))
            if self._get("printer_id") != self.printer_id or self._get("profile_id") != profile_id:
                raise StoreError("identity_mismatch")
            stale = self._conn.execute("SELECT binding_epoch,asset_id,path FROM staging").fetchall()
            for epoch, asset_id, stored_path in stale:
                expected = self._binding_dir(epoch) / f".incoming-{asset_id}"
                if Path(stored_path) != expected:
                    raise StoreError("unsafe_storage_path")
                if _is_link(expected):
                    raise StoreError("unsafe_storage_path")
                if expected.exists():
                    expected.unlink()
                    _fsync_dir(expected.parent)
                self._conn.execute("DELETE FROM staging WHERE binding_epoch=? AND asset_id=?", (epoch, asset_id))
        _fsync_dir(self.root)

    def _transaction(self):
        class Transaction:
            def __init__(inner, store):
                inner.store = store
            def __enter__(inner):
                inner.store._lock.acquire()
                if inner.store._closed:
                    inner.store._lock.release()
                    raise StoreError("closed")
                try:
                    inner.store._conn.execute("BEGIN IMMEDIATE")
                except Exception:
                    inner.store._lock.release()
                    raise
                return inner.store._conn
            def __exit__(inner, kind, value, traceback):
                try:
                    if kind:
                        inner.store._conn.execute("ROLLBACK")
                    else:
                        try:
                            inner.store._conn.execute("COMMIT")
                        except Exception:
                            inner.store._conn.execute("ROLLBACK")
                            raise
                finally:
                    inner.store._lock.release()
        return Transaction(self)

    def _get(self, key: str) -> str:
        row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0]

    def _set(self, key: str, value: Any) -> None:
        self._conn.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))

    def _binding_dir(self, epoch: int) -> Path:
        parent = self.root / "bindings"
        if _is_link(parent):
            raise StoreError("unsafe_storage_path")
        parent_existed = parent.exists()
        parent.mkdir(exist_ok=True)
        _private_dir(parent)
        if not parent_existed:
            _fsync_dir(self.root)
        path = parent / str(epoch)
        if _is_link(path):
            raise StoreError("unsafe_storage_path")
        existed = path.exists()
        path.mkdir(parents=True, exist_ok=True)
        _private_dir(path)
        if not existed:
            _fsync_dir(parent)
        return path

    def session(self) -> Dict[str, Any]:
        with self._lock:
            return {"control_epoch": int(self._get("control_epoch")), "binding_epoch": int(self._get("binding_epoch")),
                    "binding_state": self._get("binding_state"), "queue_revision": int(self._get("queue_revision"))}

    def update_binding(self, control_epoch: int, binding_epoch: int, binding_state: str, applied_queue_revision: int) -> None:
        if any(type(v) is not int or v < 1 for v in (control_epoch, binding_epoch, applied_queue_revision)) or binding_state not in {"unpaired", "paired", "revoked"}:
            raise StoreError("invalid_binding")
        with self._transaction():
            current_control, current_binding = int(self._get("control_epoch")), int(self._get("binding_epoch"))
            if control_epoch < current_control or binding_epoch < current_binding:
                raise StoreError("epoch_regression")
            changed = binding_epoch != current_binding
            current_revision = int(self._get("queue_revision"))
            if binding_state == "revoked":
                self._set("control_epoch", control_epoch)
                self._set("binding_epoch", binding_epoch)
                self._set("binding_state", binding_state)
                self._set("applied_queue_revision", applied_queue_revision)
                self._set("local_pause", 1)
                if changed:
                    self._set("queue_revision", 1)
                return
            if (changed and applied_queue_revision > 1) or applied_queue_revision > current_revision:
                raise StoreError("queue_recovery_required")
            if self._get("binding_state") == "unpaired" and binding_state == "paired" and applied_queue_revision > 1:
                raise StoreError("queue_recovery_required")
            self._set("control_epoch", control_epoch)
            self._set("binding_epoch", binding_epoch)
            self._set("binding_state", binding_state)
            if changed:
                self._set("queue_revision", applied_queue_revision)
                self._set("applied_queue_revision", applied_queue_revision)
                self._set("local_pause", 1)
            else:
                self._set("applied_queue_revision", applied_queue_revision)

    def store_asset(self, transfer: dict, stream: BinaryIO) -> Dict[str, Any]:
        asset_id = _uuid(transfer.get("asset_id"))
        digest = transfer.get("sha256")
        size = transfer.get("size_bytes")
        if transfer.get("download_path", f"api/autoprinter/device/files/{asset_id}") != f"api/autoprinter/device/files/{asset_id}":
            raise StoreError("invalid_asset_path")
        if transfer.get("profile_id") != self.profile_id:
            raise StoreError("profile_mismatch")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise StoreError("invalid_hash")
        if type(size) is not int or size < 1 or size > self.max_asset_bytes:
            raise StoreError("asset_size_invalid")
        with self._lock:
            binding = int(self._get("binding_epoch"))
            if self._get("binding_state") != "paired":
                raise StoreError("not_paired")
            existing = self._conn.execute("SELECT sha256,size_bytes,path FROM assets WHERE binding_epoch=? AND asset_id=?", (binding, asset_id)).fetchone()
            if existing:
                expected = self._binding_dir(binding) / f"{asset_id}.gcode"
                if existing[:2] != (digest, size) or Path(existing[2]) != expected or _is_link(expected) or _file_digest(expected) != (digest, size):
                    raise StoreError("asset_conflict")
                return {"asset_id": asset_id, "sha256": digest, "size_bytes": size}
            total = self._conn.execute("SELECT COALESCE(SUM(size_bytes),0) FROM assets").fetchone()[0]
            bindings = self.root / "bindings"
            if bindings.exists():
                if _is_link(bindings):
                    raise StoreError("unsafe_storage_path")
                for binding_dir in bindings.iterdir():
                    if _is_link(binding_dir):
                        raise StoreError("unsafe_storage_path")
                for stored in bindings.glob("*/*.gcode"):
                    if _is_link(stored) or not stored.is_file():
                        raise StoreError("unsafe_storage_path")
                    if stored.parent.name == str(binding) and stored.name == f"{asset_id}.gcode":
                        continue
                    if self._conn.execute("SELECT 1 FROM assets WHERE path=?", (str(stored),)).fetchone() is None:
                        total += stored.stat().st_size
            if total + size > self.max_total_bytes:
                raise StoreError("quota_exceeded")
            directory = self._binding_dir(binding)
            final = directory / f"{asset_id}.gcode"
            staging = directory / f".incoming-{asset_id}"
            if _is_link(final):
                raise StoreError("unsafe_storage_path")
            if final.exists() and _file_digest(final) != (digest, size):
                raise StoreError("asset_conflict")
            if _is_link(staging) or staging.exists():
                raise StoreError("unsafe_storage_path")
            hasher = hashlib.sha256()
            written = 0
            try:
                with self._transaction():
                    self._conn.execute("INSERT INTO staging(binding_epoch,asset_id,path) VALUES(?,?,?)", (binding, asset_id, str(staging)))
                fd = os.open(str(staging), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, "wb") as out:
                    while True:
                        chunk = stream.read(min(1024 * 1024, size - written + 1))
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > size or written > self.max_asset_bytes:
                            raise StoreError("asset_size_mismatch")
                        hasher.update(chunk)
                        out.write(chunk)
                    out.flush()
                    os.fsync(out.fileno())
                if written != size:
                    raise StoreError("asset_size_mismatch")
                if hasher.hexdigest() != digest:
                    raise StoreError("hash_mismatch")
                with self._transaction():
                    if self._get("binding_state") != "paired" or int(self._get("binding_epoch")) != binding:
                        raise StoreError("binding_changed")
                    if final.exists():
                        staging.unlink()
                        _fsync_dir(directory)
                    else:
                        os.replace(staging, final)
                        _fsync_dir(directory)
                    self._conn.execute("INSERT INTO assets(binding_epoch,asset_id,sha256,size_bytes,path) VALUES(?,?,?,?,?)", (binding, asset_id, digest, size, str(final)))
                    self._conn.execute("DELETE FROM staging WHERE binding_epoch=? AND asset_id=?", (binding, asset_id))
                return {"asset_id": asset_id, "sha256": digest, "size_bytes": size}
            finally:
                if staging.exists():
                    staging.unlink()
                    _fsync_dir(directory)
                with self._transaction():
                    self._conn.execute("DELETE FROM staging WHERE binding_epoch=? AND asset_id=?", (binding, asset_id))

    def pending_receipts(self, limit: int = 100) -> List[Dict[str, Any]]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise StoreError("invalid_limit")
        with self._lock:
            binding = int(self._get("binding_epoch"))
            rows = self._conn.execute("SELECT asset_id,sha256,size_bytes,path FROM assets WHERE binding_epoch=? AND confirmed=0 ORDER BY asset_id LIMIT ?", (binding, limit)).fetchall()
            directory = self._binding_dir(binding)
            for row in rows:
                expected = directory / f"{row[0]}.gcode"
                if Path(row[3]) != expected or _is_link(expected) or _file_digest(expected) != (row[1], row[2]):
                    raise StoreError("asset_integrity_error")
            return [{"asset_id": r[0], "sha256": r[1], "size_bytes": r[2]} for r in rows]

    def confirm_receipt(self, asset_id: str) -> Dict[str, Any]:
        asset_id = _uuid(asset_id)
        with self._transaction():
            binding = int(self._get("binding_epoch"))
            row = self._conn.execute("SELECT sha256,size_bytes,path FROM assets WHERE binding_epoch=? AND asset_id=?", (binding, asset_id)).fetchone()
            expected = self._binding_dir(binding) / f"{asset_id}.gcode"
            if row is None:
                raise StoreError("asset_not_found")
            if Path(row[2]) != expected or _is_link(expected) or _file_digest(expected) != row[:2]:
                raise StoreError("asset_integrity_error")
            self._conn.execute("UPDATE assets SET confirmed=1 WHERE binding_epoch=? AND asset_id=?", (binding, asset_id))
            return {"asset_id": asset_id, "sha256": row[0], "size_bytes": row[1], "confirmed": True}

    def apply_command(self, command: dict) -> Dict[str, Any]:
        if not isinstance(command, dict):
            raise StoreError("invalid_command")
        command_id = _uuid(command.get("command_id"))
        if type(command.get("control_epoch")) is not int or command["control_epoch"] < 1:
            raise StoreError("invalid_command")
        stable_body = {key: value for key, value in command.items() if key != "state"}
        body_hash = hashlib.sha256(_json(stable_body).encode("utf-8")).hexdigest()
        with self._transaction():
            binding = int(self._get("binding_epoch"))
            old = self._conn.execute("SELECT body_hash,state,result,binding_epoch FROM commands WHERE command_id=?", (command_id,)).fetchone()
            if old:
                if old[3] != binding:
                    raise StoreError("command_unavailable")
                if old[0] != body_hash:
                    raise StoreError("command_conflict")
                return {"command_id": command_id, "state": old[1], "result": json.loads(old[2])}
            control = int(self._get("control_epoch"))
            kind = command.get("type")
            payload = command.get("payload")
            state, result = "rejected", {"code": "unsupported_command"}
            if self._get("binding_state") != "paired":
                result = {"code": "not_paired"}
            elif command.get("control_epoch") != control:
                result = {"code": "epoch_mismatch"}
            elif not _not_expired(command.get("expires_at")):
                result = {"code": "expired"}
            elif kind == "pause_queue":
                self._set("local_pause", 1)
                state, result = "executed", {}
            elif kind in {"resume_queue", "start_run", "pause_current_print", "stop_after_current", "cancel_current_print", "recheck_bed"}:
                result = {"code": "stage1_execution_disabled"}
            elif kind in {"enqueue_series", "reorder_queue"} and isinstance(payload, dict):
                state, result = self._apply_queue_command(kind, command, payload, binding)
            if kind in {"enqueue_series", "reorder_queue"}:
                current = int(self._get("queue_revision"))
                previous = payload.get("previous_queue_revision") if isinstance(payload, dict) else None
                if state == "rejected" and (type(previous) is not int or previous != current):
                    state, result = "uncertain", {"code": "revision_conflict", "queue_revision": current}
                else:
                    result.setdefault("queue_revision", current)
            self._conn.execute("INSERT INTO commands(command_id,body_hash,state,result,binding_epoch) VALUES(?,?,?,?,?)", (command_id, body_hash, state, _json(result), binding))
            return {"command_id": command_id, "state": state, "result": result}

    def _apply_queue_command(self, kind: str, command: Mapping[str, Any], payload: Mapping[str, Any], binding: int):
        if not _not_expired(command.get("expires_at")):
            return "rejected", {"code": "expired"}
        previous, revision = payload.get("previous_queue_revision"), payload.get("queue_revision")
        if (type(previous) is not int or type(revision) is not int or revision != previous + 1
                or type(command.get("queue_revision")) is not int or command["queue_revision"] != revision):
            return "rejected", {"code": "invalid_revision"}
        current = int(self._get("queue_revision"))
        if current != previous:
            return "uncertain", {"code": "revision_conflict", "queue_revision": current}
        if type(payload.get("binding_epoch")) is not int or payload["binding_epoch"] != binding:
            return "rejected", {"code": "epoch_mismatch", "queue_revision": current}
        if kind == "enqueue_series":
            try:
                asset_id = _uuid(payload.get("asset_id"))
            except StoreError:
                return "rejected", {"code": "invalid_asset", "queue_revision": current}
            try:
                series_id = _uuid(payload.get("series_id"))
            except StoreError:
                return "rejected", {"code": "invalid_series", "queue_revision": current}
            copies = payload.get("copies_per_run")
            completion = payload.get("completion_profile")
            if type(copies) is not int or not 1 <= copies <= self.max_runs:
                return "rejected", {"code": "invalid_copies", "queue_revision": current}
            if (not isinstance(completion, dict) or set(completion) != {"version", "profile_id", "auto_remove"}
                    or type(completion.get("version")) is not int or completion.get("version") != 1
                    or completion.get("profile_id") != self.profile_id
                    or type(completion.get("auto_remove")) is not bool):
                return "rejected", {"code": "invalid_completion_profile", "queue_revision": current}
            asset = self._conn.execute("SELECT sha256,size_bytes,path FROM assets WHERE binding_epoch=? AND asset_id=? AND confirmed=1", (binding, asset_id)).fetchone()
            runs = payload.get("run_ids")
            expected_asset_path = self._binding_dir(binding) / f"{asset_id}.gcode"
            bytes_match = False
            if asset is not None and Path(asset[2]) == expected_asset_path and not _is_link(expected_asset_path):
                try:
                    bytes_match = _file_digest(expected_asset_path) == asset[:2]
                except StoreError:
                    pass
            if (asset is None or asset[:2] != (payload.get("sha256"), payload.get("size_bytes"))
                    or Path(asset[2]) != expected_asset_path or _is_link(expected_asset_path)
                    or not bytes_match or payload.get("profile_id") != self.profile_id):
                return "rejected", {"code": "asset_mismatch", "queue_revision": current}
            if not isinstance(runs, list) or not 1 <= len(runs) <= self.max_runs:
                return "rejected", {"code": "invalid_runs", "queue_revision": current}
            try:
                run_ids = [_uuid(x) for x in runs]
            except StoreError:
                return "rejected", {"code": "invalid_runs", "queue_revision": current}
            exists = {row[0] for row in self._conn.execute("SELECT run_id FROM runs WHERE binding_epoch=?", (binding,))}
            if (len(set(run_ids)) != len(run_ids) or exists.intersection(run_ids)
                    or len(exists) + len(run_ids) > self.max_runs):
                return "rejected", {"code": "invalid_runs", "queue_revision": current}
            manifest = {"series_id": series_id, "asset_id": asset_id, "sha256": payload["sha256"],
                        "size_bytes": payload["size_bytes"], "profile_id": self.profile_id,
                        "copies_per_run": copies, "completion_profile": completion}
            for run_id in run_ids:
                self._conn.execute("INSERT INTO runs(binding_epoch,run_id,payload,position) VALUES(?,?,?,?)", (binding, run_id, _json(manifest), self._conn.execute("SELECT COALESCE(MAX(position),0)+1 FROM runs WHERE binding_epoch=?", (binding,)).fetchone()[0]))
        else:
            run_ids = payload.get("run_ids")
            current_ids = [r[0] for r in self._conn.execute("SELECT run_id FROM runs WHERE binding_epoch=? ORDER BY position", (binding,))]
            try:
                normalized = [_uuid(x) for x in run_ids] if isinstance(run_ids, list) else []
            except StoreError:
                normalized = []
            if len(normalized) != len(current_ids) or set(normalized) != set(current_ids) or len(set(normalized)) != len(normalized):
                return "rejected", {"code": "invalid_queue", "queue_revision": current}
            for pos, run_id in enumerate(normalized, 1):
                self._conn.execute("UPDATE runs SET position=? WHERE binding_epoch=? AND run_id=?", (pos, binding, run_id))
        self._set("queue_revision", revision)
        return "executed", {"queue_revision": revision}

    def pending_results(self, limit: int = 20) -> List[Dict[str, Any]]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise StoreError("invalid_limit")
        with self._lock:
            binding = int(self._get("binding_epoch"))
            rows = self._conn.execute("SELECT command_id,state,result FROM commands WHERE reported=0 AND binding_epoch=? ORDER BY rowid LIMIT ?", (binding, limit)).fetchall()
            return [{"command_id": r[0], "state": r[1], "result": json.loads(r[2])} for r in rows]

    def ack_results(self, command_ids: List[str]) -> None:
        if not isinstance(command_ids, list) or len(command_ids) > 100:
            raise StoreError("invalid_results")
        ids = [_uuid(x) for x in command_ids]
        with self._transaction():
            binding = int(self._get("binding_epoch"))
            self._conn.executemany("UPDATE commands SET reported=1 WHERE command_id=? AND binding_epoch=?", [(x, binding) for x in ids])

    def queue(self) -> List[Dict[str, Any]]:
        with self._lock:
            binding = int(self._get("binding_epoch"))
            return [{"run_id": row[0], **json.loads(row[1])} for row in self._conn.execute("SELECT run_id,payload FROM runs WHERE binding_epoch=? ORDER BY position", (binding,))]

    def status(self) -> Dict[str, Any]:
        with self._lock:
            binding = int(self._get("binding_epoch"))
            return {"binding_state": self._get("binding_state"), "binding_epoch": binding,
                    "queue_revision": int(self._get("queue_revision")), "asset_count": self._conn.execute("SELECT COUNT(*) FROM assets WHERE binding_epoch=?", (binding,)).fetchone()[0],
                    "run_count": self._conn.execute("SELECT COUNT(*) FROM runs WHERE binding_epoch=?", (binding,)).fetchone()[0],
                    "pending_result_count": self._conn.execute("SELECT COUNT(*) FROM commands WHERE reported=0 AND binding_epoch=?", (binding,)).fetchone()[0],
                    "local_pause": True, "physical_execution_enabled": False}

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                try:
                    self._conn.close()
                finally:
                    self._closed = True
                    _release_process_lock(self._process_lock)


# Блок 3: Проверки пути, ACL и fsync не позволяют принять подменённый spool.
def _uuid(value: Any) -> str:
    try:
        parsed = uuid.UUID(value) if isinstance(value, str) else None
    except (ValueError, AttributeError):
        parsed = None
    if parsed is None:
        raise StoreError("invalid_uuid")
    return str(parsed)


def _json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except (TypeError, ValueError):
        raise StoreError("invalid_payload")


def _not_expired(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        from datetime import datetime, timezone
        expiry = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return expiry.tzinfo is not None and expiry > datetime.now(timezone.utc)
    except ValueError:
        return False


def _file_digest(path: Path):
    hasher = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(chunk)
                hasher.update(chunk)
    except OSError:
        raise StoreError("asset_integrity_error")
    return hasher.hexdigest(), size


def _private_dir(path: Path) -> None:
    # Windows ACL наследуется от root; POSIX-права задаются явно.
    if os.name != "nt":
        os.chmod(path, 0o700)


def _private_file(path: Path) -> None:
    if os.name != "nt":
        os.chmod(path, 0o600)


def _is_link(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or (is_junction is not None and is_junction())


def _fsync_dir(path: Path) -> None:
    if os.name != "nt":
        fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _acquire_process_lock(path: Path):
    if _is_link(path):
        raise StoreError("unsafe_storage_path")
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(str(path), flags | getattr(os, "O_BINARY", 0), 0o600)
        lock_file = os.fdopen(descriptor, "r+b")
        _private_file(path)
        if os.name == "nt":
            import msvcrt
            if path.stat().st_size == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock_file
    except (OSError, ImportError):
        try:
            lock_file.close()
        except UnboundLocalError:
            pass
        raise StoreError("store_locked")


def _release_process_lock(lock_file) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    finally:
        lock_file.close()
