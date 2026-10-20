"""Точечные проверки durable-контракта AutoPrinterStore без Moonraker и сети."""

# Блок 1: Подготовка изолированного временного хранилища.
from __future__ import annotations

import hashlib
import sqlite3
from io import BytesIO
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from moonraker.components.treed_autoprinter_store import AutoPrinterStore, StoreError
from moonraker.components import treed_autoprinter_store as store_module


PRINTER = "11111111-1111-4111-8111-111111111111"
ASSET = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
RUN_1 = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
RUN_2 = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
PROFILE = "treed_v2_corexy_v1"
DATA = b"G1 X10 Y10\n"


class AutoPrinterStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = AutoPrinterStore(self.root, PRINTER, PROFILE, max_asset_bytes=128, max_total_bytes=256, max_runs=4)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def paired(self):
        self.store.update_binding(1, 1, "paired", 1)

    def test_status_exposes_only_bounded_current_binding_queue_projection(self):
        unpaired = self.store.status()
        self.assertEqual(([], 0, False),
                         (unpaired["queue"], unpaired["queue_total"], unpaired["queue_truncated"]))
        self.paired()
        series_id = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
        payload = '{"series_id":"%s","copies_per_run":2,"asset_id":"secret","path":"private"}' % series_id
        with self.store._transaction():
            self.store._conn.executemany(
                "INSERT INTO runs(binding_epoch,run_id,payload,position) VALUES(1,?,?,?)",
                [(f"{i:08x}-0000-4000-8000-{i:012x}", payload, i) for i in range(1, 1002)])
            self.store._conn.execute(
                "INSERT INTO execution_runs(binding_epoch,run_id,state,filename) VALUES(1,?,'printing','private.gcode')",
                ("00000001-0000-4000-8000-000000000001",))
        status = self.store.status()

        self.assertEqual(1001, status["queue_total"])
        self.assertTrue(status["queue_truncated"])
        self.assertEqual(1000, len(status["queue"]))
        self.assertEqual(["run_id", "position", "state", "series_id", "copies_per_run"],
                         list(status["queue"][0]))
        self.assertEqual("printing", status["queue"][0]["state"])
        self.assertEqual(list(range(1, 1001)), [run["position"] for run in status["queue"]])
        self.assertNotIn("path", status["queue"][0])
        self.store.update_binding(2, 1, "revoked", 1)
        status = self.store.status()
        self.assertEqual(([], 0, False),
                         (status["queue"], status["queue_total"], status["queue_truncated"]))

    def test_status_keeps_legacy_queue_row_without_inventing_series_metadata(self):
        self.paired()
        self.store._conn.execute(
            "INSERT INTO runs(binding_epoch,run_id,payload,position) VALUES(1,?,?,1)",
            (RUN_1, '{"asset_id":"' + ASSET + '","sha256":"' + hashlib.sha256(DATA).hexdigest()
             + '","size_bytes":' + str(len(DATA)) + '}'))

        row = self.store.status()["queue"][0]

        self.assertEqual(RUN_1, row["run_id"])
        self.assertIsNone(row["series_id"])
        self.assertIsNone(row["copies_per_run"])

    def test_journal_limit_rolls_back_full_write_and_preserves_events_on_reopen(self):
        self.paired()
        boot = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        self.store.execution_set_boot(boot)
        event = self.store.execution_append_event(boot, 1, "bed_check", "2026-10-18T00:00:00Z",
                                                 None, {"verdict": "unknown"})
        page_size = self.store._conn.execute("PRAGMA page_size").fetchone()[0]
        limit = self.store.status()["journal"]["allocated_bytes"] + 3 * page_size
        self.store.close()
        self.store = AutoPrinterStore(self.root, PRINTER, PROFILE, max_journal_bytes=limit)
        with self.assertRaises(sqlite3.DatabaseError) as raised:
            self.store.execution_append_event(boot, 1, "bed_check", "2026-10-18T00:00:01Z",
                                             None, {"reason": "x" * (2 * limit)})
        self.assertEqual(raised.exception.sqlite_errorcode, sqlite3.SQLITE_FULL)
        self.assertEqual([event], self.store.execution_events_pending(boot))
        self.assertFalse(self.store._conn.in_transaction)
        self.assertLessEqual(self.store.db_path.stat().st_size, limit)
        self.store.execution_set_pause(local=True)
        self.store.close()
        self.store = AutoPrinterStore(self.root, PRINTER, PROFILE, max_journal_bytes=limit)
        self.assertEqual([event], self.store.execution_events_pending(boot))
        self.assertEqual(limit, self.store.status()["journal"]["limit_bytes"])
        self.assertTrue(self.store.session()["local_pause"])

    def test_lowering_journal_limit_rejects_without_truncation_or_lock_leak(self):
        self.paired()
        before = self.store.db_path.read_bytes()
        self.store.close()
        with self.assertRaisesRegex(StoreError, "journal_limit_exceeded"):
            AutoPrinterStore(self.root, PRINTER, PROFILE, max_journal_bytes=4096)
        self.assertEqual(before, self.store.db_path.read_bytes())
        self.store = AutoPrinterStore(self.root, PRINTER, PROFILE)
        self.assertEqual("paired", self.store.session()["binding_state"])
        self.assertIn("journal", self.store.status())
        self.store.update_binding(2, 1, "revoked", 1)
        self.assertNotIn("journal", self.store.status())

    def transfer(self, data=DATA, asset_id=ASSET):
        return {"asset_id": asset_id, "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data),
                "profile_id": PROFILE, "download_path": f"api/autoprinter/device/files/{asset_id}"}

    def test_asset_receipt_runs_and_command_dedup_survive_reopen(self):
        self.paired()
        metadata = {key: self.transfer()[key] for key in ("asset_id", "sha256", "size_bytes")}
        self.store.store_asset(self.transfer(), BytesIO(DATA))
        self.assertEqual([metadata], self.store.pending_receipts())
        self.store.confirm_receipt(ASSET)
        command = {"command_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "type": "enqueue_series",
                   "control_epoch": 1, "expires_at": "2099-01-01T00:00:00Z",
                   "queue_revision": 2, "state": "created",
                   "payload": {"asset_id": ASSET, "sha256": hashlib.sha256(DATA).hexdigest(), "size_bytes": len(DATA),
                               "profile_id": PROFILE, "series_id": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
                               "copies_per_run": 1, "completion_profile": {"version": 1, "profile_id": PROFILE, "auto_remove": False},
                               "run_ids": [RUN_1, RUN_2], "binding_epoch": 1,
                               "previous_queue_revision": 1, "queue_revision": 2}}
        accepted = self.store.apply_command(command)
        self.assertEqual("executed", accepted["state"])
        self.store.close()
        self.store = AutoPrinterStore(self.root, PRINTER, PROFILE, max_asset_bytes=128, max_total_bytes=256, max_runs=4)
        self.assertEqual([RUN_1, RUN_2], [run["run_id"] for run in self.store.queue()])
        self.store.update_binding(2, 1, "paired", 1)
        replay = dict(command, state="delivered")
        self.assertEqual(accepted, self.store.apply_command(replay))
        self.assertEqual(2, self.store.session()["queue_revision"])
        self.assertEqual("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee", self.store.queue()[0]["series_id"])
        self.assertEqual(1, self.store.queue()[0]["copies_per_run"])

    def test_binding_epoch_keeps_old_files_but_hides_them(self):
        self.paired()
        self.store.store_asset(self.transfer(), BytesIO(DATA))
        command = {"command_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "type": "pause_queue",
                   "control_epoch": 1, "expires_at": "2099-01-01T00:00:00Z", "state": "created", "payload": {}}
        self.store.apply_command(command)
        self.assertEqual(1, len(self.store.pending_results()))
        self.store.update_binding(2, 2, "paired", 1)
        self.assertEqual([], self.store.pending_receipts())
        self.assertEqual([], self.store.queue())
        self.assertEqual([], self.store.pending_results())
        self.assertEqual(0, self.store.status()["asset_count"])
        self.assertEqual(0, self.store.status()["run_count"])
        self.assertTrue((self.root / "bindings" / "1" / f"{ASSET}.gcode").exists())
        self.assertEqual(1, self.store.session()["queue_revision"])

    def test_unlink_intent_survives_restart_and_new_epoch_hides_account_data(self):
        self.paired()
        profile = {"display_name": "Owner", "avatar_url": "https://treed.example/avatar.jpg"}
        self.store.update_binding(1, 1, "paired", 1,
                                  profile, "Printer")
        self.store._conn.execute(
            "INSERT INTO runs(binding_epoch,run_id,payload,position) VALUES(1,?,?,1)",
            (RUN_1, "{}"))

        self.assertEqual(1, self.store.request_unlink())
        self.store.execution_set_pause(local=False, remote=False)
        self.assertTrue(self.store.session()["local_pause"])
        self.assertTrue(self.store.session()["unlink_pending"])
        self.assertEqual(profile, self.store.status()["owner_profile"])
        self.store.close()
        self.store = AutoPrinterStore(self.root, PRINTER, PROFILE,
                                      max_asset_bytes=128, max_total_bytes=256, max_runs=4)

        self.assertTrue(self.store.session()["unlink_pending"])
        self.assertEqual(profile, self.store.status()["owner_profile"])
        self.assertEqual("request", self.store.unlink_intent()["phase"])
        self.store.update_binding(2, 2, "unpaired", 1)

        status = self.store.status()
        self.assertFalse(status["unlink_pending"])
        self.assertIsNone(status["owner_profile"])
        self.assertEqual("Printer", status["display_name"])
        self.assertEqual([], status["queue"])
        self.assertEqual(1, self.store._conn.execute(
            "SELECT COUNT(*) FROM runs WHERE binding_epoch=1 AND run_id=?", (RUN_1,)
        ).fetchone()[0])

    def test_owner_avatar_validation_and_revocation(self):
        for avatar in ("javascript:alert(1)", "http://treed.example/a.jpg",
                       "https://user:pass@treed.example/a.jpg", "https://", 42,
                       "https://treed.example/\navatar.jpg", "https://treed.example/" + "x" * 2048):
            with self.subTest(avatar=avatar), self.assertRaises(StoreError):
                self.store.update_binding(1, 1, "paired", 1,
                                          owner_profile={"display_name": "Owner", "avatar_url": avatar})
        for profile in ({"display_name": "Owner"}, {"display_name": "Owner", "avatar_url": None},
                        {"display_name": "Owner", "avatar_url": "https://treed.example/a.jpg"}):
            self.store.update_binding(1, 1, "paired", 1, owner_profile=profile)
            self.assertEqual(profile, self.store.status()["owner_profile"])
        self.store.update_binding(1, 1, "revoked", 1)
        self.assertIsNone(self.store.status()["owner_profile"])

    def test_confirmed_unlink_isolates_old_outbox_without_acknowledging_it(self):
        old_boot = "11111111-1111-4111-8111-111111111111"
        restart_boot = "33333333-3333-4333-8333-333333333333"
        self.paired()
        self.store.execution_set_boot(old_boot)
        old_acked = self.store.execution_append_event(
            old_boot, 1, "run_state", "2026-10-18T00:00:00Z", None, {"state": "printing"})
        old_pending = self.store.execution_append_event(
            old_boot, 1, "run_state", "2026-10-18T00:00:01Z", None, {"state": "failed"})
        self.store.execution_ack_events([old_acked["event_id"]], [old_acked["event_id"]], [], [], old_boot)
        self.store.request_unlink()

        rolled_boot = self.store.update_binding(2, 2, "unpaired", 1)
        self.assertTrue(isinstance(rolled_boot, str) and rolled_boot != old_boot)
        self.assertEqual([old_pending["event_id"]], [event["event_id"] for event in
                                                      self.store.execution_events_pending(old_boot)])
        self.assertEqual([old_acked["event_id"]], self.store.execution_pending_receipts(old_boot))
        self.store.update_binding(2, 2, "paired", 1, owner_profile={"display_name": "New owner"})
        new_event = self.store.execution_append_event(
            rolled_boot, 2, "run_state", "2026-10-18T00:00:02Z", None, {"state": "queued"})

        self.store.close()
        self.store = AutoPrinterStore(self.root, PRINTER, PROFILE,
                                      max_asset_bytes=128, max_total_bytes=256, max_runs=4)
        previous = self.store.execution_set_boot(restart_boot)
        self.assertEqual(rolled_boot, previous["boot_id"])
        self.assertEqual([new_event["event_id"]], [event["event_id"] for event in previous["pending_events"]])
        self.assertEqual([old_pending["event_id"]], [event["event_id"] for event in
                                                      self.store.execution_events_pending(old_boot)])
        self.assertEqual([old_acked["event_id"]], self.store.execution_pending_receipts(old_boot))

    def test_unknown_or_ahead_server_revision_requires_recovery(self):
        with self.assertRaises(StoreError) as raised:
            self.store.update_binding(1, 1, "paired", 2)
        self.assertEqual("queue_recovery_required", raised.exception.code)
        self.assertEqual("unpaired", self.store.session()["binding_state"])
        self.paired()
        self.store.close()
        self.store.db_path.unlink()
        self.store = AutoPrinterStore(self.root, PRINTER, PROFILE, max_asset_bytes=128, max_total_bytes=256, max_runs=4)
        with self.assertRaises(StoreError) as raised:
            self.store.update_binding(1, 1, "paired", 2)
        self.assertEqual("queue_recovery_required", raised.exception.code)

    def test_revocation_persists_new_epoch_without_exposing_or_deleting_old_queue(self):
        self.paired()
        self.store.store_asset(self.transfer(), BytesIO(DATA))
        self.store.confirm_receipt(ASSET)
        command = {"command_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "type": "enqueue_series",
                   "control_epoch": 1, "expires_at": "2099-01-01T00:00:00Z", "queue_revision": 2, "state": "created",
                   "payload": {"asset_id": ASSET, "sha256": hashlib.sha256(DATA).hexdigest(), "size_bytes": len(DATA),
                               "profile_id": PROFILE, "series_id": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
                               "copies_per_run": 1, "completion_profile": {"version": 1, "profile_id": PROFILE, "auto_remove": False},
                               "run_ids": [RUN_1], "binding_epoch": 1, "previous_queue_revision": 1, "queue_revision": 2}}
        self.assertEqual("executed", self.store.apply_command(command)["state"])
        self.store.update_binding(2, 2, "revoked", 2)
        self.assertEqual({"control_epoch": 2, "binding_epoch": 2, "binding_state": "revoked", "queue_revision": 1,
                          "local_pause": True, "remote_pause": False, "unlink_pending": False}, self.store.session())
        self.assertEqual([], self.store.queue())
        self.assertTrue((self.root / "bindings" / "1" / f"{ASSET}.gcode").exists())
        with self.assertRaises(StoreError) as raised:
            self.store.update_binding(2, 2, "paired", 2)
        self.assertEqual("queue_recovery_required", raised.exception.code)

    def test_wrong_hash_profile_size_and_traversal_are_rejected(self):
        self.paired()
        for transfer in [dict(self.transfer(), sha256="0" * 64), dict(self.transfer(), profile_id="other"), dict(self.transfer(), size_bytes=len(DATA) + 1), dict(self.transfer(), asset_id="../escape")]:
            with self.subTest(transfer=transfer), self.assertRaises(StoreError):
                self.store.store_asset(transfer, BytesIO(DATA))
        self.assertFalse((self.root.parent / "escape.gcode").exists())

    def test_quota_revisions_reorder_and_duplicate_ids(self):
        self.paired()
        self.store.store_asset(self.transfer(), BytesIO(DATA))
        self.store.confirm_receipt(ASSET)
        first = {"command_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "type": "enqueue_series",
                 "control_epoch": 1, "expires_at": "2099-01-01T00:00:00Z",
                 "queue_revision": 2, "state": "created",
                 "payload": {"asset_id": ASSET, "sha256": hashlib.sha256(DATA).hexdigest(), "size_bytes": len(DATA),
                             "profile_id": PROFILE, "series_id": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
                             "copies_per_run": 1, "completion_profile": {"version": 1, "profile_id": PROFILE, "auto_remove": False},
                             "run_ids": [RUN_1, RUN_2], "binding_epoch": 1,
                             "previous_queue_revision": 1, "queue_revision": 2}}
        self.assertEqual("executed", self.store.apply_command(first)["state"])
        conflict = dict(first, command_id="eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
        conflict["payload"] = dict(first["payload"], previous_queue_revision=1, queue_revision=2)
        self.assertEqual("uncertain", self.store.apply_command(conflict)["state"])
        reorder = {"command_id": "ffffffff-ffff-4fff-8fff-ffffffffffff", "type": "reorder_queue",
                   "control_epoch": 1, "expires_at": "2099-01-01T00:00:00Z",
                   "queue_revision": 3, "state": "created",
                   "payload": {"run_ids": [RUN_2, RUN_1], "binding_epoch": 1,
                               "previous_queue_revision": 2, "queue_revision": 3}}
        self.assertEqual("executed", self.store.apply_command(reorder)["state"])
        self.assertEqual([RUN_2, RUN_1], [run["run_id"] for run in self.store.queue()])
        expired = dict(reorder, command_id="77777777-7777-4777-8777-777777777777",
                       expires_at="2000-01-01T00:00:00Z")
        uncertain = self.store.apply_command(expired)
        self.assertEqual("uncertain", uncertain["state"])
        self.assertEqual("revision_conflict", uncertain["result"]["code"])
        self.assertEqual(3, uncertain["result"]["queue_revision"])
        same_run = dict(first, command_id="88888888-8888-4888-8888-888888888888", queue_revision=4)
        same_run["payload"] = dict(first["payload"], run_ids=[RUN_1], previous_queue_revision=3, queue_revision=4)
        duplicate_existing = self.store.apply_command(same_run)
        self.assertEqual("rejected", duplicate_existing["state"])
        self.assertEqual("invalid_runs", duplicate_existing["result"]["code"])
        self.assertEqual(duplicate_existing, self.store.apply_command(dict(same_run, state="delivered")))
        duplicate = dict(reorder, command_id="99999999-9999-4999-8999-999999999999")
        duplicate["queue_revision"] = 4
        duplicate["payload"] = dict(reorder["payload"], run_ids=[RUN_1, RUN_1], previous_queue_revision=3, queue_revision=4)
        rejected = self.store.apply_command(duplicate)
        self.assertEqual("rejected", rejected["state"])
        self.assertEqual("invalid_queue", rejected["result"]["code"])
        self.assertEqual(rejected, self.store.apply_command(dict(duplicate, state="delivered")))

    def test_reorder_changes_only_exact_queued_runs_and_replays(self):
        self.store.close()
        self.store = AutoPrinterStore(self.root, PRINTER, PROFILE,
                                      max_asset_bytes=128, max_total_bytes=256, max_runs=5)
        self.paired()
        self.store.store_asset(self.transfer(), BytesIO(DATA))
        self.store.confirm_receipt(ASSET)
        queued_a = "66666666-6666-4666-8666-666666666666"
        queued_b = "77777777-7777-4777-8777-777777777777"
        all_runs = [RUN_1, RUN_2, "55555555-5555-4555-8555-555555555555", queued_a, queued_b]
        enqueue = {"command_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "type": "enqueue_series",
                   "control_epoch": 1, "expires_at": "2099-01-01T00:00:00Z", "queue_revision": 2,
                   "state": "created", "payload": {"asset_id": ASSET, "sha256": hashlib.sha256(DATA).hexdigest(),
                   "size_bytes": len(DATA), "profile_id": PROFILE, "series_id": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
                   "copies_per_run": 1, "completion_profile": {"version": 1, "profile_id": PROFILE, "auto_remove": False},
                   "run_ids": all_runs, "binding_epoch": 1, "previous_queue_revision": 1, "queue_revision": 2}}
        self.assertEqual("executed", self.store.apply_command(enqueue)["state"])
        for run_id, state in zip(all_runs[:3], ("completed", "printing", "uncertain")):
            self.store.execution_save(run_id, state)
        before = {row[0]: row[1] for row in self.store._conn.execute(
            "SELECT run_id,position FROM runs WHERE binding_epoch=1")}

        reorder = {"command_id": "ffffffff-ffff-4fff-8fff-ffffffffffff", "type": "reorder_queue",
                   "control_epoch": 1, "expires_at": "2099-01-01T00:00:00Z", "queue_revision": 3,
                   "state": "created", "payload": {"run_ids": [queued_b, queued_a], "binding_epoch": 1,
                   "previous_queue_revision": 2, "queue_revision": 3}}
        result = self.store.apply_command(reorder)
        self.assertEqual("executed", result["state"])
        self.assertEqual(result, self.store.apply_command(dict(reorder, state="delivered")))
        after = {row[0]: row[1] for row in self.store._conn.execute(
            "SELECT run_id,position FROM runs WHERE binding_epoch=1")}
        self.assertEqual(before[queued_a], after[queued_b])
        self.assertEqual(before[queued_b], after[queued_a])
        self.assertTrue(all(after[run_id] == before[run_id] for run_id in all_runs[:3]))

        invalid_payloads = ([queued_a], [queued_a, queued_a], [queued_a, RUN_1],
                            [queued_a, "88888888-8888-4888-8888-888888888888"])
        for index, run_ids in enumerate(invalid_payloads, 1):
            command = dict(reorder, command_id=f"{index:08x}-0000-4000-8000-{index:012x}", queue_revision=4)
            command["payload"] = dict(reorder["payload"], run_ids=run_ids,
                                       previous_queue_revision=3, queue_revision=4)
            rejected = self.store.apply_command(command)
            self.assertEqual(("rejected", "invalid_queue"), (rejected["state"], rejected["result"]["code"]))

        self.store.execution_save(queued_a, "completed")
        self.store.execution_save(queued_b, "completed")
        malformed = dict(reorder, command_id="aaaaaaaa-0000-4000-8000-000000000001", queue_revision=4)
        malformed["payload"] = dict(reorder["payload"], run_ids="", previous_queue_revision=3, queue_revision=4)
        rejected = self.store.apply_command(malformed)
        self.assertEqual(("rejected", "invalid_queue"), (rejected["state"], rejected["result"]["code"]))

    def test_command_replay_body_conflict_and_physical_commands_are_refused(self):
        self.paired()
        base = {"command_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "type": "pause_queue",
                "control_epoch": 1, "expires_at": "2099-01-01T00:00:00Z", "state": "created", "payload": {}}
        self.assertEqual("executed", self.store.apply_command(base)["state"])
        with self.assertRaises(StoreError):
            self.store.apply_command(dict(base, type="resume_queue"))
        start = dict(base, command_id="eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee", type="start_run")
        self.assertEqual("rejected", self.store.apply_command(start)["state"])
        self.assertEqual("stage1_execution_disabled", self.store.pending_results()[-1]["result"]["code"])
        self.assertFalse(self.store.status()["physical_execution_enabled"])

    def test_unpaired_storage_and_quota_are_refused(self):
        with self.assertRaises(StoreError):
            self.store.store_asset(self.transfer(), BytesIO(DATA))
        self.paired()
        oversized = b"x" * 129
        with self.assertRaises(StoreError):
            self.store.store_asset(self.transfer(oversized), BytesIO(oversized))

    def test_renamed_file_without_database_marker_is_recovered_on_retry(self):
        self.paired()
        replace = store_module.os.replace
        def rename_then_interrupt(source, destination):
            replace(source, destination)
            raise OSError("simulated interruption after durable rename")
        with patch.object(store_module.os, "replace", side_effect=rename_then_interrupt):
            with self.assertRaises(OSError):
                self.store.store_asset(self.transfer(), BytesIO(DATA))
        self.assertEqual([], self.store.pending_receipts())
        expected = {key: self.transfer()[key] for key in ("asset_id", "sha256", "size_bytes")}
        self.assertEqual(expected, self.store.store_asset(self.transfer(), BytesIO(DATA)))
        self.assertEqual([expected], self.store.pending_receipts())

    def test_global_quota_counts_unindexed_crash_orphans(self):
        self.paired()
        orphan = self.root / "bindings" / "8" / "orphan.gcode"
        orphan.parent.mkdir(parents=True)
        orphan.write_bytes(b"x" * 250)
        data = b"y" * 10
        with self.assertRaises(StoreError) as raised:
            self.store.store_asset(self.transfer(data), BytesIO(data))
        self.assertEqual("quota_exceeded", raised.exception.code)

    def test_process_lock_rejects_second_store_owner(self):
        with self.assertRaises(StoreError) as raised:
            AutoPrinterStore(self.root, PRINTER, PROFILE)
        self.assertEqual("store_locked", raised.exception.code)

    def test_startup_removes_only_journaled_incomplete_file(self):
        self.paired()
        directory = self.store._binding_dir(1)
        incomplete = directory / f".incoming-{ASSET}"
        unrelated = directory / ".incoming-untracked"
        incomplete.write_bytes(b"partial")
        unrelated.write_bytes(b"keep")
        with self.store._transaction():
            self.store._conn.execute("INSERT INTO staging(binding_epoch,asset_id,path) VALUES(?,?,?)", (1, ASSET, str(incomplete)))
        self.store.close()
        self.store = AutoPrinterStore(self.root, PRINTER, PROFILE, max_asset_bytes=128, max_total_bytes=256, max_runs=4)
        self.assertFalse(incomplete.exists())
        self.assertTrue(unrelated.exists())

    def test_asset_tampering_blocks_receipt_reuse_and_enqueue(self):
        self.paired()
        self.store.store_asset(self.transfer(), BytesIO(DATA))
        path = self.root / "bindings" / "1" / f"{ASSET}.gcode"
        path.write_bytes(b"tampered")
        with self.assertRaises(StoreError):
            self.store.confirm_receipt(ASSET)
        path.write_bytes(DATA)
        self.store.confirm_receipt(ASSET)
        path.write_bytes(b"tampered")
        with self.assertRaises(StoreError):
            self.store.store_asset(self.transfer(), BytesIO(DATA))
        command = {"command_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "type": "enqueue_series",
                   "control_epoch": 1, "expires_at": "2099-01-01T00:00:00Z", "queue_revision": 2, "state": "created",
                   "payload": {"asset_id": ASSET, "sha256": hashlib.sha256(DATA).hexdigest(), "size_bytes": len(DATA),
                               "profile_id": PROFILE, "series_id": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
                               "copies_per_run": 1, "completion_profile": {"version": 1, "profile_id": PROFILE, "auto_remove": False},
                               "run_ids": [RUN_1], "binding_epoch": 1, "previous_queue_revision": 1, "queue_revision": 2}}
        result = self.store.apply_command(command)
        self.assertEqual("rejected", result["state"])
        self.assertEqual("asset_mismatch", result["result"]["code"])

    def test_root_symlink_is_rejected(self):
        link = self.root.parent / f"{self.root.name}-link"
        try:
            link.symlink_to(self.root, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are unavailable in this Windows environment")
        try:
            with self.assertRaises(StoreError) as raised:
                AutoPrinterStore(link, PRINTER, PROFILE)
            self.assertEqual("unsafe_storage_path", raised.exception.code)
        finally:
            link.unlink()

    def test_lockfile_and_database_symlinks_are_rejected(self):
        self.store.close()
        target = self.root.parent / f"{self.root.name}-target"
        target.write_bytes(b"do not open")
        try:
            for name in ("store.lock", "store.sqlite3"):
                candidate = self.root / name
                candidate.symlink_to(target)
                try:
                    with self.assertRaises(StoreError) as raised:
                        AutoPrinterStore(self.root, PRINTER, PROFILE)
                    self.assertEqual("unsafe_storage_path", raised.exception.code)
                finally:
                    candidate.unlink()
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are unavailable in this Windows environment")
        finally:
            target.unlink()

    def test_execution_start_intent_is_durable_and_never_reissued(self):
        self.paired()
        with self.store._transaction():
            self.store._conn.execute(
                "INSERT INTO runs(binding_epoch,run_id,payload,position) VALUES(1,?,?,1)",
                (RUN_1, '{"asset_id":"' + ASSET + '","sha256":"' + hashlib.sha256(DATA).hexdigest() + '","size_bytes":' + str(len(DATA)) + '}'),
            )
        self.store.execution_save(RUN_1, "queued")
        self.store.execution_set_pause(local=False)
        self.store.execution_set_boot("11111111-1111-4111-8111-111111111111")
        proof = {"check_id": "22222222-2222-4222-8222-222222222222",
                 "after_run_id": None, "protocol_version": 1, "printer_id": PRINTER,
                 "bed_epoch": 0, "agent_boot_id": "11111111-1111-4111-8111-111111111111",
                 "verdict": "empty", "model_version": "fake-v1",
                 "captured_at": "2026-10-18T00:00:00Z"}
        self.store.execution_start_intent(RUN_1, "treed-autoprinter/1-" + RUN_1 + "-" + hashlib.sha256(DATA).hexdigest() + ".gcode",
                                          0, proof, "11111111-1111-4111-8111-111111111111", 1,
                                          "2026-10-18T00:00:00Z")
        with self.assertRaises(StoreError) as raised:
            self.store.execution_start_intent(RUN_1, "treed-autoprinter/1-" + RUN_1 + "-" + hashlib.sha256(DATA).hexdigest() + ".gcode",
                                              0, proof, "11111111-1111-4111-8111-111111111111", 1,
                                              "2026-10-18T00:00:00Z")
        self.assertEqual("start_intent_unavailable", raised.exception.code)
        self.assertTrue(self.store.execution_state(RUN_1)["start_intent"])


if __name__ == "__main__":
    unittest.main()
