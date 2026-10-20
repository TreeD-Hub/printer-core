"""MOONRAKER COMPONENT: TREED AUTOPRINTER STORE
Назначение: долговременная локальная очередь и приватное хранилище G-code.
Контур: required для синхронизации; не выполняет физические действия.
"""

from __future__ import annotations

# Блок 1: Стандартная библиотека для атомарного spool и SQLite-журнала.
import hashlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, BinaryIO, Dict, List, Mapping
import uuid
from urllib.parse import urlsplit

DEFAULT_MAX_JOURNAL_BYTES = 64 * 1024 * 1024


class StoreError(ValueError):
    """Безопасная ошибка контракта с машинно-читаемым кодом."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


_UNSET = object()


def valid_owner_profile(value: Any) -> bool:
    if value is None:
        return True
    if (not isinstance(value, dict) or not {"display_name"} <= value.keys()
            or not value.keys() <= {"display_name", "avatar_url"}
            or not isinstance(value["display_name"], str) or len(value["display_name"]) > 100):
        return False
    avatar = value.get("avatar_url")
    if avatar is None:
        return True
    if (not isinstance(avatar, str) or len(avatar) > 2048
            or any(char.isspace() or ord(char) < 32 or char == "\\" for char in avatar)):
        return False
    try:
        url = urlsplit(avatar)
        port = url.port
        return (url.scheme == "https" and bool(url.hostname) and (port is None or port > 0)
                and url.username is None and url.password is None)
    except ValueError:
        return False


class AutoPrinterStore:
    def __init__(
        self,
        root: Path,
        printer_id: str,
        profile_id: str,
        max_asset_bytes: int = 104857600,
        max_total_bytes: int = 1073741824,
        max_runs: int = 1000,
        max_journal_bytes: int = DEFAULT_MAX_JOURNAL_BYTES,
    ) -> None:
        # Блок 2: Валидация идентичности и закрытые каталоги конкретного принтера.
        self.printer_id = _uuid(printer_id)
        if not isinstance(profile_id, str) or not profile_id or len(profile_id) > 128:
            raise StoreError("invalid_profile")
        if any(type(v) is not int or v < 1 for v in (max_asset_bytes, max_total_bytes, max_runs, max_journal_bytes)):
            raise StoreError("invalid_limit")
        self.profile_id = profile_id
        self.max_asset_bytes = max_asset_bytes
        self.max_total_bytes = max_total_bytes
        self.max_runs = max_runs
        self.max_journal_bytes = max_journal_bytes
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
        # Блок 3а: жёсткий бюджет БД сохраняет dedup и outbox без удаления истории.
        page_size = self._conn.execute("PRAGMA page_size").fetchone()[0]
        max_pages = max(1, self.max_journal_bytes // page_size)
        if (page_size > self.max_journal_bytes
                or self._conn.execute("PRAGMA page_count").fetchone()[0] > max_pages):
            raise StoreError("journal_limit_exceeded")
        actual_pages = self._conn.execute(f"PRAGMA max_page_count={max_pages}").fetchone()[0]
        if actual_pages * page_size > self.max_journal_bytes:
            raise StoreError("journal_limit_exceeded")
        self.max_journal_bytes = actual_pages * page_size
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
            CREATE TABLE IF NOT EXISTS execution_runs (
                binding_epoch INTEGER NOT NULL, run_id TEXT NOT NULL, state TEXT NOT NULL,
                filename TEXT, start_intent INTEGER NOT NULL DEFAULT 0, evidence TEXT,
                bed_epoch INTEGER NOT NULL DEFAULT 0, consumed_check_id TEXT, completion_check_id TEXT,
                bed_proof TEXT,
                error_code TEXT, PRIMARY KEY(binding_epoch, run_id));
            CREATE TABLE IF NOT EXISTS execution_events (
                event_id TEXT PRIMARY KEY, boot_id TEXT NOT NULL, event_seq INTEGER NOT NULL,
                control_epoch INTEGER NOT NULL, event_type TEXT NOT NULL, occurred_at TEXT NOT NULL,
                run_id TEXT, payload TEXT NOT NULL, acknowledged INTEGER NOT NULL DEFAULT 0,
                UNIQUE(boot_id, event_seq));
            CREATE TABLE IF NOT EXISTS execution_receipts (
                receipt_id TEXT PRIMARY KEY, boot_id TEXT NOT NULL);
            """
        )
        defaults = {"printer_id": self.printer_id, "profile_id": profile_id, "control_epoch": "1",
                    "binding_epoch": "1", "binding_state": "unpaired", "queue_revision": "1",
                    "applied_queue_revision": "1", "local_pause": "1", "remote_pause": "0", "bed_epoch": "0",
                    "owner_profile": "null", "display_name": "", "unlink_pending_epoch": "0",
                    "unlink_phase": "", "account_pairing": "null"}
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
                        inner.store._conn.rollback()
                    else:
                        try:
                            inner.store._conn.commit()
                        except Exception:
                            inner.store._conn.rollback()
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
            binding = int(self._get("binding_epoch"))
            state = self._get("binding_state")
            return {"control_epoch": int(self._get("control_epoch")), "binding_epoch": int(self._get("binding_epoch")),
                    "binding_state": state, "queue_revision": int(self._get("queue_revision")),
                    "local_pause": bool(int(self._get("local_pause"))),
                    "remote_pause": bool(int(self.execution_get_meta("remote_pause", "0"))),
                    "unlink_pending": state in {"paired", "revoked"}
                    and int(self._get("unlink_pending_epoch")) == binding}

    def update_binding(self, control_epoch: int, binding_epoch: int, binding_state: str,
                       applied_queue_revision: int, owner_profile: Any = _UNSET,
                       display_name: Any = _UNSET, unlink_pending: Any = _UNSET,
                       event_boot_id: str | None = None) -> str | None:
        if any(type(v) is not int or v < 1 for v in (control_epoch, binding_epoch, applied_queue_revision)) or binding_state not in {"unpaired", "paired", "revoked"}:
            raise StoreError("invalid_binding")
        if owner_profile is not _UNSET and not valid_owner_profile(owner_profile):
            raise StoreError("invalid_owner_profile")
        if display_name is not _UNSET and (not isinstance(display_name, str) or len(display_name) > 100):
            raise StoreError("invalid_display_name")
        if unlink_pending is not _UNSET and type(unlink_pending) is not bool:
            raise StoreError("invalid_unlink_state")
        if event_boot_id is not None:
            event_boot_id = _uuid(event_boot_id)
        with self._transaction():
            current_control, current_binding = int(self._get("control_epoch")), int(self._get("binding_epoch"))
            if control_epoch < current_control or binding_epoch < current_binding:
                raise StoreError("epoch_regression")
            changed = binding_epoch != current_binding
            if changed or binding_state != "unpaired":
                self._set("account_pairing", "null")
            previous_state = self._get("binding_state")
            if (binding_state == "unpaired" and previous_state in {"paired", "revoked"}
                    and binding_epoch == current_binding):
                raise StoreError("unlink_epoch_required")
            rollover = (binding_state == "unpaired" and changed
                        and previous_state in {"paired", "revoked"})
            if rollover and binding_epoch != current_binding + 1:
                raise StoreError("invalid_unlink_epoch")
            if event_boot_id is not None and not rollover:
                raise StoreError("invalid_event_namespace_reset")
            if rollover and event_boot_id is None:
                event_boot_id = str(uuid.uuid4())
            control_changed = control_epoch != current_control
            current_revision = int(self._get("queue_revision"))
            if display_name is not _UNSET:
                self._set("display_name", display_name)
            if changed or binding_state == "unpaired":
                self._set("owner_profile", "null")
                self._set("unlink_pending_epoch", 0)
                self._set("unlink_phase", "")
            elif binding_state == "revoked":
                self._set("owner_profile", "null")
            if binding_state == "revoked":
                self._set("control_epoch", control_epoch)
                self._set("binding_epoch", binding_epoch)
                self._set("binding_state", binding_state)
                self._set("applied_queue_revision", applied_queue_revision)
                self._set("local_pause", 1)
                if changed or control_changed:
                    self._set("bed_epoch", int(self.execution_get_meta("bed_epoch", "0") or "0") + 1)
                if changed:
                    self._set("queue_revision", 1)
                if unlink_pending is True:
                    self._set("unlink_pending_epoch", binding_epoch)
                    self._set("unlink_phase", "confirm")
                return
            if (changed and applied_queue_revision > 1) or applied_queue_revision > current_revision:
                raise StoreError("queue_recovery_required")
            if self._get("binding_state") == "unpaired" and binding_state == "paired" and applied_queue_revision > 1:
                raise StoreError("queue_recovery_required")
            self._set("control_epoch", control_epoch)
            self._set("binding_epoch", binding_epoch)
            self._set("binding_state", binding_state)
            if control_changed and not changed:
                self._set("local_pause", 1)
                self._set("remote_pause", 0)
                self._set("bed_epoch", int(self.execution_get_meta("bed_epoch", "0") or "0") + 1)
            if changed:
                self._set("queue_revision", applied_queue_revision)
                self._set("applied_queue_revision", applied_queue_revision)
                self._set("local_pause", 1)
                self._set("remote_pause", 0)
                self._set("bed_epoch", int(self.execution_get_meta("bed_epoch", "0") or "0") + 1)
                if event_boot_id is not None:
                    self._set("execution_boot_id", event_boot_id)
                    self._set("snapshot_seq", 0)
            else:
                self._set("applied_queue_revision", applied_queue_revision)
            if binding_state == "paired":
                if owner_profile is not _UNSET:
                    self._set("owner_profile", _json(owner_profile))
            if unlink_pending is True:
                self._set("unlink_pending_epoch", binding_epoch)
                self._set("unlink_phase", "confirm")
                self._set("local_pause", 1)
            return event_boot_id

    def pairing(self) -> dict | None:
        # Код хранится только в закрытой локальной БД и не входит в sync snapshot.
        with self._lock:
            if self._get("binding_state") != "unpaired":
                return None
            value = json.loads(self._get("account_pairing"))
            if value is not None and value["expires_at"] is not None:
                if datetime.fromisoformat(value["expires_at"].replace("Z", "+00:00")) <= datetime.now(timezone.utc):
                    return None
            return value

    def save_pairing(self, value: dict | None) -> None:
        if value is not None:
            if (not isinstance(value, dict) or set(value) != {"code", "expires_at", "verification_url"}
                    or not isinstance(value["code"], str)
                    or re.fullmatch(r"(?:[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{8}|[A-Z0-9]{20})", value["code"]) is None
                    or not isinstance(value["verification_url"], str) or len(value["verification_url"]) > 4096):
                raise StoreError("invalid_pairing")
            if value["expires_at"] is not None:
                try:
                    expires = datetime.fromisoformat(value["expires_at"].replace("Z", "+00:00"))
                    if expires <= datetime.now(timezone.utc):
                        raise ValueError
                except (AttributeError, TypeError, ValueError):
                    raise StoreError("invalid_pairing") from None
        with self._transaction():
            if value is not None and self._get("binding_state") != "unpaired":
                raise StoreError("invalid_pairing_state")
            self._set("account_pairing", _json(value))

    def request_unlink(self) -> int:
        with self._transaction():
            binding = int(self._get("binding_epoch"))
            if self._get("binding_state") not in {"paired", "revoked"}:
                raise StoreError("not_paired")
            if int(self._get("unlink_pending_epoch")) != binding:
                self._set("unlink_pending_epoch", binding)
                self._set("unlink_phase", "request")
            elif self._get("unlink_phase") not in {"request", "confirm"}:
                self._set("unlink_phase", "request")
            self._set("local_pause", 1)
            return binding

    def unlink_intent(self) -> Dict[str, Any] | None:
        with self._lock:
            binding = int(self._get("binding_epoch"))
            phase = self._get("unlink_phase")
            if (int(self._get("unlink_pending_epoch")) != binding
                    or phase not in {"request", "confirm"}):
                return None
            return {"binding_epoch": binding, "phase": phase}

    def set_unlink_phase(self, binding_epoch: int, phase: str) -> None:
        if type(binding_epoch) is not int or phase not in {"request", "confirm"}:
            raise StoreError("invalid_unlink_state")
        with self._transaction():
            if (int(self._get("binding_epoch")) != binding_epoch
                    or int(self._get("unlink_pending_epoch")) != binding_epoch):
                raise StoreError("stale_unlink_intent")
            self._set("unlink_phase", phase)

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
                result = {"code": "account_unlink_pending" if int(self._get("unlink_pending_epoch")) == binding
                          else "stage1_execution_disabled"}
            elif kind in {"enqueue_series", "reorder_queue"} and isinstance(payload, dict):
                if int(self._get("unlink_pending_epoch")) == binding:
                    result = {"code": "account_unlink_pending", "queue_revision": int(self._get("queue_revision"))}
                else:
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
            rows = self._conn.execute("SELECT r.run_id,r.position,e.state,e.start_intent FROM runs r LEFT JOIN execution_runs e ON e.binding_epoch=r.binding_epoch AND e.run_id=r.run_id WHERE r.binding_epoch=? ORDER BY r.position", (binding,)).fetchall()
            queued_rows = [r for r in rows if r[2] in (None, "queued") and not r[3]]
            queued_ids = {r[0] for r in queued_rows}
            try:
                normalized = [_uuid(x) for x in run_ids] if isinstance(run_ids, list) else None
            except StoreError:
                normalized = None
            if (normalized is None or len(normalized) != len(queued_ids)
                    or set(normalized) != queued_ids or len(set(normalized)) != len(normalized)):
                return "rejected", {"code": "invalid_queue", "queue_revision": current}
            queued_positions = [r[1] for r in queued_rows]
            for position, run_id in zip(queued_positions, normalized):
                self._conn.execute("UPDATE runs SET position=? WHERE binding_epoch=? AND run_id=?",
                                   (position, binding, run_id))
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

    def execution_state(self, run_id: str) -> Dict[str, Any] | None:
        run_id = _uuid(run_id)
        with self._lock:
            binding = int(self._get("binding_epoch"))
            row = self._conn.execute("SELECT state,filename,start_intent,evidence,bed_epoch,consumed_check_id,error_code,bed_proof FROM execution_runs WHERE binding_epoch=? AND run_id=?", (binding, run_id)).fetchone()
            if row is None:
                return None
            return {"run_id": run_id, "state": row[0], "filename": row[1], "start_intent": bool(row[2]),
                    "evidence": json.loads(row[3]) if row[3] else None, "bed_epoch": row[4],
                    "consumed_check_id": row[5], "error_code": row[6],
                    "bed_proof": json.loads(row[7]) if row[7] else None}

    def execution_states(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            binding = int(self._get("binding_epoch"))
            rows = self._conn.execute("SELECT run_id,state,filename,start_intent,evidence,bed_epoch,consumed_check_id,error_code,bed_proof FROM execution_runs WHERE binding_epoch=?", (binding,)).fetchall()
            return {r[0]: {"run_id": r[0], "state": r[1], "filename": r[2], "start_intent": bool(r[3]),
                           "evidence": json.loads(r[4]) if r[4] else None, "bed_epoch": r[5],
                           "consumed_check_id": r[6], "error_code": r[7],
                           "bed_proof": json.loads(r[8]) if r[8] else None} for r in rows}

    def execution_save(self, run_id: str, state: str, *, filename: str | None = None,
                       evidence: dict | None = None, error_code: str | None = None) -> Dict[str, Any]:
        run_id = _uuid(run_id)
        if state not in {"queued", "preparing", "printing", "cooling", "removing", "verifying", "completed", "failed", "cancelled", "uncertain"}:
            raise StoreError("invalid_run_state")
        with self._transaction():
            binding = int(self._get("binding_epoch"))
            if self._conn.execute("SELECT 1 FROM runs WHERE binding_epoch=? AND run_id=?", (binding, run_id)).fetchone() is None:
                raise StoreError("run_not_found")
            previous = self._conn.execute("SELECT state,start_intent,filename FROM execution_runs WHERE binding_epoch=? AND run_id=?", (binding, run_id)).fetchone()
            if previous and previous[1] and state in {"queued", "preparing"}:
                raise StoreError("start_intent_consumed")
            if previous and previous[2] and filename and previous[2] != filename:
                raise StoreError("filename_conflict")
            self._conn.execute("INSERT INTO execution_runs(binding_epoch,run_id,state,filename,evidence,error_code) VALUES(?,?,?,?,?,?) ON CONFLICT(binding_epoch,run_id) DO UPDATE SET state=excluded.state,filename=COALESCE(excluded.filename,execution_runs.filename),evidence=COALESCE(excluded.evidence,execution_runs.evidence),error_code=excluded.error_code", (binding, run_id, state, filename, _json(evidence) if evidence is not None else None, error_code))
            return self.execution_state(run_id)

    def execution_seed_run(self, run_id: str, boot_id: str, control_epoch: int, occurred_at: str) -> None:
        run_id, boot_id = _uuid(run_id), _uuid(boot_id)
        with self._transaction():
            binding = int(self._get("binding_epoch"))
            if self._conn.execute("SELECT 1 FROM runs WHERE binding_epoch=? AND run_id=?", (binding, run_id)).fetchone() is None:
                raise StoreError("run_not_found")
            if self._conn.execute("SELECT 1 FROM execution_runs WHERE binding_epoch=? AND run_id=?", (binding, run_id)).fetchone():
                return
            self._conn.execute("INSERT INTO execution_runs(binding_epoch,run_id,state) VALUES(?,?,'queued')", (binding, run_id))

    def execution_transition(self, run_id: str, state: str, boot_id: str, control_epoch: int,
                             occurred_at: str, *, error_code: str | None = None,
                             evidence: dict | None = None) -> None:
        run_id, boot_id = _uuid(run_id), _uuid(boot_id)
        with self._transaction():
            binding = int(self._get("binding_epoch"))
            if int(self._get("unlink_pending_epoch")) == binding and state == "preparing":
                raise StoreError("account_unlink_pending")
            row = self._conn.execute("SELECT state,start_intent,filename FROM execution_runs WHERE binding_epoch=? AND run_id=?", (binding, run_id)).fetchone()
            allowed = {
                "queued": {"preparing", "failed", "cancelled"},
                "preparing": {"printing", "failed", "cancelled"},
                "printing": {"cooling", "removing", "verifying", "failed", "cancelled"},
                "cooling": {"removing", "verifying", "failed", "cancelled"},
                "removing": {"verifying", "failed", "cancelled"},
                "verifying": {"failed", "cancelled"},
            }
            if row is None or state not in allowed.get(row[0], set()):
                raise StoreError("invalid_run_transition")
            if row[0] in {"queued", "preparing"} and state == "printing" and not row[1]:
                raise StoreError("missing_start_intent")
            payload = {"state": state}
            if error_code is not None:
                payload["error_code"] = error_code[:100]
            if evidence is not None:
                self._insert_event(boot_id, control_epoch, "run_reconciled", occurred_at, run_id,
                                   {"state": state, "evidence": evidence})
            else:
                self._insert_event(boot_id, control_epoch, "run_state", occurred_at, run_id, payload)
            self._conn.execute("UPDATE execution_runs SET state=?,error_code=COALESCE(?,error_code),evidence=COALESCE(?,evidence) WHERE binding_epoch=? AND run_id=?", (state, error_code, _json(evidence) if evidence is not None else None, binding, run_id))
            if state in {"failed", "cancelled"}:
                self._set("bed_epoch", int(self._get("bed_epoch")) + 1)

    def execution_reconcile(self, run_id: str, state: str, evidence: dict, boot_id: str,
                            control_epoch: int, occurred_at: str) -> None:
        run_id, boot_id = _uuid(run_id), _uuid(boot_id)
        if state not in {"printing", "failed", "cancelled"} or not isinstance(evidence, dict):
            raise StoreError("invalid_reconciliation")
        with self._transaction():
            binding = int(self._get("binding_epoch"))
            row = self._conn.execute("SELECT state,start_intent FROM execution_runs WHERE binding_epoch=? AND run_id=?", (binding, run_id)).fetchone()
            if row is None or row[0] != "uncertain" or not row[1]:
                raise StoreError("reconciliation_unavailable")
            self._insert_event(boot_id, control_epoch, "run_reconciled", occurred_at, run_id,
                               {"state": state, "evidence": evidence})
            self._conn.execute("UPDATE execution_runs SET state=?,evidence=? WHERE binding_epoch=? AND run_id=?", (state, _json(evidence), binding, run_id))
            if state in {"failed", "cancelled"}:
                self._set("bed_epoch", int(self._get("bed_epoch")) + 1)

    def _insert_event(self, boot_id: str, control_epoch: int, event_type: str, occurred_at: str,
                      run_id: str | None, payload: dict) -> None:
        if self._get("execution_boot_id") != boot_id:
            raise StoreError("boot_mismatch")
        pending = self._conn.execute("SELECT COUNT(*) FROM execution_events WHERE acknowledged=0").fetchone()[0]
        if pending >= 10000:
            raise StoreError("outbox_full")
        seq = self._conn.execute("SELECT COALESCE(MAX(event_seq),0)+1 FROM execution_events WHERE boot_id=?", (boot_id,)).fetchone()[0]
        self._conn.execute("INSERT INTO execution_events(event_id,boot_id,event_seq,control_epoch,event_type,occurred_at,run_id,payload) VALUES(?,?,?,?,?,?,?,?)", (str(uuid.uuid4()), boot_id, seq, control_epoch, event_type, occurred_at, run_id, _json(payload)))

    def execution_start_intent(self, run_id: str, filename: str, bed_epoch: int, proof: dict,
                               boot_id: str, control_epoch: int, occurred_at: str) -> None:
        run_id = _uuid(run_id)
        boot_id = _uuid(boot_id)
        if not isinstance(filename, str) or not filename or type(bed_epoch) is not int or bed_epoch < 0 or not isinstance(proof, dict):
            raise StoreError("invalid_start_intent")
        check_id = proof.get("check_id")
        if not isinstance(check_id, str):
            raise StoreError("invalid_bed_check")
        with self._transaction():
            binding = int(self._get("binding_epoch"))
            row = self._conn.execute("SELECT state,start_intent,filename FROM execution_runs WHERE binding_epoch=? AND run_id=?", (binding, run_id)).fetchone()
            if row is None or row[1] or row[0] not in {"queued", "preparing"} or row[2] not in (None, filename):
                raise StoreError("start_intent_unavailable")
            if int(self._get("unlink_pending_epoch")) == binding:
                raise StoreError("account_unlink_pending")
            if self._get("binding_state") != "paired" or self._get("local_pause") != "0":
                raise StoreError("execution_paused")
            if self._conn.execute("SELECT 1 FROM execution_runs WHERE binding_epoch=? AND start_intent=1 AND state NOT IN ('completed','failed','cancelled')", (binding,)).fetchone():
                raise StoreError("execution_in_flight")
            if self._conn.execute("SELECT 1 FROM execution_runs WHERE consumed_check_id=? OR completion_check_id=?", (check_id, check_id)).fetchone():
                raise StoreError("bed_check_consumed")
            self._conn.execute("UPDATE execution_runs SET state='preparing',filename=?,start_intent=1,bed_epoch=?,consumed_check_id=?,bed_proof=? WHERE binding_epoch=? AND run_id=?", (filename, bed_epoch, check_id, _json(proof), binding, run_id))
            self._insert_event(boot_id, control_epoch, "bed_check", occurred_at, proof.get("after_run_id"), proof)
            self._insert_event(boot_id, control_epoch, "run_state", occurred_at, run_id, {"state": "preparing"})

    def execution_set_boot(self, boot_id: str) -> Dict[str, Any] | None:
        boot_id = _uuid(boot_id)
        with self._transaction():
            old = self._get("execution_boot_id") if self._conn.execute("SELECT 1 FROM meta WHERE key='execution_boot_id'").fetchone() else None
            if old and old != boot_id:
                self._conn.execute("UPDATE execution_runs SET state='uncertain' WHERE start_intent=1 AND state NOT IN ('completed','failed','cancelled')")
                self._set("local_pause", 1)
                self._set("bed_epoch", int(self._get("bed_epoch")) + 1)
            events = [self._event_json(r) for r in self._conn.execute("SELECT event_id,boot_id,event_seq,control_epoch,event_type,occurred_at,run_id,payload FROM execution_events WHERE acknowledged=0 AND boot_id=? ORDER BY event_seq", (old,)).fetchall()] if old else []
            receipts = self._conn.execute("SELECT COUNT(*) FROM execution_receipts WHERE boot_id=?", (old,)).fetchone()[0] if old else 0
            if old and old != boot_id and (events or receipts):
                return {"boot_id": old, "pending_events": events, "pending_receipt_count": receipts}
            self._set("execution_boot_id", boot_id)
            return None

    def execution_retire_boot(self, old_boot_id: str, new_boot_id: str) -> None:
        old_boot_id, new_boot_id = _uuid(old_boot_id), _uuid(new_boot_id)
        with self._transaction():
            if self._get("execution_boot_id") != old_boot_id:
                raise StoreError("boot_mismatch")
            pending_events = self._conn.execute("SELECT 1 FROM execution_events WHERE boot_id=? AND acknowledged=0 LIMIT 1", (old_boot_id,)).fetchone()
            pending_receipts = self._conn.execute("SELECT 1 FROM execution_receipts WHERE boot_id=? LIMIT 1", (old_boot_id,)).fetchone()
            if pending_events or pending_receipts:
                raise StoreError("boot_outbox_pending")
            self._set("execution_boot_id", new_boot_id)

    @staticmethod
    def _event_json(row) -> Dict[str, Any]:
        return {"event_id": row[0], "event_seq": row[2], "control_epoch": row[3], "type": row[4], "occurred_at": row[5], "run_id": row[6], "payload": json.loads(row[7])}

    def execution_append_event(self, boot_id: str, control_epoch: int, event_type: str, occurred_at: str,
                               run_id: str | None, payload: dict) -> Dict[str, Any]:
        boot_id = _uuid(boot_id)
        if type(control_epoch) is not int or control_epoch < 1 or event_type not in {"run_state", "run_reconciled", "bed_check"} or not isinstance(payload, dict):
            raise StoreError("invalid_event")
        if run_id is not None:
            run_id = _uuid(run_id)
        with self._transaction():
            if self._get("execution_boot_id") != boot_id:
                raise StoreError("boot_mismatch")
            pending = self._conn.execute("SELECT COUNT(*) FROM execution_events WHERE acknowledged=0").fetchone()[0]
            if pending >= 10000:
                raise StoreError("outbox_full")
            seq = self._conn.execute("SELECT COALESCE(MAX(event_seq),0)+1 FROM execution_events WHERE boot_id=?", (boot_id,)).fetchone()[0]
            event_id = str(uuid.uuid4())
            self._conn.execute("INSERT INTO execution_events(event_id,boot_id,event_seq,control_epoch,event_type,occurred_at,run_id,payload) VALUES(?,?,?,?,?,?,?,?)", (event_id, boot_id, seq, control_epoch, event_type, occurred_at, run_id, _json(payload)))
            row = self._conn.execute("SELECT event_id,boot_id,event_seq,control_epoch,event_type,occurred_at,run_id,payload FROM execution_events WHERE event_id=?", (event_id,)).fetchone()
            return self._event_json(row)

    def execution_events_pending(self, boot_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise StoreError("invalid_limit")
        with self._lock:
            return [self._event_json(r) for r in self._conn.execute("SELECT event_id,boot_id,event_seq,control_epoch,event_type,occurred_at,run_id,payload FROM execution_events WHERE acknowledged=0 AND boot_id=? ORDER BY event_seq LIMIT ?", (_uuid(boot_id), limit)).fetchall()]

    def execution_unacknowledged_count(self, boot_id: str) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM execution_events WHERE acknowledged=0 AND boot_id=?", (_uuid(boot_id),)).fetchone()[0]

    def execution_ack_events(self, event_ids: List[str], submitted: List[str], receipts: List[str],
                             submitted_receipts: List[str], boot_id: str) -> None:
        if (not isinstance(event_ids, list) or not isinstance(submitted, list)
                or not isinstance(receipts, list) or not isinstance(submitted_receipts, list)
                or len(event_ids) > 100 or len(receipts) > 100
                or len(set(event_ids)) != len(event_ids) or len(set(submitted_receipts)) != len(submitted_receipts)):
            raise StoreError("invalid_event_ack")
        ids = [_uuid(x) for x in event_ids]
        sent = {_uuid(x) for x in submitted}
        receipt_ids = [_uuid(x) for x in receipts]
        confirmed = [_uuid(x) for x in submitted_receipts]
        boot_id = _uuid(boot_id)
        if not set(ids) <= sent or not set(receipt_ids) <= set(confirmed):
            raise StoreError("invalid_event_ack")
        with self._transaction():
            existing_receipts = {r[0] for r in self._conn.execute("SELECT receipt_id FROM execution_receipts WHERE boot_id=? AND receipt_id IN (%s)" % ",".join("?" for _ in confirmed), [boot_id, *confirmed]).fetchall()} if confirmed else set()
            if existing_receipts != set(confirmed):
                raise StoreError("unknown_event_receipt")
            if self._get("execution_boot_id") != boot_id:
                raise StoreError("boot_mismatch")
            rows = {r[0] for r in self._conn.execute("SELECT event_id FROM execution_events WHERE acknowledged=0 AND event_id IN (%s)" % ",".join("?" for _ in ids), ids)} if ids else set()
            if rows != set(ids):
                raise StoreError("unknown_event_ack")
            remaining = self._conn.execute("SELECT COUNT(*) FROM execution_receipts WHERE boot_id=?", (boot_id,)).fetchone()[0] - len(confirmed)
            if remaining + len(ids) > 100:
                raise StoreError("receipt_outbox_full")
            self._conn.executemany("DELETE FROM execution_receipts WHERE receipt_id=?", [(x,) for x in confirmed])
            self._conn.executemany("UPDATE execution_events SET acknowledged=1 WHERE event_id=?", [(x,) for x in ids])
            self._conn.executemany("INSERT INTO execution_receipts(receipt_id,boot_id) VALUES(?,?)", [(x, boot_id) for x in ids])

    def execution_pending_receipts(self, boot_id: str, limit: int = 100) -> List[str]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise StoreError("invalid_limit")
        with self._lock:
            return [r[0] for r in self._conn.execute("SELECT receipt_id FROM execution_receipts WHERE boot_id=? ORDER BY rowid LIMIT ?", (_uuid(boot_id), limit)).fetchall()]

    def execution_outbox_count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM execution_events WHERE acknowledged=0").fetchone()[0]

    def execution_complete(self, run_id: str, boot_id: str, control_epoch: int,
                           occurred_at: str, proof: dict) -> None:
        run_id, boot_id = _uuid(run_id), _uuid(boot_id)
        check_id = proof.get("check_id") if isinstance(proof, dict) else None
        if not isinstance(check_id, str):
            raise StoreError("invalid_bed_check")
        with self._transaction():
            binding = int(self._get("binding_epoch"))
            state = self._conn.execute("SELECT state,start_intent,consumed_check_id FROM execution_runs WHERE binding_epoch=? AND run_id=?", (binding, run_id)).fetchone()
            if state is None or state[0] != "verifying" or not state[1] or state[2] == check_id:
                raise StoreError("completion_unavailable")
            if self._get("execution_boot_id") != boot_id or self._conn.execute("SELECT 1 FROM execution_runs WHERE consumed_check_id=? OR completion_check_id=?", (check_id, check_id)).fetchone():
                raise StoreError("bed_check_consumed")
            self._insert_event(boot_id, control_epoch, "bed_check", occurred_at, run_id, proof)
            self._conn.execute("UPDATE execution_runs SET state='completed',completion_check_id=? WHERE binding_epoch=? AND run_id=?", (check_id, binding, run_id))
            self._set("bed_epoch", proof["bed_epoch"] + 1)

    def execution_asset_path(self, run: Mapping[str, Any]) -> Path:
        with self._lock:
            binding = int(self._get("binding_epoch"))
            row = self._conn.execute("SELECT asset_id,sha256,size_bytes FROM assets WHERE binding_epoch=? AND asset_id=? AND confirmed=1", (binding, run.get("asset_id"))).fetchone()
            if row is None or row[1:] != (run.get("sha256"), run.get("size_bytes")):
                raise StoreError("asset_mismatch")
            path = self._binding_dir(binding) / f"{row[0]}.gcode"
            if _is_link(path) or _file_digest(path) != row[1:]:
                raise StoreError("asset_integrity_error")
            return path

    def execution_set_pause(self, *, local: bool | None = None, remote: bool | None = None) -> None:
        with self._transaction():
            pending = (int(self._get("unlink_pending_epoch")) == int(self._get("binding_epoch"))
                       and self._get("binding_state") in {"paired", "revoked"})
            if local is not None:
                self._set("local_pause", int(local or pending))
            if remote is not None:
                self._set("remote_pause", int(remote or pending))

    def execution_get_meta(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return row[0] if row else default

    def execution_set_meta(self, key: str, value: Any) -> None:
        with self._transaction():
            self._set(key, value)

    def execution_mark_uncertain(self, run_id: str) -> None:
        run_id = _uuid(run_id)
        with self._transaction():
            binding = int(self._get("binding_epoch"))
            self._conn.execute("UPDATE execution_runs SET state='uncertain' WHERE binding_epoch=? AND run_id=? AND start_intent=1 AND state NOT IN ('failed','cancelled')", (binding, run_id))

    def execution_has_unresolved_intent(self) -> bool:
        with self._lock:
            return self._conn.execute("SELECT 1 FROM execution_runs WHERE start_intent=1 AND state NOT IN ('completed','failed','cancelled') LIMIT 1").fetchone() is not None

    def execution_last_started(self) -> Dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT r.run_id,r.filename,r.state FROM execution_events e JOIN execution_runs r ON r.run_id=e.run_id AND r.binding_epoch=? WHERE e.event_type='run_state' AND json_extract(e.payload,'$.state') IN ('preparing','printing') ORDER BY e.rowid DESC LIMIT 1", (int(self._get("binding_epoch")),)).fetchone()
            return {"run_id": row[0], "filename": row[1], "state": row[2]} if row else None

    def execution_begin_action(self, command: dict, *, local: bool = False) -> Dict[str, Any]:
        if not isinstance(command, dict):
            raise StoreError("invalid_command")
        command_id = _uuid(command.get("command_id"))
        if type(command.get("control_epoch")) is not int or not isinstance(command.get("payload"), dict):
            raise StoreError("invalid_command")
        canonical = {key: value for key, value in command.items()
                     if key not in ({"state", "expires_at"} if local else {"state"})}
        hash_input = {"local": True, "command": canonical} if local else canonical
        body_hash = hashlib.sha256(_json(hash_input).encode("utf-8")).hexdigest()
        with self._transaction():
            binding = int(self._get("binding_epoch"))
            old = self._conn.execute("SELECT body_hash,state,result,binding_epoch FROM commands WHERE command_id=?", (command_id,)).fetchone()
            if old:
                if old[3] != binding:
                    raise StoreError("command_unavailable")
                if old[0] != body_hash:
                    raise StoreError("command_conflict")
                return {"command_id": command_id, "state": old[1], "result": json.loads(old[2]), "new": False}
            result = {"code": "side_effect_uncertain"}
            if (self._get("binding_state") != "paired" or command["control_epoch"] != int(self._get("control_epoch"))
                    or (not local and not _not_expired(command.get("expires_at")))):
                result = {"code": "epoch_mismatch" if command["control_epoch"] != int(self._get("control_epoch")) else "expired"}
                state = "rejected"
            else:
                state = "uncertain"
            self._conn.execute("INSERT INTO commands(command_id,body_hash,state,result,binding_epoch,reported) VALUES(?,?,?,?,?,?)",
                               (command_id, body_hash, state, _json(result), binding, int(local)))
            return {"command_id": command_id, "state": state, "result": result, "new": True}

    def execution_finish_action(self, command_id: str, state: str, result: dict) -> None:
        command_id = _uuid(command_id)
        if state not in {"executed", "rejected", "uncertain"} or not isinstance(result, dict):
            raise StoreError("invalid_command_result")
        with self._transaction():
            row = self._conn.execute("SELECT state,binding_epoch FROM commands WHERE command_id=?", (command_id,)).fetchone()
            if row is None or row[0] != "uncertain" or row[1] != int(self._get("binding_epoch")):
                raise StoreError("command_unavailable")
            self._conn.execute("UPDATE commands SET state=?,result=? WHERE command_id=?", (state, _json(result), command_id))

    def status(self) -> Dict[str, Any]:
        with self._lock:
            binding = int(self._get("binding_epoch"))
            paired = self._get("binding_state") == "paired"
            pending = (self._get("binding_state") in {"paired", "revoked"}
                       and int(self._get("unlink_pending_epoch")) == binding)
            queue_total = self._conn.execute(
                "SELECT COUNT(*) FROM runs WHERE binding_epoch=?", (binding,)
            ).fetchone()[0] if paired else 0
            queue_rows = self._conn.execute(
                "SELECT r.run_id,r.position,e.state,r.payload FROM runs r "
                "LEFT JOIN execution_runs e ON e.binding_epoch=r.binding_epoch AND e.run_id=r.run_id "
                "WHERE r.binding_epoch=? ORDER BY r.position,r.run_id LIMIT 1000", (binding,)
            ).fetchall() if paired else []
            queue = []
            for run_id, position, state, payload in queue_rows:
                manifest = json.loads(payload)
                queue.append({"run_id": run_id, "position": position,
                              "state": state or "queued", "series_id": manifest.get("series_id"),
                              "copies_per_run": manifest.get("copies_per_run")})
            result = {"binding_state": self._get("binding_state"), "binding_epoch": binding,
                    "queue_revision": int(self._get("queue_revision")), "asset_count": self._conn.execute("SELECT COUNT(*) FROM assets WHERE binding_epoch=?", (binding,)).fetchone()[0] if paired else 0,
                    "run_count": self._conn.execute("SELECT COUNT(*) FROM runs WHERE binding_epoch=?", (binding,)).fetchone()[0] if paired else 0,
                    "pending_result_count": self._conn.execute("SELECT COUNT(*) FROM commands WHERE reported=0 AND binding_epoch=?", (binding,)).fetchone()[0] if paired else 0,
                    "queue": queue, "queue_total": queue_total,
                    "queue_truncated": queue_total > len(queue),
                    "display_name": self._get("display_name") or None,
                    "owner_profile": json.loads(self._get("owner_profile")) if paired else None,
                    "pairing": self.pairing(),
                    "unlink_pending": pending,
                    "local_pause": True, "remote_pause": False, "physical_execution_enabled": False}
            if paired:
                page_size = self._conn.execute("PRAGMA page_size").fetchone()[0]
                allocated = self._conn.execute("PRAGMA page_count").fetchone()[0] * page_size
                result["journal"] = {"allocated_bytes": allocated,
                                     "limit_bytes": self.max_journal_bytes,
                                     "at_capacity": allocated >= self.max_journal_bytes}
            return result

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
