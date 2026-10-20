"""Контракт атомарного принятия автономного пакета AutoPrinter."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import hashlib

from moonraker.components.treed_autoprinter_store import AutoPrinterStore
from moonraker.components import treed_autoprinter_store as store_module


PRINTER = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
PROFILE = "treed-v2"
ASSETS = ["11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"]
RUNS = ["33333333-3333-4333-8333-333333333333", "44444444-4444-4444-8444-444444444444",
        "55555555-5555-4555-8555-555555555555"]


class AutoPrinterPackageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = AutoPrinterStore(Path(self.temp.name), PRINTER, PROFILE, max_runs=10)
        self.store.update_binding(1, 1, "paired", 1)
        self.data = [b"G1 X1\n", b"G1 X2\n"]
        for asset_id, body in zip(ASSETS, self.data):
            self.store.store_asset({"asset_id": asset_id, "sha256": hashlib.sha256(body).hexdigest(),
                                   "size_bytes": len(body), "profile_id": PROFILE}, BytesIO(body))
            self.store.confirm_receipt(asset_id)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def command(self, *, missing_second_asset=False):
        groups = []
        for index, (asset_id, body, series_id, run_ids) in enumerate(zip(
                ASSETS, self.data,
                ["66666666-6666-4666-8666-666666666666", "77777777-7777-4777-8777-777777777777"],
                [RUNS[:2], RUNS[2:]])):
            groups.append({"series_id": series_id, "asset_id": asset_id,
                           "sha256": hashlib.sha256(body).hexdigest(), "size_bytes": len(body),
                           "copies_per_run": len(run_ids),
                           "completion_profile": {"version": 1, "profile_id": PROFILE, "auto_remove": False},
                           "run_ids": run_ids})
        if missing_second_asset:
            groups[1]["sha256"] = "0" * 64
        manifest = {"package_id": "88888888-8888-4888-8888-888888888888", "version": 1,
                    "printer_id": PRINTER, "binding_epoch": 1, "profile_id": PROFILE,
                    "policy_version": 1, "runs": groups}
        payload = {"binding_epoch": 1, "previous_queue_revision": 1, "queue_revision": 2,
                   "manifest": manifest, "manifest_hash": store_module._manifest_hash(manifest)}
        return {"command_id": "99999999-9999-4999-8999-999999999999", "type": "enqueue_package",
                "control_epoch": 1, "expires_at": "2099-01-01T00:00:00Z", "queue_revision": 2,
                "payload": payload}

    def test_multi_asset_package_commits_all_runs_and_replay_is_idempotent_after_delivery_expiry(self):
        command = self.command()
        result = self.store.apply_command(command)
        self.assertEqual("executed", result["state"])
        self.assertEqual(3, len(self.store.status()["queue"]))
        self.assertEqual(RUNS, [run["run_id"] for run in self.store.status()["queue"]])
        with patch.object(store_module, "_not_expired", return_value=False):
            replay = self.store.apply_command(command)
        self.assertEqual(result, replay)
        self.assertEqual(1, self.store._conn.execute("SELECT COUNT(*) FROM packages").fetchone()[0])

    def test_manifest_hash_matches_cross_language_a_b_a_canonical_vector(self):
        asset_a = ASSETS[0]
        asset_b = ASSETS[1]
        profile = {"version": 1, "profile_id": PROFILE, "auto_remove": False}
        manifest = {"package_id": "88888888-8888-4888-8888-888888888888", "version": 1,
                    "printer_id": PRINTER, "binding_epoch": 1, "profile_id": PROFILE,
                    "policy_version": 1, "runs": [
                        {"series_id": "66666666-6666-4666-8666-666666666666", "asset_id": asset_a,
                         "sha256": hashlib.sha256(b"G1 X1\n").hexdigest(), "size_bytes": 6,
                         "copies_per_run": 1, "completion_profile": profile,
                         "run_ids": ["33333333-3333-4333-8333-333333333333"]},
                        {"series_id": "77777777-7777-4777-8777-777777777777", "asset_id": asset_b,
                         "sha256": hashlib.sha256(b"G1 X2\n").hexdigest(), "size_bytes": 6,
                         "copies_per_run": 1, "completion_profile": profile,
                         "run_ids": ["44444444-4444-4444-8444-444444444444"]},
                        {"series_id": "99999999-9999-4999-8999-999999999999", "asset_id": asset_a,
                         "sha256": hashlib.sha256(b"G1 X1\n").hexdigest(), "size_bytes": 6,
                         "copies_per_run": 1, "completion_profile": profile,
                         "run_ids": ["55555555-5555-4555-8555-555555555555"]},
                    ]}

        self.assertEqual("5b7cbc068c3fa1cce94f3c991af22c7a9160d2ec8f4742572873fb5748f44c0c",
                         store_module._manifest_hash(manifest))

    def test_missing_second_asset_rejects_whole_package_without_partial_runs(self):
        result = self.store.apply_command(self.command(missing_second_asset=True))
        self.assertEqual("rejected", result["state"])
        self.assertEqual([], self.store.queue())
        self.assertEqual(0, self.store._conn.execute("SELECT COUNT(*) FROM packages").fetchone()[0])

    def test_reorder_cannot_mutate_an_accepted_package(self):
        self.assertEqual("executed", self.store.apply_command(self.command())["state"])
        reorder = {"command_id": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", "type": "reorder_queue",
                   "control_epoch": 1, "expires_at": "2099-01-01T00:00:00Z", "queue_revision": 3,
                   "payload": {"binding_epoch": 1, "previous_queue_revision": 2,
                               "queue_revision": 3, "run_ids": list(reversed(RUNS))}}
        result = self.store.apply_command(reorder)
        self.assertEqual("rejected", result["state"])
        self.assertEqual("package_immutable", result["result"]["code"])
        self.assertEqual(2, result["result"]["queue_revision"])
        self.assertEqual(RUNS, [run["run_id"] for run in self.store.queue()])

    def test_status_selects_current_package_and_cancel_keeps_current_run_and_next_package(self):
        first = self.command()
        self.assertEqual("executed", self.store.apply_command(first)["state"])
        next_package_id = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
        next_run_id = "cccccccc-dddd-4eee-8fff-000000000001"
        next_series_id = "dddddddd-eeee-4fff-8000-000000000001"
        next_group = {"series_id": next_series_id, "asset_id": ASSETS[0],
                      "sha256": hashlib.sha256(self.data[0]).hexdigest(),
                      "size_bytes": len(self.data[0]), "copies_per_run": 1,
                      "completion_profile": {"version": 1, "profile_id": PROFILE, "auto_remove": False},
                      "run_ids": [next_run_id]}
        manifest = {"package_id": next_package_id, "version": 1, "printer_id": PRINTER,
                    "binding_epoch": 1, "profile_id": PROFILE, "policy_version": 1,
                    "runs": [next_group]}
        second = {"command_id": "eeeeeeee-ffff-4000-8111-000000000001", "type": "enqueue_package",
                  "control_epoch": 1, "expires_at": "2099-01-01T00:00:00Z", "queue_revision": 3,
                  "payload": {"binding_epoch": 1, "previous_queue_revision": 2,
                              "queue_revision": 3, "manifest": manifest,
                              "manifest_hash": store_module._manifest_hash(manifest)}}
        self.assertEqual("executed", self.store.apply_command(second)["state"])
        self.store.execution_save(RUNS[0], "printing")
        self.store.execution_set_boot("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")

        status = self.store.status()
        self.assertEqual("88888888-8888-4888-8888-888888888888", status["active_package"]["package_id"])
        self.assertEqual("running", status["active_package"]["state"])
        result = self.store.execution_cancel_package(
            "88888888-8888-4888-8888-888888888888",
            "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff", "2026-10-20T00:00:03Z")
        self.assertEqual(RUNS[1:], result["result"]["cancelled_run_ids"])
        states = self.store.execution_states()
        self.assertEqual("printing", states[RUNS[0]]["state"])
        next_run = next(run for run in self.store.status()["queue"]
                        if run["run_id"] == next_run_id)
        self.assertEqual("queued", next_run["state"])
        self.assertEqual("88888888-8888-4888-8888-888888888888",
                         self.store.status()["active_package"]["package_id"])
        self.assertEqual(1, self.store.status()["active_package"]["remaining_count"])
        self.store.execution_save(RUNS[0], "completed")
        self.assertEqual(next_package_id, self.store.status()["active_package"]["package_id"])

    def test_detection_hold_is_durable_and_idempotent_until_explicit_resume(self):
        context = {"run_id": RUNS[0], "generation": 4}
        first = self.store.execution_hold_for_detection("occupied", context, "2026-10-20T00:00:00Z")
        second = self.store.execution_hold_for_detection("occupied", context, "2026-10-20T00:00:01Z")
        self.assertEqual(first["hold_id"], second["hold_id"])
        self.assertTrue(self.store.session()["local_pause"])
        self.assertEqual([first["hold_id"]], [hold["hold_id"] for hold in self.store.execution_active_holds()])
        self.store.execution_release_holds("2026-10-20T00:00:02Z")
        self.assertFalse(self.store.session()["local_pause"])
        self.assertEqual([], self.store.execution_active_holds())

    def test_cancel_package_atomically_cancels_only_queued_runs_and_replays_receipt(self):
        self.store.apply_command(self.command())
        self.store.execution_set_boot("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
        self.store.execution_save(RUNS[0], "printing")
        action_id = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff"
        package_id = "88888888-8888-4888-8888-888888888888"
        first = self.store.execution_cancel_package(
            package_id, action_id, "2026-10-20T00:00:03Z")
        replay = self.store.execution_cancel_package(
            package_id, action_id, "2026-10-20T00:00:04Z")

        self.assertEqual(first, replay)
        self.assertEqual(RUNS[1:], first["result"]["cancelled_run_ids"])
        cancel_events = [event for event in self.store.execution_events_pending(
            "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee") if event["run_id"] in RUNS[1:]]
        self.assertEqual(RUNS[1:], [event["run_id"] for event in cancel_events])
        for event in cancel_events:
            self.assertEqual({"state": "cancelled", "error_code": "package_cancelled",
                              "package_id": package_id, "package_version": 1}, event["payload"])
        states = self.store.execution_states()
        self.assertEqual("printing", states[RUNS[0]]["state"])
        self.assertEqual(["cancelled", "cancelled"], [states[run]["state"] for run in RUNS[1:]])
        self.assertEqual(2, self.store._conn.execute(
            "SELECT COUNT(*) FROM execution_events WHERE acknowledged=0").fetchone()[0])

    def test_cancel_package_preflights_outbox_before_any_run_mutation(self):
        self.store.apply_command(self.command())
        self.store.execution_set_boot("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
        self.store._conn.execute(
            "INSERT INTO execution_events(event_id,boot_id,event_seq,control_epoch,event_type,occurred_at,payload) "
            "SELECT lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || "
            "substr(lower(hex(randomblob(2))),2) || '-8' || substr(lower(hex(randomblob(2))),2) || '-' || "
            "lower(hex(randomblob(6))),?,n,1,'run_state','2026-10-20T00:00:00Z','{}' "
            "FROM (WITH RECURSIVE seq(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM seq WHERE n<9999) SELECT n FROM seq)",
            ("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",),
        )
        with self.assertRaisesRegex(Exception, "outbox_full"):
            self.store.execution_cancel_package(
                "88888888-8888-4888-8888-888888888888",
                "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff", "2026-10-20T00:00:03Z")
        self.assertEqual({}, self.store.execution_states())


if __name__ == "__main__":
    unittest.main()
