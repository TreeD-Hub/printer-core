"""Адресные офлайн-проверки AutoPrinter без Moonraker и сети.
Контур: read-only mock компонентов и локальные проверки валидации.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
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
    def test_avatar_negotiation_and_unlink_profile_validation(self):
        transport = AutoPrinterTransport("https://treed.example", "test-secret")
        with patch.object(transport, "_opener") as opener:
            opener.open.return_value = io.BytesIO(b"{}")
            transport.sync({})
            request = opener.open.call_args.args[0]
            self.assertEqual("avatar-v1", request.get_header("X-treed-owner-profile"))
            self.assertEqual("persistent-v1", request.get_header("X-treed-pairing"))
        response = {"binding_state": "paired", "binding_epoch": 1, "control_epoch": 1,
                    "queue_revision": 1, "unlink_pending": True,
                    "owner_profile": {"display_name": "Owner", "avatar_url": "https://treed.example/a.jpg"}}
        component._validate_unlink_response(response, 1, confirmed=False)
        response["owner_profile"]["avatar_url"] = "javascript:alert(1)"
        with self.assertRaises(ValueError):
            component._validate_unlink_response(response, 1, confirmed=False)

    async def test_disabled_component_registers_only_authorized_local_endpoints(self):
        with patch.object(component, "AutoPrinterStore") as store, \
                patch.object(component, "AutoPrinterTransport") as transport, \
                patch.object(component, "MoonrakerDevice") as device, \
                patch.object(component, "E0BedChecker") as bed_checker:
            config = FakeConfig()
            instance = component.load_component(config)
        store.assert_not_called()
        transport.assert_not_called()
        device.assert_not_called()
        bed_checker.assert_not_called()
        self.assertFalse(instance.enabled)
        self.assertEqual(set(config.server.endpoints), {
            component.STATUS_ENDPOINT, component.PAIRING_ENDPOINT,
            component.PAIRING_CANCEL_ENDPOINT, component.ACCOUNT_UNLINK_ENDPOINT, component.CONTROL_ENDPOINT})
        self.assertEqual(config.server.endpoints[component.STATUS_ENDPOINT][0], ["GET"])
        self.assertEqual(config.server.endpoints[component.PAIRING_ENDPOINT][0], ["POST"])
        self.assertTrue(all(item[2].get("wrap_result") is False for item in config.server.endpoints.values()))
        status = await instance._handle_status(None)
        self.assertEqual(status["protocol_version"], 1)
        self.assertEqual(status["configuration_version"], 1)
        self.assertIsNone(status["last_sync_at"])
        self.assertFalse(status["physical_execution_enabled"])
        self.assertTrue(status["capabilities"]["account_linking"])
        self.assertNotIn("secret", status)
        await instance.component_init()
        await instance.close()
        store.assert_not_called()
        transport.assert_not_called()
        device.assert_not_called()

    async def test_defaults_enable_features_but_missing_credentials_do_not_start_workers(self):
        config = FakeConfig()
        config.values.pop("enabled")
        with patch.object(component, "AutoPrinterStore") as store, \
                patch.object(component, "AutoPrinterTransport") as transport, \
                patch.object(component, "MoonrakerDevice") as device, \
                patch.object(component, "E0BedChecker") as bed_checker:
            instance = component.load_component(config)
            try:
                await instance.component_init()
                status = await instance._handle_status(None)
                self.assertTrue(instance.enabled)
                self.assertFalse(instance.execution_enabled)
                self.assertTrue(instance.bed_check_enabled)
                self.assertTrue(status["enabled"])
                self.assertFalse(status["configured"])
                self.assertFalse(status["execution_enabled"])
                self.assertEqual(status["blocked_reason"], "configuration_invalid")
                self.assertIsNone(instance._task)
                self.assertIsNone(instance._execution_task)
                store.assert_not_called()
                transport.assert_not_called()
                device.assert_not_called()
                bed_checker.assert_not_called()
            finally:
                await instance.close()

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

    async def test_journal_configuration_error_is_safe_and_does_not_start_workers(self):
        instance = component.TreeDAutoPrinter(FakeConfig(enabled=True))
        with patch.object(instance, "_create_configured", side_effect=component.StoreError("journal_limit_exceeded")), \
                patch.object(component, "MoonrakerDevice") as device:
            await instance.component_init()
        status = await instance._handle_status(None)
        self.assertFalse(status["configured"])
        self.assertEqual(status["blocked_reason"], "journal_limit_exceeded")
        self.assertIsNone(instance._task)
        self.assertIsNone(instance._execution_task)
        device.assert_not_called()
        await instance.close()

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

    async def test_bed_check_can_be_enabled_without_execution_and_recheck_while_paired(self):
        printer_id = "11111111-1111-4111-8111-111111111111"
        profile_id = "treed_v2_corexy_v1"

        class CandidateChecker:
            live_qualified = False
            model_version = "fdm-detail-e0-20261016-8de7e8aa22e2"
            allowed_model_versions = (model_version,)

            async def check(self, after_run_id, bed_epoch, boot_id):
                return {"protocol_version": 1, "printer_id": printer_id,
                        "after_run_id": after_run_id, "check_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                        "bed_epoch": bed_epoch, "agent_boot_id": boot_id, "verdict": "unknown",
                        "model_version": self.model_version,
                        "captured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                        "confidence": None, "reason": "model_not_qualified"}

            def status(self):
                return {"enabled": True, "live_qualified": False, "verdict": None,
                        "model_version": self.model_version, "reason": None}

        class Request:
            def get_args(self):
                return {"type": "recheck_bed"}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            credentials = root / "credentials.json"
            credentials.write_text(json.dumps(
                {"printer_id": printer_id, "secret": "s" * 32}), encoding="utf-8")
            if os.name != "nt":
                credentials.chmod(0o600)
            checker = CandidateChecker()
            config = FakeConfig(enabled=True, execution_enabled=False, bed_check_enabled=True,
                                printer_id=printer_id, profile_id=profile_id,
                                credentials_path=str(credentials), api_origin="https://example.test",
                                storage_path=str(root / "store"), bed_check_model_path=str(root / "model.pth"),
                                bed_check_python=str(root / "python"))
            instance = component.TreeDAutoPrinter(config)
            instance._sync_loop = AsyncMock()
            instance._execution_loop = AsyncMock()
            try:
                with patch.object(component, "AutoPrinterTransport", return_value=object()), \
                        patch.object(component, "E0BedChecker", return_value=checker) as make_checker, \
                        patch.object(component, "MoonrakerDevice") as device:
                    await instance.component_init()
                    make_checker.assert_called_once_with(
                        printer_id, str(root / "model.pth"), str(root / "python"),
                        "http://127.0.0.1:8080/?action=snapshot", socket_path=None)
                    device.assert_not_called()
                    self.assertFalse(instance._executor.enabled)
                    self.assertIs(checker, instance._executor.bed_checker)
                await instance._executor.initialize()
                await asyncio.to_thread(instance._store.update_binding, 1, 1, "paired", 1)
                submitted = await instance._executor.sync_fields()
                session = await asyncio.to_thread(instance._store.session)
                submitted.update(control_epoch=session["control_epoch"], binding_epoch=session["binding_epoch"])
                await instance._executor.acknowledge({"control_epoch": session["control_epoch"],
                                                       "binding_epoch": session["binding_epoch"],
                                                       "ack_event_ids": [], "ignored_event_ids": []}, submitted)
                status = await instance._handle_status(None)
                self.assertEqual(checker.status(), status["bed_check"])
                self.assertFalse(status["capabilities"]["bed_check"])
                self.assertTrue(status["capabilities"]["bed_check_diagnostic"])
                self.assertTrue(status["capabilities"]["queue"])
                self.assertTrue(status["capabilities"]["pairing"])
                result = await instance._handle_control(Request())
                self.assertEqual({"type": "recheck_bed", "command_id": None, "state": "executed",
                                  "result": {"code": "bed_unknown"}}, result["action_result"])
                self.assertEqual("unknown", (await asyncio.to_thread(
                    instance._store.execution_events_pending, instance._boot_id))[0]["payload"]["verdict"])
                self.assertFalse(result["capabilities"]["bed_check"])
            finally:
                await instance.close()

    async def test_invalid_bed_check_config_reports_safe_status_when_paired(self):
        printer_id = "11111111-1111-4111-8111-111111111111"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            credentials = root / "credentials.json"
            credentials.write_text(json.dumps(
                {"printer_id": printer_id, "secret": "s" * 32}), encoding="utf-8")
            if os.name != "nt":
                credentials.chmod(0o600)
            config = FakeConfig(enabled=True, execution_enabled=False, bed_check_enabled=True,
                                printer_id=printer_id, profile_id="treed_v2_corexy_v1",
                                credentials_path=str(credentials), api_origin="https://example.test",
                                storage_path=str(root / "store"), bed_check_model_path="relative/model.pth",
                                bed_check_python="relative/python")
            instance = component.TreeDAutoPrinter(config)
            instance._sync_loop = AsyncMock()
            instance._execution_loop = AsyncMock()
            try:
                with patch.object(component, "AutoPrinterTransport", return_value=object()), \
                        patch.object(component, "E0BedChecker", side_effect=ValueError("path")):
                    await instance.component_init()
                await asyncio.to_thread(instance._store.update_binding, 1, 1, "paired", 1)
                status = await instance._handle_status(None)
                self.assertEqual("configuration_invalid", status["bed_check"]["last_check"]["reason"])
                self.assertNotIn("path", str(status["bed_check"]))
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

    async def test_pairing_returns_validated_url_without_credentials(self):
        config = FakeConfig()
        instance = component.TreeDAutoPrinter(config)

        class FakeTransport:
            def pairing(self):
                return {"code": "ABCD2345", "expires_at": "2030-01-01T00:00:00Z",
                        "verification_url": "https://app.example/autoprinter/connect#code=ABCD2345",
                        "secret": "hidden"}

        class FakeStore:
            def session(self):
                return {"binding_state": "unpaired", "unlink_pending": False}

            def pairing(self):
                return None

            def save_pairing(self, value):
                self.value = value

        instance._store = FakeStore()
        instance._transport = FakeTransport()
        result = await instance._handle_pairing(None)
        self.assertEqual(result, {"code": "ABCD2345", "expires_at": "2030-01-01T00:00:00Z",
                                  "verification_url": "https://app.example/autoprinter/connect#code=ABCD2345"})

    async def test_persistent_pairing_survives_restart_and_cancellation_requires_confirmation(self):
        pairing = {"code": "ABCD2345", "expires_at": None,
                   "verification_url": "https://app.example/autoprinter/connect#code=ABCD2345"}
        class FakeTransport:
            pairing_calls = 0

            def pairing(self):
                self.pairing_calls += 1
                return pairing

            def cancel_pairing(self):
                return {"cancelled": True}

        class Request:
            def get_args(self):
                return {}

        with tempfile.TemporaryDirectory() as directory:
            instance = component.TreeDAutoPrinter(FakeConfig())
            instance._transport = FakeTransport()
            instance._store = AutoPrinterStore(Path(directory), "11111111-1111-4111-8111-111111111111", "profile")
            try:
                self.assertEqual(pairing, await instance._handle_pairing(None))
                instance._store.close()
                instance._store = AutoPrinterStore(Path(directory), "11111111-1111-4111-8111-111111111111", "profile")
                self.assertEqual(pairing, await instance._handle_pairing(None))
                self.assertEqual(1, instance._transport.pairing_calls)
                self.assertEqual(pairing, (await instance._handle_status(None))["pairing"])
                for failure in (TransportError("network_error"), {"cancelled": False}):
                    with patch.object(instance._transport, "cancel_pairing",
                                      side_effect=failure if isinstance(failure, Exception) else None,
                                      return_value=failure):
                        with self.assertRaises(RuntimeError):
                            await instance._handle_pairing_cancel(Request())
                    self.assertEqual(pairing, instance._store.pairing())
                self.assertEqual({"cancelled": True}, await instance._handle_pairing_cancel(Request()))
                self.assertIsNone(instance._store.pairing())
                await instance._handle_pairing(None)
                self.assertEqual(2, instance._transport.pairing_calls)
                instance._store.update_binding(1, 2, "paired", 1)
                self.assertIsNone(instance._store.pairing())
                with self.assertRaises(RuntimeError):
                    await instance._handle_pairing_cancel(Request())
            finally:
                await instance.close()

    async def test_account_unlink_keeps_durable_pending_when_transport_is_offline(self):
        config = FakeConfig(enabled=True)
        instance = component.TreeDAutoPrinter(config)

        class FakeStore:
            requested = False

            def request_unlink(self):
                self.requested = True

            def unlink_intent(self):
                return {"binding_epoch": 3, "phase": "request"}

            def status(self):
                return {"binding_state": "paired", "binding_epoch": 3,
                        "owner_profile": {"display_name": "Owner"},
                        "display_name": "Printer", "unlink_pending": True}

        class FakeTransport:
            def unlink(self, binding_epoch, confirm):
                raise TransportError("network_error")

        class Request:
            def get_args(self):
                return {}

        instance._store = FakeStore()
        instance._transport = FakeTransport()

        result = await instance._handle_account_unlink(Request())

        self.assertTrue(instance._store.requested)
        self.assertTrue(result["enabled"])
        self.assertTrue(result["configured"])
        self.assertEqual(1, result["protocol_version"])
        self.assertTrue(result["unlink_pending"])
        self.assertEqual({"account_linking": True, "pairing_cancel": True,
                          "offline_packages_v1": True}, result["capabilities"])

    async def test_pending_unlink_preserves_old_control_outbox_until_confirm(self):
        printer_id = "11111111-1111-4111-8111-111111111111"
        profile_id = "treed_v2_corexy_v1"
        old_boot = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        instance = component.TreeDAutoPrinter(FakeConfig())

        directory = tempfile.TemporaryDirectory()
        store = AutoPrinterStore(Path(directory.name), printer_id, profile_id)
        instance._store = store
        try:
            store.update_binding(1, 1, "paired", 1,
                                owner_profile={"display_name": "Owner"}, display_name="Printer")
            store.execution_set_boot(old_boot)
            old_receipt = store.execution_append_event(
                old_boot, 1, "run_state", "2030-01-01T00:00:00Z", None, {"state": "queued"})
            store.execution_ack_events([old_receipt["event_id"]], [old_receipt["event_id"]], [], [], old_boot)
            old_pending = store.execution_append_event(
                old_boot, 1, "run_state", "2030-01-01T00:00:01Z", None, {"state": "printing"})
        except Exception:
            store.close()
            directory.cleanup()
            raise

        class FakeExecutor:
            def __init__(self):
                self.active_boot_id = old_boot
                self.acknowledgements = []
                self.quiescence = [False, True]

            async def sync_fields(self):
                return {"agent_boot_id": self.active_boot_id, "snapshot_seq": 1,
                        "snapshot": {"printer_id": printer_id, "profile_id": profile_id},
                        "events": store.execution_events_pending(self.active_boot_id),
                        "acknowledged_event_ids": store.execution_pending_receipts(self.active_boot_id),
                        "command_results": []}

            async def acknowledge(self, response, submitted):
                self.acknowledgements.append((response, submitted))

            async def unlink_quiescent(self, device=None):
                return self.quiescence.pop(0)

            def reset_outbox_namespace(self, boot_id):
                self.active_boot_id = boot_id

        class FakeTransport:
            def __init__(self):
                self.sync_payloads = []
                self.confirm_calls = []
                self.sync_count = 0

            def sync(self, payload):
                self.sync_payloads.append(payload)
                self.sync_count += 1
                response = {"protocol_version": 1, "printer_id": printer_id,
                            "control_epoch": 2, "binding_epoch": 1, "binding_state": "paired",
                            "state_version": 2, "queue_revision": 1, "applied_queue_revision": 1,
                            "execution_authority": "printer", "storage_authority": "printer",
                            "configuration_version": 1, "transfers": [], "commands": [],
                            "snapshot": {"printer_id": printer_id, "profile_id": profile_id},
                            "owner_profile": {"display_name": "Owner"}, "display_name": "Printer",
                            "unlink_pending": True, "ack_event_ids": [], "ignored_event_ids": [],
                            "reason": "control_epoch_mismatch" if self.sync_count == 1 else None}
                if self.sync_count == 1:
                    response["ignored_event_ids"] = [{"event_id": old_pending["event_id"],
                                                       "reason": "control_epoch_mismatch"}]
                return response

            def unlink(self, binding_epoch, confirm):
                self.confirm_calls.append((binding_epoch, confirm))
                return {"binding_state": "unpaired", "binding_epoch": 2, "control_epoch": 2,
                        "queue_revision": 1, "owner_profile": None, "display_name": "Printer",
                        "unlink_pending": False}

        executor = FakeExecutor()
        transport = FakeTransport()
        instance._executor = executor
        instance._transport = transport
        instance._device = object()
        try:
            await instance._sync_once()
            self.assertEqual([old_pending], transport.sync_payloads[0]["events"])
            self.assertEqual([old_receipt["event_id"]], transport.sync_payloads[0]["acknowledged_event_ids"])
            self.assertTrue((await asyncio.to_thread(store.session))["unlink_pending"])
            self.assertEqual([], executor.acknowledgements)
            self.assertEqual([], transport.confirm_calls)

            await instance._sync_once()
            self.assertEqual([], transport.sync_payloads[1]["events"])
            self.assertEqual([], transport.sync_payloads[1]["acknowledged_event_ids"])
            self.assertEqual([], executor.acknowledgements)
            self.assertEqual([(1, True)], transport.confirm_calls,
                             f"quiescence remaining: {executor.quiescence}")
            self.assertEqual("unpaired", (await asyncio.to_thread(store.session))["binding_state"])
            self.assertFalse((await asyncio.to_thread(store.session))["unlink_pending"])
            self.assertEqual([old_pending], store.execution_events_pending(old_boot))
            self.assertEqual([old_receipt["event_id"]], store.execution_pending_receipts(old_boot))
            self.assertNotEqual(old_boot, executor.active_boot_id)

            await asyncio.to_thread(store.update_binding, 3, 3, "paired", 1,
                                    owner_profile={"display_name": "New owner"})
            new_fields = await executor.sync_fields()
            self.assertEqual(executor.active_boot_id, new_fields["agent_boot_id"])
            self.assertEqual([], new_fields["events"])
            self.assertEqual([], new_fields["acknowledged_event_ids"])
        finally:
            store.close()
            directory.cleanup()

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

    def test_pairing_requires_configured_frontend_origin_path_and_code(self):
        transport = AutoPrinterTransport("https://api.example", "x" * 32, "https://app.example")
        responses = [
            {"code": "ABCD2345", "expires_at": "2030-01-01T00:00:00Z",
             "verification_url": "https://app.example/autoprinter/connect#code=ABCD2345", "secret": "hidden"},
            {"code": "ABCD2345", "expires_at": "2030-01-01T00:00:00Z",
             "verification_url": "https://evil.example/autoprinter/connect#code=ABCD2345"},
            {"code": "ABCD2345", "expires_at": "2030-01-01T00:00:00Z",
             "verification_url": "https://app.example/other#code=ABCD2345"},
            {"code": "ABCD2345", "expires_at": "2030-01-01T00:00:00Z",
             "verification_url": "https://app.example/autoprinter/connect#code=ZZZZ9999"},
        ]
        for index, response in enumerate(responses):
            with self.subTest(index=index), patch.object(transport, "_json", return_value=response):
                if index == 0:
                    self.assertEqual({key: response[key] for key in ("code", "expires_at", "verification_url")},
                                     transport.pairing())
                else:
                    with self.assertRaises(TransportError):
                        transport.pairing()

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
                    component.STATUS_ENDPOINT, component.PAIRING_ENDPOINT,
                    component.PAIRING_CANCEL_ENDPOINT, component.ACCOUNT_UNLINK_ENDPOINT, component.CONTROL_ENDPOINT})
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
                self.bed_checker = None

            async def status(self):
                return {}

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
                        "ack_event_ids": [event_id], "ignored_event_ids": [],
                        "owner_profile": {"display_name": "Owner", "avatar_url": "https://treed.example/a.jpg"}}

        with tempfile.TemporaryDirectory() as directory:
            instance = component.TreeDAutoPrinter(FakeConfig())
            instance._store = AutoPrinterStore(Path(directory), printer_id, profile_id)
            instance._store.update_binding(1, 1, "paired", 1)
            instance._executor = FakeExecutor()
            transport = FakeTransport()
            instance._transport = transport
            try:
                await instance._sync_once()

                self.assertIsNotNone(instance._last_sync_at)
                status = await instance._handle_status(None)
                self.assertEqual(instance._last_sync_at, status["last_sync_at"])
                self.assertEqual({"display_name": "Owner", "avatar_url": "https://treed.example/a.jpg"},
                                 status["owner_profile"])
                self.assertIn("journal", status)
                self.assertNotIn("last_sync_at", transport.payload)
                self.assertNotIn("journal", transport.payload["snapshot"])
                self.assertEqual(transport.payload["events"], [event])
                self.assertEqual(transport.payload["agent_boot_id"], "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
                self.assertEqual(1, transport.payload["binding_epoch"])
                self.assertEqual([item["event_id"] for item in instance._executor.ack[1]["events"]], [event_id])
                self.assertEqual(instance._executor.ack[1]["binding_epoch"], 1)
                last_sync = instance._last_sync_at
                with patch.object(transport, "sync", return_value={"protocol_version": 999}):
                    with self.assertRaises(ValueError):
                        await instance._sync_once()
                self.assertEqual(last_sync, instance._last_sync_at)
                instance._store.update_binding(2, 1, "revoked", 1)
                status = await instance._handle_status(None)
                self.assertIsNone(status["last_sync_at"])
                self.assertIsNone(status["owner_profile"])
                self.assertNotIn("journal", status)
            finally:
                await instance.close()

    async def test_sync_after_lost_unlink_confirm_rotates_outbox_before_new_binding(self):
        printer_id = "11111111-1111-4111-8111-111111111111"
        profile_id = "treed_v2_corexy_v1"
        old_boot = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        snapshot = {"printer_id": printer_id, "profile_id": profile_id}
        event = None
        event_id = None

        class FakeExecutor:
            enabled = False

            def __init__(self):
                self.rotated_to = None
                self.acknowledged = False

            async def sync_fields(self):
                return {"agent_boot_id": old_boot, "snapshot_seq": 1, "snapshot": snapshot,
                        "events": [event], "acknowledged_event_ids": [], "command_results": []}

            def reset_outbox_namespace(self, boot_id):
                self.rotated_to = boot_id

            async def acknowledge(self, response, submitted):
                self.acknowledged = True

        class FakeTransport:
            def sync(self, payload):
                self.payload = payload
                return {"protocol_version": 1, "printer_id": printer_id, "control_epoch": 1,
                        "binding_epoch": 2, "binding_state": "unpaired", "state_version": 2,
                        "queue_revision": 1, "applied_queue_revision": 1,
                        "execution_authority": "printer", "storage_authority": "printer",
                        "configuration_version": 1, "transfers": [], "commands": [],
                        "snapshot": snapshot, "owner_profile": None, "unlink_pending": False,
                "ack_event_ids": [event_id], "ignored_event_ids": []}

        with tempfile.TemporaryDirectory() as directory:
            instance = component.TreeDAutoPrinter(FakeConfig())
            store = AutoPrinterStore(Path(directory), printer_id, profile_id)
            instance._store = store
            store.update_binding(1, 1, "paired", 1, owner_profile={"display_name": "Owner"})
            store.execution_set_boot(old_boot)
            event = store.execution_append_event(old_boot, 1, "run_state", "2030-01-01T00:00:00Z",
                                                 None, {"state": "printing"})
            event_id = event["event_id"]
            store.request_unlink()
            store.set_unlink_phase(1, "confirm")
            executor = FakeExecutor()
            instance._executor = executor
            transport = FakeTransport()
            instance._transport = transport
            try:
                await instance._sync_once()

                self.assertEqual([event], transport.payload["events"])
                self.assertFalse(executor.acknowledged)
                self.assertNotEqual(old_boot, executor.rotated_to)
                self.assertEqual(executor.rotated_to, store._get("execution_boot_id"))
                self.assertEqual([], store.execution_events_pending(executor.rotated_to))
                self.assertEqual([event_id], [item["event_id"] for item in
                                              store.execution_events_pending(old_boot)])
                self.assertEqual("unpaired", store.session()["binding_state"])
                self.assertFalse(store.session()["unlink_pending"])
            finally:
                await instance.close()


if __name__ == "__main__":
    unittest.main()
