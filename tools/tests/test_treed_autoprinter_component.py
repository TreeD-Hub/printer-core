"""Адресные офлайн-проверки AutoPrinter без Moonraker и сети.
Контур: read-only mock компонентов и локальные проверки валидации.
"""

from __future__ import annotations

import asyncio
import unittest
import hashlib
import io
import os
import json
import sqlite3
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from moonraker.components import treed_autoprinter as component
from moonraker.components.treed_autoprinter_store import AutoPrinterStore
from moonraker.components.treed_autoprinter_transport import AutoPrinterTransport, TransportError
from moonraker.components import treed_autoprinter_transport as transport_module


# Блок 1: Минимальная модель Moonraker registration/config API.
class FakeServer:
    def __init__(self):
        self.endpoints = {}

    def register_endpoint(self, path, methods, callback, **options):
        self.endpoints[path] = (methods, callback, options)

    def register_event_handler(self, name, callback):
        self.shutdown_handler = callback

    def error(self, message, status=400):
        return RuntimeError((status, message))


class FakeConfig:
    def __init__(self, enabled=False, **values):
        self.server = FakeServer()
        self.values = {"enabled": enabled, **values}

    def get_server(self):
        return self.server

    def getboolean(self, key, default=False):
        return self.values.get(key, default)

    def get(self, key, default=None):
        return self.values.get(key, default)


# Блок 2: Проверки границ конфигурации, identity, sync и команд.
class AutoPrinterComponentTest(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_component_registers_only_authorized_local_endpoints(self):
        with patch.object(component, "AutoPrinterStore") as store, \
                patch.object(component, "AutoPrinterTransport") as transport, \
                patch.object(component, "MoonrakerDevice") as device:
            config = FakeConfig()
            instance = component.load_component(config)
        store.assert_not_called()
        transport.assert_not_called()
        device.assert_not_called()
        self.assertFalse(instance.enabled)
        self.assertEqual(set(config.server.endpoints), {
            component.STATUS_ENDPOINT, component.PAIRING_ENDPOINT, component.CONTROL_ENDPOINT})
        self.assertEqual(config.server.endpoints[component.STATUS_ENDPOINT][0], ["GET"])
        self.assertEqual(config.server.endpoints[component.PAIRING_ENDPOINT][0], ["POST"])
        self.assertTrue(all(item[2].get("wrap_result") is False for item in config.server.endpoints.values()))
        status = await instance._handle_status(None)
        self.assertFalse(status["physical_execution_enabled"])
        self.assertNotIn("secret", status)
        await instance.component_init()
        await instance.close()
        store.assert_not_called()
        transport.assert_not_called()
        device.assert_not_called()

    async def test_control_endpoint_rejects_unlisted_actions_and_disabled_physical_actions(self):
        instance = component.TreeDAutoPrinter(FakeConfig())
        instance._store = object()
        instance._executor = type("Executor", (), {"enabled": False})()

        class Request:
            def __init__(self, args):
                self.args = args

            def get_args(self):
                return self.args

        with self.assertRaisesRegex(RuntimeError, "Invalid AutoPrinter control action"):
            await instance._handle_control(Request({"type": "start_print"}))
        with self.assertRaisesRegex(RuntimeError, "Physical execution is disabled"):
            await instance._handle_control(Request({
                "type": "pause_current_print", "run_id": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
                "action_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"}))

    async def test_execution_disabled_does_not_construct_live_device(self):
        printer_id = "11111111-1111-4111-8111-111111111111"
        profile_id = "treed_v2_corexy_v1"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            credentials = root / "credentials.json"
            credentials.write_text(json.dumps(
                {"printer_id": printer_id, "secret": "s" * 32}), encoding="utf-8")
            if os.name != "nt":
                credentials.chmod(0o600)
            config = FakeConfig(enabled=True, execution_enabled=False, printer_id=printer_id,
                                profile_id=profile_id, credentials_path=str(credentials),
                                api_origin="https://example.test", storage_path=str(root / "store"))
            instance = component.TreeDAutoPrinter(config)
            instance._sync_loop = AsyncMock()
            with patch.object(component, "AutoPrinterTransport", return_value=object()), \
                    patch.object(component, "MoonrakerDevice") as device:
                try:
                    await instance.component_init()
                    self.assertIsNone(instance._device)
                    self.assertFalse(instance._executor.enabled)
                    device.assert_not_called()
                finally:
                    await instance.close()

    async def test_execution_poll_runs_during_sync_backoff_and_close_waits_for_it(self):
        printer_id = "11111111-1111-4111-8111-111111111111"
        profile_id = "treed_v2_corexy_v1"
        backoff_started = asyncio.Event()
        release_backoff = asyncio.Event()
        tick_during_backoff = asyncio.Event()
        tick_cancelled = asyncio.Event()
        release_tick_cancel = asyncio.Event()
        original_sleep = asyncio.sleep

        class FakeTransport:
            def sync(self, payload):
                raise TransportError("offline")

        class FakeExecutor:
            enabled = False
            bed_checker = None

            async def sync_fields(self):
                return {"agent_boot_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                        "snapshot_seq": 1, "snapshot": {}, "events": [],
                        "acknowledged_event_ids": [], "command_results": []}

            async def tick(self):
                if backoff_started.is_set():
                    tick_during_backoff.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    tick_cancelled.set()
                    await release_tick_cancel.wait()
                    raise

        async def controlled_sleep(delay):
            if delay >= 1.5:
                backoff_started.set()
                await release_backoff.wait()
            else:
                await original_sleep(delay)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            credentials = root / "credentials.json"
            credentials.write_text(json.dumps(
                {"printer_id": printer_id, "secret": "s" * 32}), encoding="utf-8")
            if os.name != "nt":
                credentials.chmod(0o600)
            config = FakeConfig(enabled=True, execution_enabled=False, printer_id=printer_id,
                                profile_id=profile_id, credentials_path=str(credentials),
                                api_origin="https://example.test", storage_path=str(root / "store"))
            instance = component.TreeDAutoPrinter(config)
            closing = None
            try:
                with patch.object(component, "AutoPrinterTransport", return_value=FakeTransport()), \
                        patch.object(component, "AutoPrinterExecutor", return_value=FakeExecutor()), \
                        patch.object(component.asyncio, "sleep", controlled_sleep):
                    await instance.component_init()
                    await asyncio.wait_for(backoff_started.wait(), 1)
                    await asyncio.wait_for(tick_during_backoff.wait(), 1)
                    closing = asyncio.create_task(instance.close())
                    await asyncio.wait_for(tick_cancelled.wait(), 1)
                    self.assertFalse(closing.done())
                    release_tick_cancel.set()
                    await closing
                    closing = None
            finally:
                release_backoff.set()
                release_tick_cancel.set()
                if closing is not None:
                    await closing
                elif not instance._closed:
                    await instance.close()

    async def test_pairing_returns_only_the_temporary_code(self):
        config = FakeConfig()
        instance = component.TreeDAutoPrinter(config)

        class FakeTransport:
            def pairing(self):
                return {"code": "123456789012", "expires_at": "2030-01-01T00:00:00Z", "secret": "hidden"}

        instance._store = object()
        instance._transport = FakeTransport()
        result = await instance._handle_pairing(None)
        self.assertEqual(result, {"code": "123456789012", "expires_at": "2030-01-01T00:00:00Z"})

    def test_transport_rejects_non_https_and_non_origin_urls(self):
        for origin in ("http://example.test", "https://user@example.test", "https://example.test/path",
                       "https://example.test?token=x", "https://example.test#fragment"):
            with self.subTest(origin=origin), self.assertRaises(TransportError):
                AutoPrinterTransport(origin, "x" * 32)

    def test_transport_accepts_only_literal_asset_uuid_paths(self):
        transport = AutoPrinterTransport("https://example.test", "x" * 32)
        for path in ("api/autoprinter/device/files/../asset", "api/autoprinter/device/files/%2e%2e",
                     "api/autoprinter/device/files/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa?next=https://evil.test",
                     "api/autoprinter/device/files/not-a-uuid"):
            with self.subTest(path=path), self.assertRaises(TransportError):
                transport.download(path)

    def test_bounded_response_uses_single_read_and_checks_deadline_afterward(self):
        class IncrementingClock:
            now = 10.0

            def __call__(self):
                return self.now

        class TricklingResponse:
            def __init__(self, clock):
                self.clock = clock
                self.closed = False
                self.read1_calls = 0

            def read1(self, size):
                self.read1_calls += 1
                self.clock.now = 12.0
                return b"x"

            def close(self):
                self.closed = True

        clock = IncrementingClock()
        response = TricklingResponse(clock)
        bounded = transport_module._BoundedResponse(response, 10, 11.0)
        with patch.object(transport_module.time, "monotonic", clock):
            with self.assertRaisesRegex(TransportError, "request_timeout"):
                bounded.read(65536)
        self.assertEqual(response.read1_calls, 1)
        self.assertTrue(response.closed)

    async def test_enabled_lifecycle_cancellation_closes_download_stream(self):
        printer_id = "11111111-1111-4111-8111-111111111111"
        profile_id = "treed_v2_corexy_v1"
        asset_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        body = b"G1 X1\n"
        started, release = threading.Event(), threading.Event()

        class TrackedStream(io.BytesIO):
            closed_by_component = False

            def close(self):
                self.closed_by_component = True
                super().close()

        stream = TrackedStream(body)

        class FakeTransport:
            def sync(self, payload):
                return {"protocol_version": 1, "printer_id": printer_id, "control_epoch": 1,
                        "binding_epoch": 1, "binding_state": "paired", "state_version": 1,
                        "queue_revision": 1, "applied_queue_revision": 1,
                        "execution_authority": "printer", "storage_authority": "printer",
                        "configuration_version": 1, "transfers": [{
                            "asset_id": asset_id, "sha256": hashlib.sha256(body).hexdigest(),
                            "size_bytes": len(body), "profile_id": profile_id,
                            "download_path": f"api/autoprinter/device/files/{asset_id}",
                            "expires_at": "2030-01-01T00:00:00Z"}],
                        "commands": [], "snapshot": {"printer_id": printer_id,
                        "profile_id": profile_id}, "ack_event_ids": [], "ignored_event_ids": []}

            def download(self, path):
                started.set()
                release.wait(2)
                return stream

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            credentials = root / "credentials.json"
            credentials.write_text(json.dumps(
                {"printer_id": printer_id, "secret": "s" * 32}), encoding="utf-8")
            if os.name != "nt":
                credentials.chmod(0o600)
            config = FakeConfig(enabled=True, printer_id=printer_id, profile_id=profile_id,
                                credentials_path=str(credentials), api_origin="https://example.test",
                                storage_path=str(root / "store"))
            with patch.object(component, "AutoPrinterTransport", return_value=FakeTransport()):
                instance = component.TreeDAutoPrinter(config)
                await instance.component_init()
                self.assertTrue(await asyncio.to_thread(started.wait, 2))
                closing = asyncio.create_task(instance.close())
                await asyncio.sleep(0)
                release.set()
                await closing
        self.assertTrue(stream.closed_by_component)

    async def test_sqlite_error_keeps_sync_loop_alive_for_next_success(self):
        printer_id = "11111111-1111-4111-8111-111111111111"
        profile_id = "treed_v2_corexy_v1"
        succeeded = threading.Event()

        class FakeTransport:
            calls = 0

            def sync(self, payload):
                self.calls += 1
                if self.calls == 1:
                    raise sqlite3.OperationalError("temporary database failure")
                succeeded.set()
                return {"protocol_version": 1, "printer_id": printer_id, "control_epoch": 1,
                        "binding_epoch": 1, "binding_state": "unpaired", "state_version": 1,
                        "queue_revision": 1, "applied_queue_revision": 1,
                        "execution_authority": "printer", "storage_authority": "printer",
                        "configuration_version": 1, "transfers": [], "commands": [],
                        "snapshot": {"printer_id": printer_id, "profile_id": profile_id},
                        "ack_event_ids": [], "ignored_event_ids": []}

        with tempfile.TemporaryDirectory() as directory:
            instance = component.TreeDAutoPrinter(FakeConfig())
            instance._store = AutoPrinterStore(Path(directory), printer_id, profile_id)
            instance._transport = FakeTransport()
            real_sleep = asyncio.sleep

            async def quick_sleep(delay):
                await real_sleep(0)

            async def wait_until_online():
                while instance._connection != "online":
                    await real_sleep(0)

            with patch.object(component.asyncio, "sleep", quick_sleep):
                instance._task = asyncio.create_task(instance._sync_loop())
                try:
                    self.assertTrue(await asyncio.to_thread(succeeded.wait, 2))
                    await asyncio.wait_for(wait_until_online(), 2)
                    self.assertEqual(instance._connection, "online")
                finally:
                    await instance.close()

    async def test_sqlite_error_during_unauthorized_revocation_retries(self):
        printer_id = "11111111-1111-4111-8111-111111111111"
        profile_id = "treed_v2_corexy_v1"
        calls = 0

        class UnauthorizedTransport:
            def sync(self, payload):
                raise TransportError("http_error", 401)

        with tempfile.TemporaryDirectory() as directory:
            instance = component.TreeDAutoPrinter(FakeConfig())
            instance._store = AutoPrinterStore(Path(directory), printer_id, profile_id)
            instance._transport = UnauthorizedTransport()

            def mark_revoked():
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise sqlite3.OperationalError("temporary database failure")
                instance._store.update_binding(1, 1, "revoked", 1)

            instance._mark_revoked = mark_revoked
            real_sleep = asyncio.sleep

            async def quick_sleep(delay):
                await real_sleep(0)

            async def wait_until_revoked():
                while instance._connection != "revoked":
                    await real_sleep(0)

            with patch.object(component.asyncio, "sleep", quick_sleep):
                instance._task = asyncio.create_task(instance._sync_loop())
                try:
                    await asyncio.wait_for(wait_until_revoked(), 2)
                    self.assertGreaterEqual(calls, 2)
                    self.assertEqual(instance._store.session()["binding_state"], "revoked")
                finally:
                    await instance.close()

    def test_sync_response_requires_identity_authority_and_bounded_transfer(self):
        response = {"protocol_version": 1, "printer_id": "11111111-1111-4111-8111-111111111111",
                    "control_epoch": 2, "binding_epoch": 3, "binding_state": "paired", "state_version": 1,
                    "configuration_version": 1, "queue_revision": 1,
                    "applied_queue_revision": 1, "execution_authority": "printer", "storage_authority": "printer",
                    "transfers": [], "commands": [],
                    "snapshot": {"printer_id": "11111111-1111-4111-8111-111111111111",
                                 "profile_id": "treed_v2_corexy_v1"},
                    "ack_event_ids": [], "ignored_event_ids": []}
        component._validate_response(response, response["printer_id"], "treed_v2_corexy_v1")
        for change in ({"printer_id": "22222222-2222-4222-8222-222222222222"},
                       {"execution_authority": "backend"}, {"binding_state": "offline"},
                       {"transfers": [{}]}, {"commands": [None] * 21}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                component._validate_response(response | change, response["printer_id"], "treed_v2_corexy_v1")

    def test_commands_require_current_control_binding_and_expiry(self):
        response = {"control_epoch": 4, "binding_epoch": 2}
        command = {"command_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "type": "pause_queue",
                   "control_epoch": 4, "expires_at": "2030-01-01T00:00:00Z"}
        normalized = component._validate_command(command, response)
        self.assertEqual(normalized, command)
        for changed in ({"control_epoch": 3}, {"expires_at": None}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                component._validate_command(command | changed, response)
        self.assertEqual(component._validate_command(command | {"type": "resume_queue"}, response)["type"],
                         "resume_queue")
        queue_payload = {"series_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
                         "asset_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                         "sha256": "a" * 64, "size_bytes": 32768,
                         "profile_id": "treed_v2_corexy_v1", "copies_per_run": 1,
                         "completion_profile": {"version": 1, "profile_id": "treed_v2_corexy_v1",
                                               "auto_remove": False},
                         "run_ids": ["dddddddd-dddd-4ddd-8ddd-dddddddddddd"],
                         "previous_queue_revision": 5, "queue_revision": 6, "binding_epoch": 2}
        queue_command = command | {"type": "enqueue_series", "queue_revision": 6, "payload": queue_payload}
        self.assertEqual(component._validate_command(queue_command, response), queue_command)
        bad_queue_command = queue_command | {"payload": queue_payload | {"binding_epoch": 1}}
        with self.assertRaises(ValueError):
            component._validate_command(bad_queue_command, response)

    async def test_receipt_retry_queue_replay_and_revocation_ack_gates(self):
        printer_id = "11111111-1111-4111-8111-111111111111"
        profile_id = "treed_v2_corexy_v1"
        asset_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        command_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        run_id = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
        body = b"G1 X10\n"
        transfer = {"asset_id": asset_id, "sha256": hashlib.sha256(body).hexdigest(),
                    "size_bytes": len(body), "profile_id": profile_id,
                    "download_path": f"api/autoprinter/device/files/{asset_id}",
                    "expires_at": "2030-01-01T00:00:00Z"}
        command = {"command_id": command_id, "type": "enqueue_series", "run_id": None,
                   "control_epoch": 1, "queue_revision": 2,
                   "payload": {"series_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
                               "asset_id": asset_id, "sha256": transfer["sha256"],
                               "size_bytes": len(body), "profile_id": profile_id,
                               "copies_per_run": 1,
                               "completion_profile": {"version": 1, "profile_id": profile_id,
                                                     "auto_remove": False},
                               "run_ids": [run_id], "previous_queue_revision": 1,
                               "queue_revision": 2, "binding_epoch": 1},
                   "expires_at": "2030-01-01T00:00:00Z", "state": "created"}

        def response(**changes):
            result = {"protocol_version": 1, "printer_id": printer_id, "control_epoch": 1,
                      "binding_epoch": 1, "binding_state": "paired", "state_version": 1,
                      "queue_revision": 2, "applied_queue_revision": 1,
                      "execution_authority": "printer", "storage_authority": "printer",
                      "configuration_version": 1, "transfers": [], "commands": [],
                      "snapshot": {"printer_id": printer_id, "profile_id": profile_id},
                      "ack_event_ids": [], "ignored_event_ids": []}
            return result | changes

        class FakeTransport:
            def __init__(self):
                self.sync_replies = [response(transfers=[transfer]), response(commands=[command]),
                                     response(commands=[command])]
                self.receipts = []
                self.downloads = 0

            def sync(self, payload):
                self.last_payload = payload
                return self.sync_replies.pop(0)

            def download(self, path):
                self.downloads += 1
                return io.BytesIO(body)

            def receipt(self, submitted_asset_id, value):
                self.receipts.append((submitted_asset_id, value))
                if len(self.receipts) == 1:
                    raise TransportError("network_error")
                result = {"storage_state": "stored", "asset_id": submitted_asset_id,
                          "printer_id": printer_id, "binding_epoch": 1,
                          "sha256": value["sha256"], "size_bytes": value["size_bytes"],
                          "profile_id": profile_id}
                if len(self.receipts) == 2:
                    result["sha256"] = "0" * 64
                return result

        with tempfile.TemporaryDirectory() as directory:
            instance = None
            reopened = None
            try:
                config = FakeConfig()
                instance = component.TreeDAutoPrinter(config)
                instance._store = AutoPrinterStore(Path(directory), printer_id, profile_id)
                instance._store.update_binding(1, 1, "paired", 1)
                transport = FakeTransport()
                instance._transport = transport

                with self.assertRaises(TransportError):
                    await instance._sync_once()
                self.assertEqual(instance._store.pending_receipts(), [{"asset_id": asset_id,
                                                                        "sha256": transfer["sha256"],
                                                                        "size_bytes": len(body)}])
                with self.assertRaisesRegex(ValueError, "invalid_receipt"):
                    await instance._sync_once()
                self.assertEqual(instance._store.pending_receipts(), [{"asset_id": asset_id,
                                                                        "sha256": transfer["sha256"],
                                                                        "size_bytes": len(body)}])
                await instance._sync_once()
                self.assertEqual(instance._store.pending_receipts(), [])
                self.assertEqual(instance._store.queue()[0]["run_id"], run_id)
                self.assertEqual(transport.downloads, 1)
                self.assertEqual(transport.receipts[0][0], asset_id)
                self.assertEqual(transport.receipts[1][0], asset_id)
                self.assertEqual(set(config.server.endpoints), {
                    component.STATUS_ENDPOINT, component.PAIRING_ENDPOINT, component.CONTROL_ENDPOINT})
                self.assertTrue(transport.last_payload["snapshot"]["local_pause"])
                self.assertFalse(transport.last_payload["snapshot"]["ready"])
                self.assertIsNone(transport.last_payload["snapshot"]["current_run_id"])

                await instance.close()
                instance = None
                reopened = AutoPrinterStore(Path(directory), printer_id, profile_id)
                pending = reopened.pending_results()
                self.assertEqual([item["command_id"] for item in pending], [command_id])

                class ReplyTransport(FakeTransport):
                    def __init__(self, reply):
                        self.sync_replies = [reply]
                        self.receipts = []

                    def receipt(self, submitted_asset_id, value):
                        return {}

                instance = component.TreeDAutoPrinter(FakeConfig())
                instance._store = reopened
                reopened = None
                store = instance._store
                instance._transport = ReplyTransport(response(control_epoch=2, reason="control_epoch_mismatch"))
                await instance._sync_once()
                self.assertEqual([item["command_id"] for item in store.pending_results()], [command_id])

                instance._transport = ReplyTransport(response(control_epoch=3, binding_epoch=2,
                                                              binding_state="revoked",
                                                              queue_revision=2,
                                                              applied_queue_revision=2))
                await instance._sync_once()
                self.assertEqual(store.session()["control_epoch"], 3)
                self.assertEqual(store.session()["binding_epoch"], 2)
                self.assertEqual(store.pending_results(), [])
                self.assertEqual(store.queue(), [])
                await instance.close()
                instance = None
                reopened = AutoPrinterStore(Path(directory), printer_id, profile_id)
                old_result = reopened._conn.execute(
                    "SELECT reported FROM commands WHERE command_id=?", (command_id,)
                ).fetchone()
                self.assertEqual(old_result, (0,))
                self.assertEqual(reopened.pending_results(), [])
                reopened.close()
                reopened = None
            finally:
                if instance is not None:
                    await instance.close()
                if reopened is not None:
                    reopened.close()

    async def test_sync_uses_executor_wire_fields_and_acknowledges_only_submitted_events(self):
        printer_id = "11111111-1111-4111-8111-111111111111"
        profile_id = "treed_v2_corexy_v1"
        event_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        event = {"event_id": event_id, "event_seq": 1, "control_epoch": 1,
                 "type": "run_state", "occurred_at": "2030-01-01T00:00:00Z",
                 "run_id": None, "payload": {"state": "queued"}}
        snapshot = {"observed_at": "2030-01-01T00:00:00Z", "bed_epoch": 0,
                    "queue_control": "paused", "current_run_id": None, "run_state": None,
                    "local_pause": True, "ready": False, "print_paused": False,
                    "capabilities": {"bed_check": False}, "model_version": None}

        class FakeExecutor:
            enabled = False

            def __init__(self):
                self.ack = None

            async def sync_fields(self):
                return {"agent_boot_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                        "snapshot_seq": 1, "snapshot": snapshot, "events": [event],
                        "acknowledged_event_ids": [], "command_results": []}

            async def acknowledge(self, response, submitted):
                self.ack = (response, submitted)

        class FakeTransport:
            def sync(self, payload):
                self.payload = payload
                return {"protocol_version": 1, "printer_id": printer_id, "control_epoch": 1,
                        "binding_epoch": 1, "binding_state": "paired", "state_version": 1,
                        "queue_revision": 1, "applied_queue_revision": 1,
                        "execution_authority": "printer", "storage_authority": "printer",
                        "configuration_version": 1, "transfers": [], "commands": [],
                        "snapshot": {"printer_id": printer_id, "profile_id": profile_id},
                        "ack_event_ids": [event_id], "ignored_event_ids": []}

        with tempfile.TemporaryDirectory() as directory:
            instance = component.TreeDAutoPrinter(FakeConfig())
            instance._store = AutoPrinterStore(Path(directory), printer_id, profile_id)
            instance._store.update_binding(1, 1, "paired", 1)
            instance._executor = FakeExecutor()
            transport = FakeTransport()
            instance._transport = transport
            try:
                await instance._sync_once()

                self.assertEqual(transport.payload["events"], [event])
                self.assertEqual(transport.payload["agent_boot_id"], "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
                self.assertNotIn("binding_epoch", transport.payload)
                self.assertEqual([item["event_id"] for item in instance._executor.ack[1]["events"]], [event_id])
                self.assertEqual(instance._executor.ack[1]["binding_epoch"], 1)
            finally:
                await instance.close()


if __name__ == "__main__":
    unittest.main()
