"""Проверки durable outbox и default-off исполнителя без устройства и сети."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import tempfile
import unittest
import uuid
from pathlib import Path

from moonraker.components.treed_autoprinter_execution import AutoPrinterExecutor
from moonraker.components.treed_autoprinter_store import AutoPrinterStore, StoreError


PRINTER = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
PROFILE = "treed-v2"
ASSET = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
RUNS = ["33333333-3333-4333-8333-333333333333",
        "44444444-4444-4444-8444-444444444444",
        "55555555-5555-4555-8555-555555555555"]
DATA = b"G1 X10 Y10\n"
BOOT_1 = "11111111-1111-4111-8111-111111111111"
BOOT_2 = "22222222-2222-4222-8222-222222222222"


class _Device:
    is_live = True

    async def inspect(self):
        raise AssertionError("disabled executor must not inspect the printer")


class _FakeDevice:
    is_live = False

    def __init__(self):
        self.state = "idle"
        self.filename = None
        self.starts = []
        self.pause_calls = 0
        self.cancel_calls = 0
        self.fail_next_start = False
        self.fail_next_pause = False
        self.fail_next_cancel = False

    async def inspect(self):
        return {"ready": True, "state": self.state, "filename": self.filename,
                "job_id": None, "queue_empty": True, "queue_safe": True,
                "service_active": False, "phase": "idle"}

    async def prepare(self, run, path):
        return f"treed-autoprinter/{run['binding_epoch']}-{run['run_id']}-{run['sha256']}.gcode"

    async def start(self, filename):
        self.starts.append(filename)
        self.filename = filename
        self.state = "printing"
        if self.fail_next_start:
            self.fail_next_start = False
            raise RuntimeError("response lost after start")

    async def reconcile(self, run):
        return {**(await self.inspect()), "evidence_source": "runtime"}

    async def pause(self):
        self.pause_calls += 1
        if self.fail_next_pause:
            self.fail_next_pause = False
            raise RuntimeError("pause outcome lost")
        self.state = "paused"

    async def cancel(self):
        self.cancel_calls += 1
        if self.fail_next_cancel:
            self.fail_next_cancel = False
            raise RuntimeError("cancel outcome lost")
        self.state = "cancelled"


class _FakeBedChecker:
    live_qualified = False
    model_version = "fake-v1"
    allowed_model_versions = ("fake-v1",)

    def __init__(self, printer_id):
        self.printer_id = printer_id
        self.verdict = "empty"
        self.bad_capture = False
        self.fail = False
        self.reason = None
        self.counter = 0
        self.contexts = []

    async def check(self, after_run_id, bed_epoch, boot_id):
        if self.fail:
            raise RuntimeError("provider unavailable")
        self.contexts.append(after_run_id)
        self.counter += 1
        captured = ("2000-01-01T00:00:00Z" if self.bad_capture else
                    datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"))
        return {"protocol_version": 1, "printer_id": self.printer_id,
                "after_run_id": after_run_id, "check_id": str(uuid.uuid4()),
                "bed_epoch": bed_epoch, "agent_boot_id": boot_id,
                "verdict": self.verdict, "model_version": "fake-v1",
                "captured_at": captured, "confidence": None, "reason": self.reason}


class AutoPrinterExecutionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = AutoPrinterStore(Path(self.temp.name), PRINTER, PROFILE)

    async def asyncTearDown(self):
        self.store.close()
        self.temp.cleanup()

    async def seed_queue(self, run_ids=RUNS):
        digest = hashlib.sha256(DATA).hexdigest()
        await asyncio.to_thread(self.store.update_binding, 1, 1, "paired", 1)
        await asyncio.to_thread(self.store.store_asset,
                                {"asset_id": ASSET, "sha256": digest, "size_bytes": len(DATA),
                                 "profile_id": PROFILE,
                                 "download_path": f"api/autoprinter/device/files/{ASSET}"}, BytesIO(DATA))
        await asyncio.to_thread(self.store.confirm_receipt, ASSET)
        command = {"command_id": "66666666-6666-4666-8666-666666666666", "type": "enqueue_series",
                   "control_epoch": 1, "expires_at": "2099-01-01T00:00:00Z", "queue_revision": 2,
                   "state": "created", "payload": {"asset_id": ASSET, "sha256": digest,
                   "size_bytes": len(DATA), "profile_id": PROFILE, "series_id": "77777777-7777-4777-8777-777777777777",
                   "copies_per_run": 1, "completion_profile": {"version": 1, "profile_id": PROFILE, "auto_remove": False},
                   "run_ids": list(run_ids), "binding_epoch": 1, "previous_queue_revision": 1,
                   "queue_revision": 2}}
        result = await asyncio.to_thread(self.store.apply_command, command)
        self.assertEqual("executed", result["state"])

    async def online_executor(self, device=None, checker=None, boot=BOOT_1):
        device = device or _FakeDevice()
        checker = checker or _FakeBedChecker(PRINTER)
        executor = AutoPrinterExecutor(self.store, device, boot, enabled=True, bed_checker=checker)
        await executor.initialize()
        submitted = await executor.sync_fields()
        session = await asyncio.to_thread(self.store.session)
        submitted["control_epoch"] = session["control_epoch"]
        submitted["binding_epoch"] = session["binding_epoch"]
        await executor.acknowledge({"control_epoch": session["control_epoch"],
                                    "binding_epoch": session["binding_epoch"],
                                    "ack_event_ids": [], "ignored_event_ids": []}, submitted)
        await executor.local_action({"type": "resume_queue"})
        return executor, device, checker

    async def test_restart_drains_immutable_prior_boot_events(self):
        await asyncio.to_thread(self.store.execution_set_boot, BOOT_1)
        event = await asyncio.to_thread(
            self.store.execution_append_event, BOOT_1, 1, "run_state",
            "2026-10-18T00:00:00Z", None, {"state": "queued"})

        await asyncio.to_thread(self.store.close)
        self.store = AutoPrinterStore(Path(self.temp.name), PRINTER, PROFILE)
        retired = await asyncio.to_thread(self.store.execution_set_boot, BOOT_2)

        self.assertEqual(BOOT_1, retired["boot_id"])
        self.assertEqual(event, retired["pending_events"][0])
        with self.assertRaises(StoreError):
            await asyncio.to_thread(
                self.store.execution_append_event, BOOT_2, 1, "run_state",
                "2026-10-18T00:00:01Z", None, {"state": "queued"})

    async def test_disabled_executor_never_calls_live_device(self):
        executor = AutoPrinterExecutor(self.store, _Device(), BOOT_1, enabled=False)
        await executor.initialize()
        await executor.tick()
        fields = await executor.sync_fields()

        self.assertEqual(BOOT_1, fields["agent_boot_id"])
        self.assertEqual([], fields["events"])
        self.assertFalse((await executor.status())["execution_enabled"])

    async def test_acknowledgement_is_limited_to_exact_submitted_event_ids(self):
        await asyncio.to_thread(self.store.execution_set_boot, BOOT_1)
        await asyncio.to_thread(self.store.update_binding, 1, 1, "paired", 1)
        event = await asyncio.to_thread(
            self.store.execution_append_event, BOOT_1, 1, "run_state",
            "2026-10-18T00:00:00Z", None, {"state": "queued"})
        executor = AutoPrinterExecutor(self.store, _Device(), BOOT_1)
        await executor.initialize()

        submitted = {"agent_boot_id": BOOT_1, "control_epoch": 1, "binding_epoch": 1,
                     "events": [event], "acknowledged_event_ids": []}
        with self.assertRaises(StoreError):
            await executor.acknowledge({"control_epoch": 1, "binding_epoch": 1,
                                        "ack_event_ids": [str(uuid.uuid4())], "ignored_event_ids": []}, submitted)
        pending = await asyncio.to_thread(self.store.execution_events_pending, BOOT_1)
        self.assertEqual([event["event_id"]], [item["event_id"] for item in pending])

        await executor.acknowledge({"control_epoch": 1, "binding_epoch": 1,
                                    "ack_event_ids": [event["event_id"]], "ignored_event_ids": []}, submitted)
        self.assertEqual([], await asyncio.to_thread(self.store.execution_events_pending, BOOT_1))
        self.assertEqual([event["event_id"]], await asyncio.to_thread(self.store.execution_pending_receipts, BOOT_1))

    async def test_receipt_only_reply_confirms_submitted_receipt_even_when_repeated_in_ack_ids(self):
        await asyncio.to_thread(self.store.execution_set_boot, BOOT_1)
        await asyncio.to_thread(self.store.update_binding, 1, 1, "paired", 1)
        event = await asyncio.to_thread(self.store.execution_append_event, BOOT_1, 1, "run_state",
                                        "2026-10-18T00:00:00Z", None, {"state": "queued"})
        executor = AutoPrinterExecutor(self.store, None, BOOT_1)
        await executor.initialize()
        first = {"agent_boot_id": BOOT_1, "control_epoch": 1, "binding_epoch": 1,
                 "events": [event], "acknowledged_event_ids": []}
        await executor.acknowledge({"control_epoch": 1, "binding_epoch": 1,
                                    "ack_event_ids": [event["event_id"]], "ignored_event_ids": []}, first)

        receipt_only = await executor.sync_fields()
        receipt_only["control_epoch"] = 1
        receipt_only["binding_epoch"] = 1
        receipt_id = receipt_only["acknowledged_event_ids"][0]
        await executor.acknowledge({"control_epoch": 1, "binding_epoch": 1,
                                    "ack_event_ids": [receipt_id], "ignored_event_ids": []}, receipt_only)

        self.assertEqual([], await asyncio.to_thread(self.store.execution_pending_receipts, BOOT_1))

    async def test_stage1_remote_command_dedup_keeps_original_body_hash(self):
        await asyncio.to_thread(self.store.update_binding, 1, 1, "paired", 1)
        command = {"command_id": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee", "type": "pause_queue",
                   "control_epoch": 1, "expires_at": "2099-01-01T00:00:00Z",
                   "state": "created", "payload": {}}
        accepted = await asyncio.to_thread(self.store.apply_command, command)
        executor = AutoPrinterExecutor(self.store, _Device(), BOOT_1)

        replay = await executor.command(dict(command, state="delivered"))

        self.assertEqual(accepted, replay)

    async def test_stale_queue_revision_rejection_result_has_backend_safe_shape(self):
        await asyncio.to_thread(self.store.update_binding, 1, 1, "paired", 1)
        executor = AutoPrinterExecutor(self.store, _Device(), BOOT_1)
        command = {"command_id": "ffffffff-ffff-4fff-8fff-ffffffffffff", "type": "pause_queue",
                   "control_epoch": 1, "queue_revision": 9,
                   "expires_at": "2099-01-01T00:00:00Z", "payload": {}}

        result = await executor.command(command)

        self.assertEqual("rejected", result["state"])
        self.assertEqual({"code"}, set(result["result"]))
        self.assertEqual(result["result"], (await asyncio.to_thread(self.store.pending_results))[0]["result"])

    async def test_wire_snapshot_uses_only_backend_snapshot_keys(self):
        executor = AutoPrinterExecutor(self.store, None, BOOT_1)
        fields = await executor.sync_fields()

        self.assertEqual({"observed_at", "bed_epoch", "queue_control", "current_run_id", "run_state",
                          "local_pause", "ready", "print_paused", "capabilities", "model_version"},
                         set(fields["snapshot"]))

    async def test_ignored_event_persistently_blocks_protocol(self):
        await asyncio.to_thread(self.store.execution_set_boot, BOOT_1)
        await asyncio.to_thread(self.store.update_binding, 1, 1, "paired", 1)
        event = await asyncio.to_thread(self.store.execution_append_event, BOOT_1, 1, "run_state",
                                        "2026-10-18T00:00:00Z", None, {"state": "queued"})
        executor = AutoPrinterExecutor(self.store, None, BOOT_1)
        await executor.initialize()
        submitted = {"agent_boot_id": BOOT_1, "control_epoch": 1, "binding_epoch": 1,
                     "events": [event], "acknowledged_event_ids": []}

        with self.assertRaises(StoreError):
            await executor.acknowledge({"control_epoch": 1, "binding_epoch": 1,
                                        "ack_event_ids": [event["event_id"]],
                                        "ignored_event_ids": [{"event_id": event["event_id"], "reason": "conflict"}]}, submitted)
        await executor.acknowledge({"control_epoch": 1, "binding_epoch": 1,
                                    "ack_event_ids": [event["event_id"]], "ignored_event_ids": []}, submitted)

        self.assertEqual("1", await asyncio.to_thread(self.store.execution_get_meta, "protocol_recovery_required"))
        self.assertEqual([event["event_id"]], [e["event_id"] for e in await asyncio.to_thread(self.store.execution_events_pending, BOOT_1)])

    async def test_three_queued_runs_start_once_each_and_no_fourth_exists(self):
        await self.seed_queue()
        executor, device, _ = await self.online_executor()

        for run_id in RUNS:
            await executor.tick()
            self.assertEqual(run_id, (await executor.status())["current_run_id"])
            self.assertEqual(run_id, (await asyncio.to_thread(self.store.execution_last_started))["run_id"])
            await executor.tick()  # Runtime observation, not start ACK, advances preparing to printing.
            self.assertEqual("printing", (await asyncio.to_thread(self.store.execution_state, run_id))["state"])
            device.state = "complete"
            await executor.tick()
            self.assertEqual("completed", (await asyncio.to_thread(self.store.execution_state, run_id))["state"])

        self.assertEqual(3, len(device.starts))
        self.assertEqual(RUNS, [run["run_id"] for run in await asyncio.to_thread(self.store.queue)])
        events = await asyncio.to_thread(self.store.execution_events_pending, BOOT_1)
        proof_fields = {"protocol_version", "printer_id", "after_run_id", "check_id", "bed_epoch",
                        "agent_boot_id", "verdict", "confidence", "model_version", "captured_at", "reason"}
        checks = [event for event in events if event["type"] == "bed_check"]
        self.assertEqual(6, len(checks))  # One start proof and one completion proof per run.
        self.assertTrue(all(set(event["payload"]) == proof_fields for event in checks))
        self.assertTrue(all(event["payload"]["captured_at"].endswith("Z") for event in checks))
        await executor.tick()
        self.assertEqual(3, len(device.starts))

    async def test_lost_start_response_restarts_as_uncertain_and_reconciles_without_replay(self):
        await self.seed_queue([RUNS[0]])
        device = _FakeDevice()
        device.fail_next_start = True
        executor, device, checker = await self.online_executor(device=device)
        await executor.tick()
        self.assertEqual(1, len(device.starts))
        self.assertEqual("start_outcome_uncertain", (await executor.status())["blocked_reason"])

        await asyncio.to_thread(self.store.close)
        self.store = AutoPrinterStore(Path(self.temp.name), PRINTER, PROFILE)
        restarted = AutoPrinterExecutor(self.store, device, BOOT_2, enabled=True, bed_checker=checker)
        await restarted.initialize()
        # Retire the old boot by replaying its immutable events and their receipts.
        old = await restarted.sync_fields()
        old["control_epoch"] = 1
        old["binding_epoch"] = 1
        await restarted.acknowledge({"control_epoch": 1, "binding_epoch": 1,
                                     "ack_event_ids": [e["event_id"] for e in old["events"]],
                                     "ignored_event_ids": []}, old)
        receipts = await restarted.sync_fields()
        receipts["control_epoch"] = 1
        receipts["binding_epoch"] = 1
        await restarted.acknowledge({"control_epoch": 1, "binding_epoch": 1,
                                     "ack_event_ids": [], "ignored_event_ids": []}, receipts)
        new_boot = await restarted.sync_fields()
        new_boot["control_epoch"] = 1
        new_boot["binding_epoch"] = 1
        await restarted.acknowledge({"control_epoch": 1, "binding_epoch": 1,
                                     "ack_event_ids": [], "ignored_event_ids": []}, new_boot)
        await restarted.tick()

        self.assertEqual(1, len(device.starts))
        self.assertEqual("printing", (await asyncio.to_thread(self.store.execution_state, RUNS[0]))["state"])

    async def test_invalid_bed_proofs_never_start_a_run(self):
        await self.seed_queue([RUNS[0]])
        executor, device, checker = await self.online_executor()

        for verdict, stale in (("occupied", False), ("unknown", False), ("empty", True)):
            checker.verdict = verdict
            checker.bad_capture = stale
            await executor.tick()
            self.assertEqual([], device.starts)
            self.assertEqual("queued", (await asyncio.to_thread(self.store.execution_state, RUNS[0]))["state"])
        checker.verdict = "empty"
        checker.bad_capture = False
        checker.allowed_model_versions = ()
        await executor.tick()
        self.assertEqual([], device.starts)
        checker.allowed_model_versions = ("fake-v1",)
        checker.fail = True
        await executor.tick()
        self.assertEqual([], device.starts)

    async def test_remote_resume_cannot_clear_local_pause(self):
        await self.seed_queue([RUNS[0]])
        executor, _, _ = await self.online_executor()
        await executor.local_action({"type": "pause_queue"})
        command = {"command_id": "88888888-8888-4888-8888-888888888888", "type": "resume_queue",
                   "control_epoch": 1, "queue_revision": 2, "expires_at": "2099-01-01T00:00:00Z", "payload": {}}

        result = await executor.command(command)

        self.assertEqual("executed", result["state"])
        self.assertTrue((await asyncio.to_thread(self.store.session))["local_pause"])
        await executor.local_action({"type": "resume_queue"})
        self.assertFalse((await asyncio.to_thread(self.store.session))["local_pause"])

    async def test_explicit_resume_clears_stop_after_current(self):
        await self.seed_queue([RUNS[0]])
        executor, device, _ = await self.online_executor()
        await executor.local_action({"type": "stop_after_current"})
        await executor.local_action({"type": "resume_queue"})

        await executor.tick()

        self.assertEqual(1, len(device.starts))

    async def test_external_print_blocks_start_and_revocation_hides_old_queue(self):
        await self.seed_queue([RUNS[0]])
        executor, device, _ = await self.online_executor()
        device.state = "printing"
        device.filename = "external.gcode"
        await executor.tick()
        self.assertEqual([], device.starts)

        await asyncio.to_thread(self.store.update_binding, 2, 2, "revoked", 2)
        await executor.tick()
        self.assertEqual([], await asyncio.to_thread(self.store.queue))
        self.assertEqual([], device.starts)

    async def test_old_completion_filename_cannot_complete_current_run(self):
        await self.seed_queue([RUNS[0]])
        executor, device, _ = await self.online_executor()
        await executor.tick()
        device.state = "complete"
        device.filename = "treed-autoprinter/1-old-run-" + "0" * 64 + ".gcode"
        await executor.tick()

        self.assertNotEqual("completed", (await asyncio.to_thread(self.store.execution_state, RUNS[0]))["state"])
        self.assertEqual("reconciliation_mismatch", (await executor.status())["blocked_reason"])

    async def test_current_physical_action_requires_runtime_filename_match(self):
        await self.seed_queue([RUNS[0]])
        executor, device, _ = await self.online_executor()
        await executor.tick()
        device.filename = "external-or-stale.gcode"
        command = {"command_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc", "type": "cancel_current_print",
                   "control_epoch": 1, "queue_revision": 2, "expires_at": "2099-01-01T00:00:00Z",
                   "run_id": RUNS[0], "payload": {"run_id": RUNS[0]}}

        result = await executor.command(command)

        self.assertEqual("rejected", result["state"])
        self.assertEqual(0, device.cancel_calls)

    async def test_revoked_namespace_snapshot_hides_old_run_identity(self):
        await self.seed_queue([RUNS[0]])
        executor, _, _ = await self.online_executor()
        await executor.tick()
        await asyncio.to_thread(self.store.update_binding, 2, 1, "revoked", 2)

        snapshot = await executor.status()

        self.assertIsNone(snapshot["current_run_id"])
        self.assertIsNone(snapshot["run_state"])
        self.assertFalse(snapshot["ready"])

    async def test_physical_action_id_deduplicates_and_uncertain_failure_is_not_replayed(self):
        await self.seed_queue([RUNS[0]])
        executor, device, _ = await self.online_executor()
        await executor.tick()
        action = {"command_id": "99999999-9999-4999-8999-999999999999", "type": "pause_current_print",
                  "control_epoch": 1, "queue_revision": 2, "expires_at": "2099-01-01T00:00:00Z",
                  "run_id": RUNS[0], "payload": {"run_id": RUNS[0]}}
        first = await executor.command(action)
        second = await executor.command(action)
        self.assertEqual("executed", first["state"])
        self.assertEqual(first["state"], second["state"])
        self.assertEqual(1, device.pause_calls)

        failing = dict(action, command_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", type="cancel_current_print")
        device.fail_next_cancel = True
        first_failure = await executor.command(failing)
        second_failure = await executor.command(failing)
        self.assertEqual("uncertain", first_failure["state"])
        self.assertEqual(first_failure["state"], second_failure["state"])
        self.assertEqual(1, device.cancel_calls)

    async def test_local_physical_action_targets_current_run_and_reuses_action_id(self):
        await self.seed_queue([RUNS[0]])
        executor, device, _ = await self.online_executor()
        await executor.tick()
        action = {"type": "pause_current_print", "run_id": RUNS[0],
                  "action_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"}

        await executor.local_action(action)
        await executor.local_action(action)

        self.assertEqual(1, device.pause_calls)

    async def test_local_recheck_completes_verifying_run_with_one_fresh_bed_event(self):
        await self.seed_queue([RUNS[0]])
        executor, _, _ = await self.online_executor()
        run = (await asyncio.to_thread(self.store.queue))[0]
        proof = await executor._bed_proof(None)
        filename = f"treed-autoprinter/1-{RUNS[0]}-{run['sha256']}.gcode"
        await asyncio.to_thread(self.store.execution_start_intent, RUNS[0], filename,
                                proof["bed_epoch"], proof, BOOT_1, 1,
                                "2026-10-18T00:00:00Z")
        await asyncio.to_thread(self.store.execution_transition, RUNS[0], "printing", BOOT_1, 1,
                                "2026-10-18T00:00:01Z")
        await asyncio.to_thread(self.store.execution_transition, RUNS[0], "verifying", BOOT_1, 1,
                                "2026-10-18T00:00:02Z")
        before = len([event for event in await asyncio.to_thread(self.store.execution_events_pending, BOOT_1)
                      if event["type"] == "bed_check"])

        await executor.local_action({"type": "recheck_bed"})

        after_events = await asyncio.to_thread(self.store.execution_events_pending, BOOT_1)
        after = len([event for event in after_events if event["type"] == "bed_check"])
        self.assertEqual("completed", (await asyncio.to_thread(self.store.execution_state, RUNS[0]))["state"])
        self.assertEqual(before + 1, after)

    async def test_candidate_empty_cannot_unlock_live_start(self):
        await self.seed_queue([RUNS[0]])
        device = _FakeDevice()
        device.is_live = True
        checker = _FakeBedChecker(PRINTER)
        checker.live_qualified = False
        executor, device, _ = await self.online_executor(device=device, checker=checker)

        self.assertIsNone(await executor._bed_proof(None, require_empty=False))
        executor.device = None
        self.assertIsNone(await executor._bed_proof(None, require_empty=False))
        executor.device = device
        await executor.tick()

        self.assertEqual([], device.starts)
        self.assertEqual("bed_proof_unavailable", (await executor.status())["blocked_reason"])

    async def test_candidate_occupied_or_unknown_recheck_never_completes_verifying_run(self):
        await self.seed_queue([RUNS[0]])
        executor, _, checker = await self.online_executor()
        run = (await asyncio.to_thread(self.store.queue))[0]
        proof = await executor._bed_proof(None)
        filename = f"treed-autoprinter/1-{RUNS[0]}-{run['sha256']}.gcode"
        await asyncio.to_thread(self.store.execution_start_intent, RUNS[0], filename,
                                proof["bed_epoch"], proof, BOOT_1, 1,
                                "2026-10-18T00:00:00Z")
        await asyncio.to_thread(self.store.execution_transition, RUNS[0], "printing", BOOT_1, 1,
                                "2026-10-18T00:00:01Z")
        await asyncio.to_thread(self.store.execution_transition, RUNS[0], "verifying", BOOT_1, 1,
                                "2026-10-18T00:00:02Z")
        checker.live_qualified = False
        before_ids = {event["event_id"] for event in await asyncio.to_thread(
            self.store.execution_events_pending, BOOT_1)}

        for verdict in ("unknown", "occupied"):
            checker.verdict = verdict
            await executor.local_action({"type": "recheck_bed"})
            state = await asyncio.to_thread(self.store.execution_state, RUNS[0])
            self.assertEqual("verifying", state["state"])

        events = await asyncio.to_thread(self.store.execution_events_pending, BOOT_1)
        evidence = [event for event in events if event["type"] == "bed_check"
                    and event["event_id"] not in before_ids]
        self.assertEqual(["unknown", "occupied"], [event["payload"]["verdict"] for event in evidence])

    async def test_camera_error_unknown_is_fresh_utc_evidence_in_durable_event(self):
        await self.seed_queue([RUNS[0]])
        executor, _, checker = await self.online_executor()
        checker.verdict = "unknown"
        checker.reason = "camera_unavailable"
        before_ids = {event["event_id"] for event in await asyncio.to_thread(
            self.store.execution_events_pending, BOOT_1)}

        await executor.local_action({"type": "recheck_bed"})

        events = await asyncio.to_thread(self.store.execution_events_pending, BOOT_1)
        evidence = [event for event in events if event["type"] == "bed_check"
                    and event["event_id"] not in before_ids]
        self.assertEqual(1, len(evidence))
        proof = evidence[0]["payload"]
        self.assertEqual("unknown", proof["verdict"])
        self.assertEqual("camera_unavailable", proof["reason"])
        self.assertTrue(proof["captured_at"].endswith("Z"))

    async def test_remote_candidate_recheck_is_durable_without_completing_run(self):
        await self.seed_queue([RUNS[0]])
        executor, _, checker = await self.online_executor()
        run = (await asyncio.to_thread(self.store.queue))[0]
        proof = await executor._bed_proof(None)
        filename = f"treed-autoprinter/1-{RUNS[0]}-{run['sha256']}.gcode"
        await asyncio.to_thread(self.store.execution_start_intent, RUNS[0], filename,
                                proof["bed_epoch"], proof, BOOT_1, 1,
                                "2026-10-18T00:00:00Z")
        await asyncio.to_thread(self.store.execution_transition, RUNS[0], "printing", BOOT_1, 1,
                                "2026-10-18T00:00:01Z")
        await asyncio.to_thread(self.store.execution_transition, RUNS[0], "verifying", BOOT_1, 1,
                                "2026-10-18T00:00:02Z")
        checker.live_qualified = False
        checker.verdict = "unknown"
        before_ids = {event["event_id"] for event in await asyncio.to_thread(
            self.store.execution_events_pending, BOOT_1)}
        session = await asyncio.to_thread(self.store.session)
        command = {"command_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc", "type": "recheck_bed",
                   "control_epoch": session["control_epoch"], "queue_revision": session["queue_revision"],
                   "expires_at": "2099-01-01T00:00:00Z",
                   "payload": {}}

        first = await executor.command(command)
        second = await executor.command(command)

        self.assertEqual("executed", first["state"])
        self.assertEqual(first, second)
        self.assertEqual("verifying", (await asyncio.to_thread(
            self.store.execution_state, RUNS[0]))["state"])
        events = await asyncio.to_thread(self.store.execution_events_pending, BOOT_1)
        evidence = [event for event in events if event["type"] == "bed_check"
                    and event["event_id"] not in before_ids]
        self.assertEqual(["unknown"], [event["payload"]["verdict"] for event in evidence])

    async def test_recheck_waits_for_previous_boot_outbox_to_drain(self):
        await self.seed_queue([RUNS[0]])
        checker = _FakeBedChecker(PRINTER)
        previous = AutoPrinterExecutor(self.store, _FakeDevice(), BOOT_1, enabled=True,
                                       bed_checker=checker)
        await previous.initialize()
        await previous._emit("run_state", None, {"state": "queued"})
        executor = AutoPrinterExecutor(self.store, _FakeDevice(), BOOT_2, enabled=True,
                                       bed_checker=checker)
        await executor.initialize()
        self.assertEqual(BOOT_1, executor._draining_boot_id)
        before = await asyncio.to_thread(self.store.execution_events_pending, BOOT_1)

        await executor.local_action({"type": "recheck_bed"})
        session = await asyncio.to_thread(self.store.session)
        remote = {"command_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc", "type": "recheck_bed",
                  "control_epoch": session["control_epoch"], "queue_revision": session["queue_revision"],
                  "expires_at": "2099-01-01T00:00:00Z", "payload": {}}
        result = await executor.command(remote)

        self.assertEqual("rejected", result["state"])
        self.assertEqual("bed_check_unavailable", result["result"]["code"])
        self.assertEqual(0, checker.counter)
        self.assertEqual(before, await asyncio.to_thread(self.store.execution_events_pending, BOOT_1))

        for _ in range(3):
            submitted = await executor.sync_fields()
            submitted.update(control_epoch=session["control_epoch"], binding_epoch=session["binding_epoch"])
            await executor.acknowledge({"control_epoch": session["control_epoch"],
                                        "binding_epoch": session["binding_epoch"],
                                        "ack_event_ids": ([event["event_id"] for event in submitted["events"]]
                                                          + submitted["acknowledged_event_ids"]),
                                        "ignored_event_ids": []}, submitted)
            if executor._draining_boot_id is None:
                break

        self.assertIsNone(executor._draining_boot_id)
        await executor.local_action({"type": "recheck_bed"})
        remote["command_id"] = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
        remote["queue_revision"] = session["queue_revision"]
        self.assertEqual("rejected", (await executor.command(remote))["state"])
        self.assertEqual(0, checker.counter)
        submitted = await executor.sync_fields()
        submitted.update(control_epoch=session["control_epoch"], binding_epoch=session["binding_epoch"])
        await executor.acknowledge({"control_epoch": session["control_epoch"],
                                    "binding_epoch": session["binding_epoch"],
                                    "ack_event_ids": ([event["event_id"] for event in submitted["events"]]
                                                      + submitted["acknowledged_event_ids"]),
                                    "ignored_event_ids": []}, submitted)
        await executor.local_action({"type": "recheck_bed"})
        remote["command_id"] = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
        self.assertEqual("executed", (await executor.command(remote))["state"])
        self.assertEqual(2, checker.counter)
        events = await asyncio.to_thread(self.store.execution_events_pending, BOOT_2)
        self.assertEqual(BOOT_2, events[-1]["payload"]["agent_boot_id"])

    async def test_reused_local_action_id_with_different_operation_conflicts(self):
        await self.seed_queue([RUNS[0]])
        executor, device, _ = await self.online_executor()
        await executor.tick()
        action_id = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
        await executor.local_action({"type": "pause_current_print", "run_id": RUNS[0], "action_id": action_id})

        with self.assertRaises(StoreError) as raised:
            await executor.local_action({"type": "cancel_current_print", "run_id": RUNS[0], "action_id": action_id})

        self.assertEqual("command_conflict", raised.exception.code)
        self.assertEqual(0, device.cancel_calls)


if __name__ == "__main__":
    unittest.main()
