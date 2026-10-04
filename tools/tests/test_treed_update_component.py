"""Offline API compatibility and polling contract for Moonraker updates."""

import asyncio
import importlib.util
import json
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "treed_update_component", ROOT / "moonraker/components/treed_update.py")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class ApiError(Exception):
    pass


class Request:
    def __init__(self, **args):
        self.args = args

    def get_str(self, key, default=None):
        value = self.args.get(key, default)
        if not isinstance(value, str):
            raise ValueError(key)
        return value

    def get(self, key, default=None):
        return self.args.get(key, default)


class UpdateComponentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.api = NS(query_objects=AsyncMock(return_value={"print_stats": {"state": "standby"}}))
        self.endpoints = {}
        self.server = NS(
            lookup_component=lambda _name: self.api,
            error=lambda message, code=400: ApiError(f"{code}: {message}"),
            register_endpoint=lambda path, methods, handler, **kwargs:
                self.endpoints.update({path: (methods, handler)}),
        )
        values = {
            "state_file": str(root / "state.json"),
            "core_manifest_path": str(root / "core-manifest.json"),
            "core_update_command": str(root / "treed-core-update"),
            "submit_command": "/usr/bin/sudo -n /usr/local/sbin/treed-update-service submit",
            "version_file": str(root / "VERSION"),
            "shell_manifest_path": str(root / "ui-manifest.json"),
        }
        config = NS(get_server=lambda: self.server, get=lambda key, default=None: values.get(key, default))
        self.component = module.TreeDUpdate(config)

    async def test_legacy_apply_without_request_id_returns_fast_accepted_operation(self):
        async def make_process(*args, **_kwargs):
            payload = {
                "accepted": True, "operationId": "operation-1", "requestId": args[-3],
                "status": "queued", "phase": "queued", "progress": 0,
                "message": "Обновление поставлено в очередь.",
            }
            return NS(returncode=0, communicate=AsyncMock(return_value=(json.dumps(payload).encode(), b"")))

        with patch.object(asyncio, "create_subprocess_exec", new=AsyncMock(side_effect=make_process)) as submit, \
             patch.object(self.component, "_check_releases", new=AsyncMock()) as release_check:
            result = await self.component._handle_apply(Request(targetId="printer-ui", targetTag="ui-main-123-1"))

        self.assertEqual(result["operationId"], "operation-1")
        self.assertTrue(result["busy"])
        self.assertTrue(result["available"])
        request_id, target_id, tag = submit.await_args.args[-3:]
        self.assertRegex(request_id, re.compile(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"))
        self.assertEqual((target_id, tag), ("printer-ui", "ui-main-123-1"))
        release_check.assert_not_awaited()

    async def test_status_is_local_and_requires_installed_runtime_updater(self):
        result = await self.component._handle_status(object())
        core = next(row for row in result["releaseResults"] if row["id"] == "printer-core")
        self.assertNotIn("firmware", result)
        self.assertFalse(core["canApply"])
        self.assertFalse(core["capability"]["supported"])
        self.assertEqual(core["capability"]["reasonCode"], "core_runtime_updater_missing")

    async def test_core_version_uses_confirmed_runtime_manifest_and_capability(self):
        self.component.version_file.write_text("9.0.0", encoding="utf-8")
        self.component.core_update_command.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
        self.component.core_update_command.chmod(0o755)
        self.component.core_manifest_path.write_text(json.dumps({
            "kind": "treed-core-runtime", "schema": 1, "version": "0.2.0"}), encoding="utf-8")
        self.assertEqual(self.component._read_core_version(), "0.2.0")
        self.assertTrue(self.component._core_capability()["supported"])

    async def test_old_source_only_core_release_is_not_applicable(self):
        target = self.component._build_targets()[1]
        releases = [{"tag_name": "v0.2.0", "assets": [{"name": "treed-mainshellos-source.zip"}]}]
        with patch.object(module, "_fetch_releases", return_value=releases):
            result = await self.component._check_target(target)
        self.assertEqual(result["status"], "unknown")

    async def test_core_apply_reuses_existing_submission_and_operation_contract(self):
        process = NS(returncode=0, communicate=AsyncMock(return_value=(json.dumps({
            "accepted": True, "operationId": "core-operation", "status": "queued",
            "targetId": "printer-core", "targetTag": "v0.2.0"}).encode(), b"")))
        with patch.object(self.component, "_core_capability", return_value={"supported": True}), \
             patch.object(asyncio, "create_subprocess_exec", new=AsyncMock(return_value=process)) as submit:
            result = await self.component._handle_apply(Request(targetId="printer-core", targetTag="v0.2.0"))
        self.assertTrue(result["busy"])
        self.assertEqual(result["operationId"], "core-operation")
        self.assertEqual(submit.await_args.args[-2:], ("printer-core", "v0.2.0"))

    async def test_bad_request_id_is_rejected_before_root_submission(self):
        with patch.object(asyncio, "create_subprocess_exec", new=AsyncMock()) as submit:
            with self.assertRaises(ApiError):
                await self.component._handle_apply(Request(
                    requestId="not-an-id", targetId="printer-ui", targetTag="ui-main-123-1"))
        submit.assert_not_awaited()

    async def test_state_directory_is_reported_as_unreadable_and_blocks_apply(self):
        self.component.state_file.mkdir()

        result = await self.component._handle_status(object())

        self.assertTrue(result["busy"])
        self.assertFalse(result["canApply"])
        self.assertEqual(result["resultCode"], "state_unreadable")
        ui = next(row for row in result["releaseResults"] if row["id"] == "printer-ui")
        self.assertFalse(ui["canApply"])

    async def test_ui_release_without_sha256_digest_is_not_advertised_as_applicable(self):
        release = {
            "tag_name": "ui-main-123-1", "assets": [{
                "name": "treed-shell-ui.zip", "browser_download_url": "https://example.invalid/ui.zip",
                "digest": None,
            }],
        }
        self.assertFalse(module._has_verified_ui_asset([release], release["tag_name"], "treed-shell-ui.zip"))
        release["assets"][0]["digest"] = "sha256:" + "a" * 64
        self.assertTrue(module._has_verified_ui_asset([release], release["tag_name"], "treed-shell-ui.zip"))


if __name__ == "__main__":
    unittest.main()
