"""MOONRAKER COMPONENT: TREED AUTOPRINTER EXECUTION
Назначение: безопасно исполнять локальную очередь и журналировать результат.
Контур: required при включённом исполнении; физический доступ только через адаптер.
"""

from __future__ import annotations

# Блок 1: Зависимости, проверки доказательств и разрешённые состояния.
import asyncio
from datetime import datetime, timedelta, timezone
import math
import re
import uuid
from typing import Any, Mapping

from .treed_autoprinter_store import AutoPrinterStore, StoreError


_TERMINAL = {"completed", "failed", "cancelled"}
_ACTIONS = {"pause_queue", "resume_queue", "stop_after_current", "pause_current_print",
            "cancel_current_print", "recheck_bed"}


class AutoPrinterExecutor:
    def __init__(self, store: AutoPrinterStore, device: Any, process_boot_id: str,
                 enabled: bool = False, bed_checker: Any = None) -> None:
        self.store = store
        self.device = device
        self.process_boot_id = str(uuid.UUID(process_boot_id))
        self.enabled = bool(enabled)
        self.bed_checker = bed_checker
        self._active_boot_id = self.process_boot_id
        self._draining_boot_id: str | None = None
        self._blocked_reason: str | None = None
        self._initialized = False
        self._snapshot_seq = 0
        self._ready = False

    async def _db(self, method: str, *args: Any, **kwargs: Any) -> Any:
        # Блокирующие SQLite/file operations должны завершиться даже при отмене caller.
        task = asyncio.create_task(asyncio.to_thread(getattr(self.store, method), *args, **kwargs))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    async def initialize(self) -> None:
        previous = await self._db("execution_set_boot", self.process_boot_id)
        if previous:
            self._draining_boot_id = previous["boot_id"]
            self._active_boot_id = previous["boot_id"]
        self._snapshot_seq = int(await self._db("execution_get_meta", "snapshot_seq", "0") or "0")
        self._ready = False
        session = await self._db("session")
        for run in await self._db("queue"):
            await self._db("execution_seed_run", run["run_id"], self._active_boot_id,
                           session["control_epoch"], _now())
        self._initialized = True

    async def _emit(self, kind: str, run_id: str | None, payload: dict) -> dict:
        session = await self._db("session")
        return await self._db("execution_append_event", self._active_boot_id,
                              session["control_epoch"], kind, _now(), run_id, payload)

    async def sync_fields(self) -> dict[str, Any]:
        if not self._initialized:
            await self.initialize()
        events = await self._db("execution_events_pending", self._active_boot_id, 100)
        receipts = await self._db("execution_pending_receipts", self._active_boot_id, 100)
        results = await self._db("pending_results", 20)
        self._snapshot_seq += 1
        await self._db("execution_set_meta", "snapshot_seq", self._snapshot_seq)
        snapshot = await self._wire_snapshot()
        if await self._db("execution_unacknowledged_count", self._active_boot_id) > 100:
            snapshot["current_run_id"] = None
            snapshot["run_state"] = None
        return {"agent_boot_id": self._active_boot_id, "snapshot_seq": self._snapshot_seq,
                "snapshot": snapshot, "events": events,
                "acknowledged_event_ids": receipts,
                "command_results": results}

    async def acknowledge(self, response: dict, submitted: dict) -> None:
        if not isinstance(response, dict) or not isinstance(submitted, dict):
            raise StoreError("invalid_ack")
        session = await self._db("session")
        if (response.get("reason") == "control_epoch_mismatch"
                or response.get("binding_state") == "revoked"
                or submitted.get("binding_epoch") != session["binding_epoch"]
                or response.get("control_epoch") != submitted.get("control_epoch")
                or response.get("binding_epoch") != submitted.get("binding_epoch")
                or submitted.get("agent_boot_id") != self._active_boot_id):
            self._blocked_reason = "protocol_epoch_mismatch"
            return
        sent_events = submitted.get("events", [])
        sent = [e.get("event_id") for e in sent_events if isinstance(e, dict)] if isinstance(sent_events, list) else None
        sent_receipts = submitted.get("acknowledged_event_ids", [])
        ack = response.get("ack_event_ids", [])
        ignored = response.get("ignored_event_ids", [])
        if (not isinstance(sent, list) or not isinstance(sent_receipts, list)
                or not isinstance(ack, list) or not isinstance(ignored, list)
                or not all(_uuid_valid(value) for value in sent + sent_receipts + ack)
                or len(set(sent)) != len(sent) or len(set(sent_receipts)) != len(sent_receipts)
                or any(not isinstance(item, dict) or not _uuid_valid(item.get("event_id")) for item in ignored)):
            raise StoreError("invalid_event_ack")
        acked = set(ack)
        expected = set(sent) | set(sent_receipts)
        if not acked <= expected:
            raise StoreError("invalid_event_ack")
        if ignored:
            await self._db("execution_set_meta", "protocol_recovery_required", "1")
            self._blocked_reason = "event_rejected"
            raise StoreError("event_rejected")
        if await self._db("execution_get_meta", "protocol_recovery_required") == "1":
            self._blocked_reason = "protocol_recovery_required"
            return
        event_acks = [event_id for event_id in sent if event_id in acked]
        receipt_acks = list(sent_receipts)
        await self._db("execution_ack_events", event_acks, sent, receipt_acks, receipt_acks,
                       self._active_boot_id)
        self._blocked_reason = None
        await self._db("execution_set_meta", "protocol_epoch_mismatch", "0")
        await self._db("execution_set_meta", "execution_handshake_boot", self._active_boot_id)
        if (self._draining_boot_id
                and not await self._db("execution_events_pending", self._draining_boot_id, 1)
                and not await self._db("execution_pending_receipts", self._draining_boot_id, 1)):
            await self._db("execution_retire_boot", self._draining_boot_id, self.process_boot_id)
            self._active_boot_id = self.process_boot_id
            self._draining_boot_id = None
            self._snapshot_seq = 0
            await self._db("execution_set_meta", "snapshot_seq", 0)

    async def transport_error(self, status: int | None) -> None:
        if status == 409:
            await self._db("execution_set_meta", "protocol_recovery_required", "1")
            await self._db("execution_set_pause", local=True, remote=True)
            self._blocked_reason = "protocol_recovery_required"
        elif status == 401:
            await self._db("execution_set_pause", local=True, remote=True)
            self._blocked_reason = "binding_revoked"

    async def command(self, command: dict, *, local: bool = False) -> dict:
        if not isinstance(command, dict) or command.get("type") not in _ACTIONS:
            return await self._db("apply_command", command)
        kind = command["type"]
        session = await self._db("session")
        intent = await self._db("execution_begin_action", command, local=local)
        if intent["state"] != "uncertain" or not intent.get("new"):
            return {key: value for key, value in intent.items() if key != "new"}
        if session.get("unlink_pending") and kind == "resume_queue":
            result = {"command_id": intent["command_id"], "state": "rejected",
                      "result": {"code": "account_unlink_pending"}}
            await self._db("execution_finish_action", intent["command_id"], result["state"], result["result"])
            return result
        if (session["binding_state"] != "paired" or command.get("control_epoch") != session["control_epoch"]
                or command.get("queue_revision") != session["queue_revision"]):
            result = {"command_id": intent["command_id"], "state": "rejected",
                      "result": {"code": "epoch_mismatch"}}
            await self._db("execution_finish_action", intent["command_id"], result["state"], result["result"])
            return result
        if kind == "pause_queue":
            await self._db("execution_set_pause", remote=True)
            result = {"command_id": intent["command_id"], "state": "executed", "result": {}}
        elif kind == "resume_queue":
            await self._db("execution_set_pause", remote=False)
            if not (await self._db("session"))["local_pause"]:
                await self._db("execution_set_meta", "stop_after_current", "0")
            result = {"command_id": intent["command_id"], "state": "executed", "result": {}}
        elif kind == "stop_after_current":
            await self._db("execution_set_meta", "stop_after_current", "1")
            result = {"command_id": intent["command_id"], "state": "executed", "result": {}}
        elif kind == "recheck_bed":
            current = await self._current()
            after_run = current["run_id"] if current else await self._last_started_id()
            proof = await self._bed_proof(after_run, require_empty=False)
            if proof is None:
                result = {"command_id": intent["command_id"], "state": "rejected", "result": {"code": "bed_check_unavailable"}}
            elif (current and current["state"] == "verifying" and proof["verdict"] == "empty"
                  and (getattr(self.bed_checker, "live_qualified", False) is True
                       or getattr(self.device, "is_live", None) is False)):
                await self._db("execution_complete", current["run_id"], self._active_boot_id,
                               session["control_epoch"], _now(), proof)
                result = {"command_id": intent["command_id"], "state": "executed", "result": {}}
            else:
                await self._emit("bed_check", after_run, proof)
                result = {"command_id": intent["command_id"], "state": "executed", "result": {}}
        else:
            current = await self._current()
            payload = command.get("payload", {})
            if current is None or command.get("run_id") != current["run_id"] or payload.get("run_id", current["run_id"]) != current["run_id"]:
                result = {"command_id": intent["command_id"], "state": "rejected", "result": {"code": "run_mismatch"}}
            else:
                try:
                    observed = await self.device.inspect()
                except Exception:
                    observed = None
                if (not _safe_observation(observed) or observed["filename"] != current["filename"]
                        or observed["state"] not in {"printing", "paused"}
                        or not observed["ready"] or not observed["queue_empty"]
                        or observed["service_active"] or not observed["queue_safe"]):
                    result = {"command_id": intent["command_id"], "state": "rejected",
                              "result": {"code": "runtime_run_mismatch"}}
                else:
                    try:
                        await self._db("execution_set_meta", "bed_epoch", int(await self._db("execution_get_meta", "bed_epoch", "0") or "0") + 1)
                        await getattr(self.device, "pause" if kind == "pause_current_print" else "cancel")()
                        result = {"command_id": intent["command_id"], "state": "executed", "result": {}}
                    except Exception:
                        result = {"command_id": intent["command_id"], "state": "uncertain", "result": {"code": "side_effect_uncertain"}}
        await self._db("execution_finish_action", intent["command_id"], result["state"], result["result"])
        return result

    async def local_action(self, action: dict) -> dict:
        if not isinstance(action, dict) or action.get("type") not in _ACTIONS:
            raise StoreError("invalid_action")
        session = await self._db("session")
        if session["binding_state"] != "paired":
            raise StoreError("not_paired")
        kind = action["type"]
        if session.get("unlink_pending") and kind == "resume_queue":
            raise StoreError("account_unlink_pending")
        receipt = {"type": kind, "command_id": None, "state": "uncertain",
                   "result": {"code": "local_action_outcome_uncertain"}}
        try:
            if kind == "pause_queue":
                await self._db("execution_set_pause", local=True)
                receipt.update(state="executed", result={})
            elif kind == "resume_queue":
                await self._db("execution_set_pause", local=False)
                await self._db("execution_set_meta", "stop_after_current", "0")
                receipt.update(state="executed", result={})
            elif kind == "recheck_bed":
                current = await self._current()
                context = current["run_id"] if current else await self._last_started_id()
                proof = await self._bed_proof(context, require_empty=False)
                if proof is None:
                    self._blocked_reason = "bed_proof_unavailable"
                    receipt.update(state="rejected", result={"code": "bed_proof_unavailable"})
                elif (current and current["state"] == "verifying" and proof["verdict"] == "empty"
                      and (getattr(self.bed_checker, "live_qualified", False) is True
                           or getattr(self.device, "is_live", None) is False)):
                    await self._db("execution_complete", current["run_id"], self._active_boot_id,
                                   session["control_epoch"], _now(), proof)
                    receipt.update(state="executed", result={})
                else:
                    await self._emit("bed_check", context, proof)
                    code = {"occupied": "bed_occupied", "unknown": "bed_unknown"}.get(proof["verdict"])
                    receipt.update(state="executed", result={"code": code} if code else {})
            elif kind == "stop_after_current":
                await self._db("execution_set_pause", local=True)
                await self._db("execution_set_meta", "stop_after_current", "1")
                receipt.update(state="executed", result={})
            elif kind in {"pause_current_print", "cancel_current_print"}:
                current = await self._current()
                if (current is None or action.get("run_id") != current["run_id"]
                        or not _uuid_valid(action.get("action_id"))):
                    raise StoreError("no_current_run")
                command = {"command_id": action["action_id"], "type": kind,
                           "run_id": current["run_id"],
                           "control_epoch": session["control_epoch"],
                           "binding_epoch": session["binding_epoch"],
                           "queue_revision": session["queue_revision"],
                           "payload": {"run_id": current["run_id"]}}
                receipt.update(command_id=action["action_id"])
                command_result = await self.command(command, local=True)
                receipt.update(state=command_result["state"], result=command_result["result"])
        except StoreError as exc:
            if kind in {"pause_current_print", "cancel_current_print"} or str(exc) in {"no_current_run", "invalid_uuid"}:
                raise
        except Exception:
            pass
        status = await self.status()
        status["action_result"] = receipt
        return status

    async def unlink_quiescent(self, device: Any = None) -> bool:
        target = self.device if device is None else device
        if target is None:
            return False
        try:
            observed = await target.inspect()
        except Exception:
            return False
        return (isinstance(observed, dict) and observed.get("ready") is True
                and observed.get("phase") == "idle"
                and observed.get("state") in {"idle", "complete", "error", "cancelled"}
                and observed.get("service_active") is False and observed.get("queue_safe") is True)

    def reset_outbox_namespace(self, event_boot_id: str) -> None:
        self._active_boot_id = str(uuid.UUID(event_boot_id))
        self._draining_boot_id = None
        self._snapshot_seq = 0
        if self._blocked_reason in {"binding_revoked", "protocol_epoch_mismatch"}:
            self._blocked_reason = None

    async def tick(self) -> None:
        if not self._initialized:
            await self.initialize()
        session = await self._db("session")
        unlink_pending = bool(session.get("unlink_pending"))
        hard_blocks = {"protocol_recovery_required", "protocol_epoch_mismatch", "event_rejected",
                       "binding_revoked"}
        if (not self.enabled or self.device is None
                or self._draining_boot_id and not unlink_pending
                or self._blocked_reason in hard_blocks
                or await self._db("execution_get_meta", "protocol_recovery_required") == "1"
                or (await self._db("execution_get_meta", "execution_handshake_boot") != self._active_boot_id
                    and not unlink_pending)):
            return
        self._blocked_reason = None
        if await self._db("execution_has_unresolved_intent"):
            run = await self._current()
            if run is None:
                self._blocked_reason = "unresolved_start_intent"
                return
            await self._reconcile(run)
            return
        if unlink_pending:
            current = await self._current()
            if current is not None:
                await self._reconcile(current)
            else:
                self._blocked_reason = "account_unlink_pending"
            return
        if (session["binding_state"] != "paired"
                or await self._db("execution_outbox_count") >= 9900):
            self._blocked_reason = "execution_gate_closed"
            return
        if session.get("local_pause") or session.get("remote_pause"):
            self._blocked_reason = "execution_gate_closed"
            return
        observation = await self.device.inspect()
        self._ready = bool(_safe_observation(observation) and observation["ready"])
        if not _safe_observation(observation):
            self._blocked_reason = "device_not_safe"
            return
        queue = await self._db("queue")
        session = await self._db("session")
        for queued in queue:
            await self._db("execution_seed_run", queued["run_id"], self._active_boot_id,
                           session["control_epoch"], _now())
        states = await self._db("execution_states")
        if await self._db("execution_get_meta", "stop_after_current") == "1" and not await self._current():
            return
        run = next((dict(r, binding_epoch=session["binding_epoch"]) for r in queue
                    if states.get(r["run_id"], {}).get("state", "queued") == "queued"), None)
        if run is None:
            return
        terminal_states = {"complete", "cancelled", "error"}
        safe_idle = observation["state"] == "idle"
        if observation["state"] in terminal_states:
            previous = await self._last_started_run()
            safe_idle = (previous is not None and observation["filename"] == previous["filename"]
                         and observation["queue_empty"] and observation["queue_safe"]
                         and not observation["service_active"])
        if (not observation["ready"] or not safe_idle or not observation["queue_empty"]
                or not observation["queue_safe"] or observation["service_active"]):
            self._blocked_reason = "device_not_safe"
            if observation["state"] != "idle" and await self._db("execution_get_meta", "bed_intervention") != "1":
                await self._db("execution_set_meta", "bed_intervention", "1")
                await self._db("execution_set_meta", "bed_epoch", int(await self._db("execution_get_meta", "bed_epoch", "0") or "0") + 1)
            return
        # Fresh bed proof is required before the runtime adapter promotes a file.
        try:
            initial_proof = await self._bed_proof(await self._last_started_id())
            if initial_proof is None:
                self._blocked_reason = "bed_proof_unavailable"
                return
            path = await self._db("execution_asset_path", run)
            filename = await self.device.prepare(run, path)
            if not _safe_filename(filename, run["binding_epoch"], run["run_id"], run["sha256"]):
                raise StoreError("invalid_runtime_filename")
            proof = await self._bed_proof(await self._last_started_id())
            if proof is None:
                self._blocked_reason = "bed_proof_unavailable"
                await self._db("execution_set_pause", local=True)
                return
            await self._db("execution_start_intent", run["run_id"], filename, proof["bed_epoch"], proof,
                           self._active_boot_id, session["control_epoch"], _now())
            await self.device.start(filename)
        except Exception as error:
            # Неопределённый результат start сохраняется; повторного физического вызова не будет.
            current = await self._db("execution_state", run["run_id"])
            if current and current["start_intent"]:
                self._blocked_reason = "start_outcome_uncertain"
            else:
                await self._db("execution_set_pause", local=True)
                self._blocked_reason = getattr(error, "code", "execution_preflight_failed")

    async def _reconcile(self, run: dict) -> None:
        observed = await self.device.reconcile(run)
        self._ready = bool(_safe_observation(observed) and observed["ready"])
        if not _safe_observation(observed):
            self._blocked_reason = "reconciliation_unknown"
            return
        if observed["state"] == "paused":
            await self._db("execution_set_meta", "print_paused", "1")
            self._blocked_reason = "print_paused"
            return
        await self._db("execution_set_meta", "print_paused", "0")
        if observed["filename"] != run["filename"]:
            self._blocked_reason = "reconciliation_mismatch"
            return
        source = observed.get("evidence_source")
        if source not in {"runtime", "history"} or not _run_hash(run):
            self._blocked_reason = "reconciliation_unknown"
            return
        evidence = {"source": source, "run_id": run["run_id"], "sha256": _run_hash(run)}
        if observed["state"] == "printing":
            if run["state"] != "printing":
                if run["state"] == "uncertain":
                    await self._db("execution_reconcile", run["run_id"], "printing", evidence, self._active_boot_id,
                                   (await self._db("session"))["control_epoch"], _now())
                else:
                    await self._db("execution_transition", run["run_id"], "printing", self._active_boot_id,
                                   (await self._db("session"))["control_epoch"], _now())
            self._blocked_reason = None
        elif observed["state"] in {"failed", "cancelled", "error"}:
            final_state = "failed" if observed["state"] == "error" else observed["state"]
            if run["state"] == "uncertain":
                await self._db("execution_reconcile", run["run_id"], final_state, evidence, self._active_boot_id,
                               (await self._db("session"))["control_epoch"], _now())
            else:
                await self._db("execution_transition", run["run_id"], final_state, self._active_boot_id,
                               (await self._db("session"))["control_epoch"], _now(), error_code="runtime_reconciled")
            self._blocked_reason = None
        elif observed["state"] in {"complete", "completed"}:
            if run["state"] not in {"printing", "cooling", "removing", "verifying"}:
                self._blocked_reason = "reconciliation_unknown"
                return
            if run["state"] != "verifying":
                await self._db("execution_transition", run["run_id"], "verifying", self._active_boot_id,
                               (await self._db("session"))["control_epoch"], _now())
            proof = await self._bed_proof(run["run_id"])
            if proof:
                await self._db("execution_complete", run["run_id"], self._active_boot_id,
                               (await self._db("session"))["control_epoch"], _now(), proof)
                self._blocked_reason = None
            else:
                self._blocked_reason = "bed_proof_unavailable"
        else:
            self._blocked_reason = "reconciliation_unknown"

    async def _current(self) -> dict | None:
        states = await self._db("execution_states")
        session = await self._db("session")
        return next((dict(r, **states[r["run_id"]], binding_epoch=session["binding_epoch"])
                     for r in await self._db("queue") if r["run_id"] in states and
                     states[r["run_id"]]["state"] in {"preparing", "printing", "cooling", "removing", "verifying", "uncertain"}), None)

    async def _bed_proof(self, after_run_id: str | None, *, require_empty: bool = True) -> dict | None:
        if self.bed_checker is None:
            return None
        qualified = getattr(self.bed_checker, "live_qualified", False) is True
        offline_test_device = self.device is not None and getattr(self.device, "is_live", None) is False
        if require_empty and not qualified and not offline_test_device:
            return None
        if not require_empty:
            handshake_boot = await self._db("execution_get_meta", "execution_handshake_boot")
            if (not self._initialized or self._draining_boot_id is not None
                    or handshake_boot != self._active_boot_id):
                return None
        expected_bed_epoch = int(await self._db("execution_get_meta", "bed_epoch", "0") or "0")
        try:
            proof = await self.bed_checker.check(after_run_id, expected_bed_epoch, self._active_boot_id)
        except Exception:
            return None
        if not isinstance(proof, dict):
            return None
        now = datetime.now(timezone.utc)
        captured = _parse_time(proof.get("captured_at"))
        allowed_models = getattr(self.bed_checker, "allowed_model_versions", ())
        confidence = proof.get("confidence")
        reason = proof.get("reason")
        valid_confidence = (confidence is None or (not isinstance(confidence, bool)
                            and isinstance(confidence, (int, float)) and math.isfinite(confidence)
                            and 0.0 <= confidence <= 1.0))
        valid_reason = reason is None or (isinstance(reason, str) and len(reason) <= 100)
        verdict = proof.get("verdict")
        valid_verdict = (verdict == "empty" and (qualified or offline_test_device)
                         or not require_empty and verdict in {"occupied", "unknown"})
        if (proof.get("printer_id") != self.store.printer_id or proof.get("after_run_id") != after_run_id
                or proof.get("agent_boot_id") != self._active_boot_id or not valid_verdict
                or type(proof.get("protocol_version")) is not int or proof["protocol_version"] != 1
                or type(proof.get("bed_epoch")) is not int or proof["bed_epoch"] != expected_bed_epoch
                or not _uuid_valid(proof.get("check_id")) or captured is None
                or captured > now or (now - captured).total_seconds() > 30
                or not isinstance(proof.get("model_version"), str)
                or len(proof["model_version"]) > 100 or not valid_confidence or not valid_reason
                or proof["model_version"] not in allowed_models):
            return None
        payload = {key: proof[key] for key in (
            "protocol_version", "printer_id", "after_run_id", "check_id", "bed_epoch",
            "agent_boot_id", "verdict", "model_version", "captured_at",
        ) if key in proof}
        payload["captured_at"] = captured.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        payload["confidence"] = confidence
        payload["reason"] = reason
        return payload

    async def _last_started_run(self) -> dict | None:
        return await self._db("execution_last_started")

    async def _last_started_id(self) -> str | None:
        run = await self._last_started_run()
        return run["run_id"] if run else None

    async def _wire_snapshot(self) -> dict[str, Any]:
        session = await self._db("session")
        states = await self._db("execution_states")
        queue = await self._db("queue")
        current = next((states[run["run_id"]] for run in queue if session["binding_state"] == "paired" and run["run_id"] in states
                        and states[run["run_id"]]["state"] in {"preparing", "printing", "cooling", "removing", "verifying", "uncertain"}), None)
        protocol_block = await self._db("execution_get_meta", "protocol_recovery_required") == "1"
        control = ("blocked" if protocol_block or self._blocked_reason in {"reconciliation_unknown", "reconciliation_mismatch", "protocol_recovery_required", "protocol_epoch_mismatch", "event_rejected", "start_outcome_uncertain"}
                   else "stop_after_current" if await self._db("execution_get_meta", "stop_after_current") == "1"
                   else "paused" if session["local_pause"] or session["remote_pause"]
                   else "running")
        return {"observed_at": _now(), "bed_epoch": int(await self._db("execution_get_meta", "bed_epoch", "0") or "0"),
                "queue_control": control, "current_run_id": current["run_id"] if current else None,
                "run_state": current["state"] if current else None,
                "local_pause": session["local_pause"], "ready": self._ready and session["binding_state"] == "paired",
                "print_paused": bool(await self._db("execution_get_meta", "print_paused", "0") == "1"),
                "capabilities": {"bed_check": bool(self.bed_checker and getattr(self.bed_checker, "live_qualified", False))},
                "model_version": getattr(self.bed_checker, "model_version", None)}

    async def status(self) -> dict[str, Any]:
        snapshot = await self._wire_snapshot()
        return {**snapshot,
                "blocked_reason": self._blocked_reason,
                "execution_enabled": bool(self.enabled and self.device is not None),
                "stop_after_current": await self._db("execution_get_meta", "stop_after_current", "0") == "1",
                "capabilities": {"bed_check": bool(self.bed_checker and getattr(self.bed_checker, "live_qualified", False)),
                                 "bed_check_diagnostic": self.bed_checker is not None,
                                 "queue": True, "pairing": True}}


# Блок 2: Строгая валидация наблюдений и времени из runtime/provider.
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except ValueError:
        return None


def _uuid_valid(value: Any) -> bool:
    try:
        return str(uuid.UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def _safe_filename(value: Any, binding_epoch: Any, run_id: Any, sha256: Any) -> bool:
    if (type(binding_epoch) is not int or not _uuid_valid(run_id)
            or not isinstance(sha256, str) or re.fullmatch(r"[0-9a-f]{64}", sha256) is None):
        return False
    expected = f"treed-autoprinter/{binding_epoch}-{run_id}-{sha256}.gcode"
    return value == expected


def _safe_observation(value: Any) -> bool:
    fields = {"ready", "state", "filename", "job_id", "queue_empty", "queue_safe", "service_active", "phase"}
    return (isinstance(value, dict) and fields <= value.keys() and type(value["ready"]) is bool
            and value["state"] in {"idle", "printing", "paused", "complete", "error", "cancelled", "unknown"}
            and type(value["queue_empty"]) is bool and type(value["queue_safe"]) is bool
            and type(value["service_active"]) is bool)


def _run_hash(run: Mapping[str, Any]) -> str:
    value = run.get("sha256")
    return value if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) else ""
