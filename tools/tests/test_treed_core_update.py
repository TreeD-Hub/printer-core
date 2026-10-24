"""Назначение: адресные проверки пакета, сохранности данных и journal recovery.
Контур: временные файлы и mock services/network; без принтера и systemd.
"""

# Блок 1: Реальный installer в изолированном runtime.
import contextlib
import configparser
import hashlib
import io
import itertools
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
import uuid
from unittest.mock import ANY, call, patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "runtime-scripts/treed-update/treed-core-update"
GIT_BASH = Path(r"C:\Program Files\Git\bin\bash.exe")
core = types.ModuleType("treed_core_update")
exec(compile(PATH.read_text(encoding="utf-8"), str(PATH), "exec"), core.__dict__)
core_load_package = core.load_package
core_update_admission = core.update_admission
core_services_inactive = core.services_inactive
OPERATION = "11111111-1111-4111-8111-111111111111"
LIGHT_KEYS = ("klipper/treed_light_control.py",
              "config/profiles/treed_v2_corexy_v1/service_fans.cfg",
              "config/profiles/treed_v2_corexy_v1/macros_ui_contract.cfg")


def build_source_with_worktree_files(repo, archive):
    core.build_source(repo, archive)
    plan = json.loads((repo / core.MANAGED_RUNTIME).read_text(encoding="utf-8"))
    with zipfile.ZipFile(archive) as bundle:
        content = {item.filename: bundle.read(item) for item in bundle.infolist()}
    for relative in [core.MANAGED_RUNTIME, *plan["inputs"], *(
            f"loader/steps/{name}.sh" for name in plan["steps"])]:
        content[relative] = (repo / relative).read_bytes()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name, data in content.items():
            bundle.writestr(name, data)


class CoreUpdateTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.env = {"TREED_UPDATE_PI_HOME": str(self.root / "home"),
                    "TREED_UPDATE_PI_USER": "radxa", "TREED_UPDATE_PI_GROUP": "radxa",
                    "TREED_CORE_MANIFEST_FILE": str(self.root / "state/core-manifest.json"),
                    "TREED_CORE_STATE_DIR": str(self.root / "state/core")}
        self.locations = {name: self.root / name for name in ("config", "klipper", "moonraker", "camera", "vision", "sbin")}
        self.old = {
            "config/printer.cfg": b"[include profiles/treed_v2_corexy_v1/macros.cfg]\n\n" + core.MARKER + b"\n#*# [input_shaper]\n#*# shaper_freq_x = 43\n",
            "config/profiles/treed_v2_corexy_v1/macros.cfg": b"[gcode_macro OLD]\ngcode: G4 P0\n",
            "klipper/treed_driver_mode.py": b"version = 1\n",
            "moonraker/treed_update.py": b"version = 1\n",
        }
        self.payload = dict(self.old)
        self.payload.update({"config/printer.cfg": b"# new release\n[include profiles/treed_v2_corexy_v1/macros.cfg]\n",
                             "klipper/treed_driver_mode.py": b"version = 2\n",
                             "moonraker/treed_update.py": b"version = 2\n"})
        self.payload.update({key: core.sources(ROOT)[key].read_bytes() for key in LIGHT_KEYS})
        for name in self.locations.values():
            name.mkdir()
        for key, data in self.old.items():
            path = core.destination(key, self.locations)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.previous = {"kind": core.KIND, "schema": 1, "version": "0.1.0", "files": [
            {"path": key, "runtimeSha256": core.content_hash(key, value)} for key, value in self.old.items()]}
        core.write_json(core.manifest_path(self.env), self.previous)
        self.manifest = dict(self.previous, version="0.2.0", tag="v0.2.0")
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(core, "roots", return_value=self.locations))
        self.stack.enter_context(patch.object(core, "download"))
        self.stack.enter_context(patch.object(core, "load_package", side_effect=lambda *_: (self.manifest, self.payload)))
        self.vision_units = self.stack.enter_context(patch.object(core, "vision_units_installed", return_value=True))
        self.compatibility = self.stack.enter_context(patch.object(core, "check_compatibility"))
        self.phase = self.stack.enter_context(patch.object(core, "phase"))
        self.services = self.stack.enter_context(patch.object(core, "services"))
        self.services_inactive = self.stack.enter_context(patch.object(core, "services_inactive", return_value=True))
        self.admission = self.stack.enter_context(patch.object(core, "update_admission"))
        self.stack.enter_context(patch.object(core, "idle", return_value=True))
        self.stop_heating = self.stack.enter_context(patch.object(core, "stop_heating"))
        self.wait = self.stack.enter_context(patch.object(core, "wait_ready", return_value=True))
        self.stack.enter_context(patch.object(core, "owner", return_value=(
            getattr(os, "getuid", lambda: 0)(), getattr(os, "getgid", lambda: 0)())))

    def apply(self):
        return core.apply("v0.2.0", OPERATION, self.env)

    def seed_failed_provisioning(self):
        self.previous = dict(self.previous, version="0.0.1", tag="v0.0.1", sha="a" * 40)
        core.write_json(core.manifest_path(self.env), self.previous)
        old_manifest = {"kind": core.KIND, "schema": 1, "version": "0.1.0", "tag": "v0.1.0",
                        "sha": "b" * 40, "requires": {"klipper": "c" * 40, "moonraker": "d" * 40},
                        "provisioning": {"schema": 1, "revision": "e" * 64}}
        old_operation = "33333333-3333-4333-8333-333333333333"
        old_journal = core.journal_path(self.env, old_operation)
        old_journal.parent.mkdir(parents=True)
        core.prepare(old_manifest, self.payload, self.env, old_journal)
        transaction = core.read_json(old_journal)
        old_reset = [{"path": "config/printer.cfg", "sha256": core.sha(self.old["config/printer.cfg"])}]
        transaction.update(stage="provisioning_failed", provisioningStarted=True,
                           resetConfigs=old_reset)
        core.write_json(old_journal, transaction)
        partial = next(row for row in transaction["entries"]
                       if row["path"] == "klipper/treed_driver_mode.py")
        partial_data = core.read_json(old_journal.parent / "manifest.new.json")
        self.assertIn(partial["path"], {row["path"] for row in partial_data["files"]})
        self.assertNotEqual(core.read_file(core.destination(partial["path"], self.locations))[0],
                            (old_journal.parent / (partial["backup"] + ".new")).read_bytes())
        core.write_file(core.destination(partial["path"], self.locations),
                        (old_journal.parent / (partial["backup"] + ".new")).read_bytes())
        return old_operation, old_reset

    def build_current_release_archives(self):
        package = self.root / "replacement-package.zip"
        source = self.root / "replacement-source.zip"
        core.build(ROOT, package)
        build_source_with_worktree_files(ROOT, source)
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        manifest, payload = core_load_package(package, "v" + version)
        return package.read_bytes(), source.read_bytes(), manifest, payload

    @unittest.skipIf(os.name == "nt", "flock is a Linux lock contract")
    def test_borrowed_loader_lock_remains_held_and_rejects_other_open_descriptions(self):
        import fcntl

        lock_path = self.root / "update.lock"
        lock_path.touch()
        fd = os.open(lock_path, os.O_RDWR)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        env = dict(self.env, TREED_UPDATE_LOCK_FILE=str(lock_path))
        with patch.dict(os.environ, {"TREED_UPDATE_LOCK_FD": str(fd)}):
            self.assertIsNone(core.acquire_loader_update_lock(env))
        other_fd = os.open(lock_path, os.O_RDWR)
        self.addCleanup(os.close, other_fd)
        with self.assertRaises(BlockingIOError):
            fcntl.flock(other_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        other_lock_path = self.root / "other.lock"
        other_lock_path.touch()
        with patch.dict(os.environ, {"TREED_UPDATE_LOCK_FD": str(other_fd)}):
            with self.assertRaisesRegex(ValueError, "не совпадает"):
                core.acquire_loader_update_lock(dict(env, TREED_UPDATE_LOCK_FILE=str(other_lock_path)))

    def test_verify_cli_requires_matching_files_and_stable_readiness(self):
        installed = dict(self.previous, tag="v0.1.0")
        core.write_json(core.manifest_path(self.env), installed)
        with patch.object(core.os, "geteuid", return_value=0, create=True), \
             patch.object(core, "settings", return_value=self.env):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(core.main(["treed-core-update", "verify"]), 0)
            self.assertEqual(json.loads(output.getvalue()), {"tag": "v0.1.0"})
            self.wait.return_value = False
            with self.assertRaisesRegex(RuntimeError, "readiness"):
                core.main(["treed-core-update", "verify"])
            self.wait.return_value = True
            (self.locations["klipper"] / "treed_driver_mode.py").write_bytes(b"modified")
            with self.assertRaisesRegex(RuntimeError, "Runtime файл отличается"):
                core.main(["treed-core-update", "verify"])
        self.assertEqual(core.read_json(core.manifest_path(self.env)), installed)

    def test_managed_loader_receives_fixed_preservation_environment(self):
        self.manifest.update(requires={"klipper": "a" * 40, "moonraker": "b" * 40},
                             provisioning={"schema": 1, "revision": "c" * 64})
        self.stack.enter_context(patch.object(core.subprocess, "check_output", return_value=""))
        self.stack.enter_context(patch.object(core, "extract_source"))
        upstream = self.stack.enter_context(patch.object(core, "check_upstream_clean", wraps=core.check_upstream_clean))
        for name, suffix in (("klipper", Path("klippy/extras")),
                             ("moonraker", Path("moonraker/components"))):
            checkout = self.root / f"custom-{name}"
            location = checkout / suffix
            location.mkdir(parents=True)
            self.locations[name] = location
            self.env[f"TREED_CORE_{name.upper()}_DIR"] = str(checkout)
            key = f"{name}/treed_{'driver_mode' if name == 'klipper' else 'update'}.py"
            (location / Path(key).name).write_bytes(self.old[key])
        loader = self.stack.enter_context(patch.object(core, "run_managed_loader", return_value=0))
        self.assertEqual("applied", self.apply()["status"])
        args, kwargs = loader.call_args
        self.assertEqual(args[0], self.root / "state/core" / OPERATION / "source")
        self.assertEqual(args[1]["TREED_UPDATE_MANAGED"], "1")
        self.assertEqual(args[1]["TREED_UPDATE_MANAGED_LOADER"], "1")
        self.assertEqual(args[1]["TREED_UPDATE_OPERATION_ID"], OPERATION)
        self.assertEqual(args[1]["TREED_DEPLOY_MODE"], "preserve")
        self.assertEqual(args[1]["TREED_SHELL_INSTALL"], "0")
        self.assertEqual(args[1]["TREED_KLIPPER_SRC_DIR"], str(self.env_path("klipper")))
        self.assertEqual(args[1]["TREED_MOONRAKER_SRC_DIR"], str(self.env_path("moonraker")))
        journal = core.read_json(core.journal_path(self.env, OPERATION))
        self.assertEqual(journal["stage"], "committed")
        self.assertTrue(journal["provisioningStarted"])
        upstream.assert_called_once_with(self.locations, self.previous)
        self.assertEqual(self.compatibility.call_args.args[0], self.manifest)
        self.assertTrue(all("runtimeSha256" in row for row in self.compatibility.call_args.args[2]["files"]))

    def env_path(self, name):
        return Path(self.env[f"TREED_CORE_{name.upper()}_DIR"])

    def test_managed_loader_failure_keeps_durable_provisioning_failure_without_rollback(self):
        self.manifest.update(requires={"klipper": "a" * 40, "moonraker": "b" * 40},
                             provisioning={"schema": 1, "revision": "c" * 64})
        self.stack.enter_context(patch.object(core.subprocess, "check_output", return_value=""))
        self.stack.enter_context(patch.object(core, "extract_source"))
        self.stack.enter_context(patch.object(core, "run_managed_loader", return_value=1))
        result = self.apply()
        self.assertEqual("provisioning_failed", result["resultCode"])
        self.assertEqual("provisioning_failed", core.read_json(core.journal_path(self.env, OPERATION))["stage"])
        self.assertTrue(core.read_json(core.journal_path(self.env, OPERATION))["provisioningStarted"])
        self.assertEqual(self.payload["klipper/treed_driver_mode.py"],
                         core.destination("klipper/treed_driver_mode.py", self.locations).read_bytes())
        self.services.assert_called_once_with("stop")

    def test_committed_journal_write_error_does_not_runtime_rollback_provisioning(self):
        self.manifest.update(requires={"klipper": "a" * 40, "moonraker": "b" * 40},
                             provisioning={"schema": 1, "revision": "c" * 64})
        self.stack.enter_context(patch.object(core.subprocess, "check_output", return_value=""))
        self.stack.enter_context(patch.object(core, "extract_source"))
        self.stack.enter_context(patch.object(core, "run_managed_loader", return_value=0))
        original_write = core.write_json
        journal = core.journal_path(self.env, OPERATION)

        def fail_after_committed_write(path, value):
            original_write(path, value)
            if Path(path) == journal and value.get("stage") == "committed":
                raise OSError("simulated fsync failure after durable rename")

        self.stack.enter_context(patch.object(core, "write_json", side_effect=fail_after_committed_write))
        result = self.apply()
        self.assertEqual("provisioning_failed", result["resultCode"])
        self.assertTrue(core.read_json(journal)["provisioningStarted"])
        self.assertEqual(self.payload["klipper/treed_driver_mode.py"],
                         core.destination("klipper/treed_driver_mode.py", self.locations).read_bytes())
        self.assertEqual("0.2.0", core.read_json(core.manifest_path(self.env))["version"])
        self.services.assert_called_once_with("stop")

    def test_dirty_upstream_rejects_before_source_download_or_runtime_mutation(self):
        self.manifest.update(requires={"klipper": "a" * 40, "moonraker": "b" * 40},
                             provisioning={"schema": 1, "revision": "c" * 64})
        self.stack.enter_context(patch.object(core.subprocess, "check_output", return_value=" M klippy.py"))
        with self.assertRaisesRegex(ValueError, "Изменены исходники"):
            self.apply()
        self.services.assert_not_called()

    def test_printer_became_busy_after_download_creates_no_journal_or_side_effects(self):
        installed = core.read_json(core.manifest_path(self.env))
        with patch.object(core, "download") as download, patch.object(core, "idle", return_value=False):
            with self.assertRaises(core.PrinterNotReady):
                self.apply()
        download.assert_called_once()
        self.assertEqual(core.update_error_result(core.PrinterNotReady("busy"))["resultCode"],
                         "core_not_ready")
        self.assertFalse(core.journal_path(self.env, OPERATION).exists())
        self.assertEqual(core.read_json(core.manifest_path(self.env)), installed)
        self.services.assert_not_called()

    def test_replacement_replays_forward_and_preserves_fresh_save_config(self):
        if os.name == "nt" and GIT_BASH.is_file():
            original_run = subprocess.run

            def run_with_git_bash(command, *args, **kwargs):
                if command and Path(command[0]).name.lower() == "bash" and command[1:2] == ["-n"]:
                    command = [str(GIT_BASH), *command[1:]]
                return original_run(command, *args, **kwargs)

            self.stack.enter_context(patch.object(subprocess, "run", side_effect=run_with_git_bash))
        old_operation, old_resets = self.seed_failed_provisioning()
        package, source_archive, target_manifest, target_payload = self.build_current_release_archives()
        operation = str(uuid.uuid4())
        state_path = self.root / "state/update-state.json"
        self.env["TREED_UPDATE_STATE_FILE"] = str(state_path)
        state = {"busy": True, "status": "queued", "targetId": "printer-core",
                 "targetTag": target_manifest["tag"], "operationId": operation,
                 "resetConfigs": [], "supersedesOperationId": old_operation,
                 "supersedesTargetTag": "v0.1.0", "supersededResetConfigs": old_resets}
        core.write_json(state_path, state)

        def download(_tag, archive, _env, asset_name=core.ASSET):
            Path(archive).write_bytes(package if asset_name == core.ASSET else source_archive)

        target_paths = {core.destination(key, self.locations) for key in target_payload}
        original_write = core.write_file
        installed_paths = []

        def fail_mid_install(path, *args, **kwargs):
            if Path(path) in target_paths:
                if len(installed_paths) == 1:
                    raise OSError("simulated crash during forward install")
                installed_paths.append(Path(path))
            return original_write(path, *args, **kwargs)

        output = io.StringIO()
        with patch.object(core.os, "geteuid", return_value=0, create=True), \
                patch.object(core, "settings", return_value=self.env), \
                patch.object(core, "download", side_effect=download), \
                patch.object(core, "load_package", side_effect=core_load_package), \
                patch.object(core, "check_upstream_clean"), \
                patch.object(core, "write_file", side_effect=fail_mid_install), \
                contextlib.redirect_stdout(output):
            self.assertEqual(1, core.main(["treed-core-update", "apply", target_manifest["tag"], operation]))
        failed_result = json.loads(output.getvalue())
        self.assertEqual("core_update_failed", failed_result["resultCode"])
        self.assertEqual(1, len(installed_paths))
        journal = core.journal_path(self.env, operation)
        transaction = core.read_json(journal)
        self.assertTrue(transaction["forwardOnly"])
        self.assertEqual("installing", transaction["stage"])
        self.assertFalse(transaction["provisioningStarted"])
        self.assertEqual(old_resets, transaction["supersededResetConfigs"])
        self.assertEqual([], transaction["resetConfigs"])
        self.assertEqual(self.previous, core.read_json(core.manifest_path(self.env)))
        installed_row = next(row for row in transaction["entries"]
                             if core.destination(row["path"], self.locations) == installed_paths[0])
        installed_data = (journal.parent / (installed_row["backup"] + ".new")).read_bytes()
        self.assertEqual(installed_data, installed_paths[0].read_bytes())
        state.update(status="error", busy=False)
        core.write_json(state_path, state)
        self.assertEqual([], core.config_conflicts(self.env))
        conflict_path = core.destination("klipper/treed_driver_mode.py", self.locations)
        conflict_data = conflict_path.read_bytes()
        conflict_path.write_bytes(b"unexpected local edit")
        self.assertEqual([{"path": "klipper/treed_driver_mode.py",
                           "sha256": core.sha(b"unexpected local edit")}],
                         core.config_conflicts(self.env))
        conflict_path.write_bytes(conflict_data)
        state.update(status="queued", busy=True)
        core.write_json(state_path, state)

        uninstalled_row = next(row for row in transaction["entries"]
                               if not row["delete"] and row["path"] != installed_row["path"])
        staged_path = journal.parent / (uninstalled_row["backup"] + ".new")
        staged_data = staged_path.read_bytes()
        staged_path.write_bytes(b"corrupt target bytes")
        self.services.reset_mock()
        with self.assertRaisesRegex(ValueError, "staged target"):
            core.recover(self.env, operation)
        self.services.assert_not_called()
        self.assertEqual("0.0.1", core.read_json(core.manifest_path(self.env))["version"])
        staged_path.write_bytes(staged_data)
        source_path = journal.parent / core.SOURCE_ASSET
        source_bytes = source_path.read_bytes()
        source_path.write_bytes(b"corrupt zip stream")
        with self.assertRaises(core.ValidationError):
            core.recover(self.env, operation)
        self.assertEqual("core_validation_failed",
                         core.update_error_result(core.ValidationError("corrupt source"))["resultCode"])
        self.services.assert_not_called()
        source_path.write_bytes(source_bytes)

        printer = core.destination("config/printer.cfg", self.locations)
        current_printer = printer.read_bytes()
        self.assertIn(core.MARKER, current_printer)
        printer.write_bytes(current_printer.replace(b"shaper_freq_x = 43", b"shaper_freq_x = 71"))
        def ready_before_commit(_manifest):
            self.assertEqual(self.previous, core.read_json(core.manifest_path(self.env)))
            return True

        self.wait.side_effect = ready_before_commit
        with patch.object(core, "run_managed_loader", return_value=0):
            result = core.recover(self.env, operation)
        self.assertEqual("applied", result["status"])
        self.assertEqual(target_manifest["version"], core.read_json(core.manifest_path(self.env))["version"])
        self.assertEqual("committed", core.read_json(journal)["stage"])
        self.assertIn(b"shaper_freq_x = 71", printer.read_bytes())
        self.assertNotEqual(old_resets, core.read_json(journal).get("resetConfigs"))

        self.wait.side_effect = None
        self.wait.return_value = True
        next_version = target_manifest["version"].rsplit(".", 1)
        next_version = f"{next_version[0]}.{int(next_version[1]) + 1}"
        next_manifest = dict(target_manifest, version=next_version, tag="v" + next_version)
        pins = itertools.cycle([target_manifest["requires"]["klipper"],
                                target_manifest["requires"]["moonraker"]])
        next_operation = str(uuid.uuid4())
        with patch.object(core, "download"), patch.object(core, "load_package",
                return_value=(next_manifest, target_payload)), patch.object(core, "check_upstream_clean"), \
                patch.object(core, "git_head", side_effect=lambda _path: next(pins)):
            next_result = core.apply(next_manifest["tag"], next_operation, self.env)
        self.assertEqual("applied", next_result["status"])
        self.assertEqual(next_version, core.read_json(core.manifest_path(self.env))["version"])

    def test_replacement_does_not_mutate_runtime_when_admission_gate_rejects_print(self):
        old_operation, old_resets = self.seed_failed_provisioning()
        operation = str(uuid.uuid4())
        tag = "v0.2.0"
        state = {"supersedesOperationId": old_operation, "supersedesTargetTag": "v0.1.0",
                 "supersededResetConfigs": old_resets}
        replacement = core.replacement_context(state, operation, tag, self.env)
        target = dict(self.previous, version=tag[1:], tag=tag, sha="f" * 40,
                      requires={"klipper": "c" * 40, "moonraker": "d" * 40},
                      provisioning={"schema": 1, "revision": "9" * 64})
        runtime_before = {key: core.destination(key, self.locations).read_bytes()
                          for key in self.old}
        self.admission.side_effect = RuntimeError("printer is active")
        self.services_inactive.return_value = False
        with patch.object(core, "download"), patch.object(core, "load_package",
                return_value=(target, self.payload)), patch.object(core, "extract_source"), \
                patch.object(core, "check_upstream_clean"):
            with self.assertRaisesRegex(RuntimeError, "Klipper не подтверждён как остановленный"):
                core.apply(tag, operation, self.env, [], replacement)
        self.services.assert_not_called()
        self.stop_heating.assert_not_called()
        self.assertEqual(runtime_before, {key: core.destination(key, self.locations).read_bytes()
                                          for key in self.old})
        self.assertEqual(self.previous, core.read_json(core.manifest_path(self.env)))
        transaction = core.read_json(core.journal_path(self.env, operation))
        self.assertEqual("validated", transaction["stage"])
        self.assertTrue(transaction["forwardOnly"])

    def test_validated_replacement_recovers_after_admission_gate_defers(self):
        if os.name == "nt" and GIT_BASH.is_file():
            original_run = subprocess.run

            def run_with_git_bash(command, *args, **kwargs):
                if command and Path(command[0]).name.lower() == "bash" and command[1:2] == ["-n"]:
                    command = [str(GIT_BASH), *command[1:]]
                return original_run(command, *args, **kwargs)

            self.stack.enter_context(patch.object(subprocess, "run", side_effect=run_with_git_bash))
        old_operation, old_resets = self.seed_failed_provisioning()
        package, source_archive, target, target_payload = self.build_current_release_archives()
        operation = str(uuid.uuid4())
        reset_path = core.destination("klipper/treed_driver_mode.py", self.locations)
        reset_path.write_bytes(b"version = 77\n")
        new_resets = [{"path": "klipper/treed_driver_mode.py", "sha256": core.sha(
            reset_path.read_bytes())}]
        state = {"supersedesOperationId": old_operation, "supersedesTargetTag": "v0.1.0",
                 "supersededResetConfigs": old_resets}
        replacement = core.replacement_context(state, operation, target["tag"], self.env)

        def download(_tag, archive, _env, asset_name=core.ASSET):
            Path(archive).write_bytes(package if asset_name == core.ASSET else source_archive)

        self.admission.side_effect = RuntimeError("printer is active")
        self.services_inactive.return_value = False
        with patch.object(core, "download", side_effect=download), \
                patch.object(core, "load_package", side_effect=core_load_package), \
                patch.object(core, "check_upstream_clean"):
            with self.assertRaisesRegex(RuntimeError, "Klipper не подтверждён как остановленный"):
                core.apply(target["tag"], operation, self.env, new_resets, replacement)

        journal = core.journal_path(self.env, operation)
        transaction = core.read_json(journal)
        self.assertEqual("validated", transaction["stage"])
        self.assertFalse(transaction["provisioningStarted"])
        self.assertEqual(new_resets, transaction["resetConfigs"])
        self.assertEqual(old_resets, transaction["supersededResetConfigs"])
        self.assertEqual(self.previous, core.read_json(core.manifest_path(self.env)))
        self.services.assert_not_called()
        self.admission.side_effect = None
        self.admission.reset_mock()
        self.services_inactive.return_value = True
        with patch.object(core, "run_managed_loader", return_value=0):
            result = core.recover(self.env, operation)
        self.assertEqual("core_updated_recovered", result["resultCode"])
        self.assertEqual("committed", core.read_json(journal)["stage"])
        self.assertEqual(core.read_json(journal.parent / "manifest.new.json"),
                         core.read_json(core.manifest_path(self.env)))

    def test_committed_forward_only_journal_revalidates_runtime_and_upstream(self):
        if os.name == "nt" and GIT_BASH.is_file():
            original_run = subprocess.run

            def run_with_git_bash(command, *args, **kwargs):
                if command and Path(command[0]).name.lower() == "bash" and command[1:2] == ["-n"]:
                    command = [str(GIT_BASH), *command[1:]]
                return original_run(command, *args, **kwargs)

            self.stack.enter_context(patch.object(subprocess, "run", side_effect=run_with_git_bash))
        old_operation, old_resets = self.seed_failed_provisioning()
        package, source_archive, target, payload = self.build_current_release_archives()
        operation = str(uuid.uuid4())
        replacement = core.replacement_context({
            "supersedesOperationId": old_operation, "supersedesTargetTag": "v0.1.0",
            "supersededResetConfigs": old_resets,
        }, operation, target["tag"], self.env)
        journal = core.journal_path(self.env, operation)
        journal.parent.mkdir(parents=True)
        archive = journal.parent / core.SOURCE_ASSET
        archive.write_bytes(source_archive)
        core.extract_source(archive, journal.parent / "source", target, payload)
        core.prepare(target, payload, self.env, journal, [], replacement)
        expected = core.read_json(journal.parent / "manifest.new.json")
        transaction = core.read_json(journal)
        transaction.update(stage="committed", provisioningStarted=True)
        core.write_json(journal, transaction)
        core.write_json(core.manifest_path(self.env), expected)
        self.wait.return_value = True
        with patch.object(core, "check_compatibility"):
            with self.assertRaises(ValueError):
                core.recover(self.env, operation)
        self.wait.assert_not_called()
        self.assertEqual(expected, core.read_json(core.manifest_path(self.env)))

        self.services.reset_mock()
        self.wait.reset_mock()
        with patch.object(core, "check_compatibility", side_effect=ValueError("upstream refs mismatch")):
            with self.assertRaisesRegex(ValueError, "upstream refs mismatch"):
                core.recover(self.env, operation)
        self.wait.assert_not_called()
        self.services.assert_not_called()

    def test_config_conflicts_uses_public_recovery_metadata_without_private_staged_reads(self):
        old_operation, old_resets = self.seed_failed_provisioning()
        anchor = core.journal_path(self.env, old_operation)
        anchor_transaction = core.read_json(anchor)
        anchor_transaction.pop("targetTag")
        anchor_transaction.pop("operationId")
        core.write_json(anchor, anchor_transaction)
        new_key = LIGHT_KEYS[0]
        new_path = core.destination(new_key, self.locations)
        new_path.parent.mkdir(parents=True, exist_ok=True)
        new_path.write_bytes(self.payload[new_key] + b"# local edit\n")
        state_path = self.root / "state/update-state.json"
        self.env["TREED_UPDATE_STATE_FILE"] = str(state_path)
        state = {"targetId": "printer-core", "targetTag": "v0.1.0", "operationId": old_operation,
                 "status": "error", "busy": False, "resultCode": "provisioning_failed"}
        core.write_json(state_path, state)
        original_read_file = core.read_file
        original_read_bytes = Path.read_bytes

        def public_only_bytes(path):
            path = Path(path)
            if path.parent == anchor.parent and (
                    core.re.fullmatch(r"[0-9]+(?:\.new)?", path.name) or path.suffix == ".new"):
                raise AssertionError(f"inspection attempted private staged read: {path.name}")
            return original_read_bytes(path)

        def public_only_read(path):
            path = Path(path)
            if path.parent == anchor.parent and (
                    core.re.fullmatch(r"[0-9]+(?:\.new)?", path.name) or path.suffix == ".new"):
                raise AssertionError(f"inspection attempted private staged read: {path.name}")
            return original_read_file(path)

        with patch.object(core, "read_file", side_effect=public_only_read), \
                patch.object(core, "validate_forward_only", side_effect=AssertionError("private validator called")), \
                patch.object(Path, "read_bytes", new=public_only_bytes):
            conflicts = core.config_conflicts(self.env)
        self.assertNotIn("klipper/treed_driver_mode.py", {row["path"] for row in conflicts})
        self.assertIn(new_key, {row["path"] for row in conflicts})

        current_operation = str(uuid.uuid4())
        state.update(operationId=current_operation, targetTag="v0.1.1", resultCode="core_validation_failed",
                     lastFailurePhase="downloading", supersedesOperationId=old_operation,
                     supersedesTargetTag="v0.1.0", supersededResetConfigs=old_resets)
        core.write_json(state_path, state)
        with patch.object(core, "read_file", side_effect=public_only_read), \
                patch.object(core, "validate_forward_only", side_effect=AssertionError("private validator called")), \
                patch.object(Path, "read_bytes", new=public_only_bytes):
            conflicts = core.config_conflicts(self.env)
        self.assertNotIn("klipper/treed_driver_mode.py", {row["path"] for row in conflicts})
        self.assertIn(new_key, {row["path"] for row in conflicts})

        state.update(resultCode="corrective_precondition_deferred")
        state.pop("lastFailurePhase")
        core.write_json(state_path, state)
        with patch.object(core, "read_file", side_effect=public_only_read), \
                patch.object(core, "validate_forward_only", side_effect=AssertionError("private validator called")), \
                patch.object(Path, "read_bytes", new=public_only_bytes):
            deferred_conflicts = core.config_conflicts(self.env)
        self.assertEqual(conflicts, deferred_conflicts)

        new_path.write_bytes(self.payload[new_key])
        replacement = core.replacement_context(state, current_operation, "v0.1.1", self.env)
        target = dict(core.read_json(anchor.parent / "manifest.new.json"), version="0.1.1", tag="v0.1.1",
                      sha="f" * 40)
        target_journal = core.journal_path(self.env, current_operation)
        core.prepare(target, self.payload, self.env, target_journal, [], replacement)
        new_path.write_bytes(self.payload[new_key] + b"# edited after prepare\n")
        state.update(status="error", targetTag="v0.1.1", resultCode="core_update_failed",
                     lastFailurePhase="installing")
        core.write_json(state_path, state)
        target_anchor = target_journal

        def guarded_read(path):
            path = Path(path)
            if path.parent == target_anchor.parent and (
                    core.re.fullmatch(r"[0-9]+(?:\.new)?", path.name) or path.suffix == ".new"):
                raise AssertionError(f"inspection attempted private staged read: {path.name}")
            return original_read_file(path)

        def guarded_bytes(path):
            path = Path(path)
            if path.parent == target_anchor.parent and (
                    re.fullmatch(r"[0-9]+(?:\.new)?", path.name) or path.suffix == ".new"):
                raise AssertionError(f"inspection attempted private staged read: {path.name}")
            return original_read_bytes(path)

        with patch.object(core, "read_file", side_effect=guarded_read), \
                patch.object(core, "validate_forward_only", side_effect=AssertionError("private validator called")), \
                patch.object(Path, "read_bytes", new=guarded_bytes):
            conflicts = core.config_conflicts(self.env)
        self.assertIn(new_key, {row["path"] for row in conflicts})

    def assert_restored(self):
        for key, data in self.old.items():
            self.assertEqual(core.destination(key, self.locations).read_bytes(), data, key)
        self.assertEqual(core.read_json(core.manifest_path(self.env))["version"], "0.1.0")
        for key in LIGHT_KEYS:
            if key not in self.old:
                self.assertFalse(core.destination(key, self.locations).exists(), key)

    def seed_autoprinter_update(self):
        private = {
            "printer_data/system/treed-autoprinter-credentials.json": b'private-device-secret',
            "printer_data/system/treed-autoprinter/store.sqlite3": b'private-journal',
            "printer_data/system/treed-autoprinter/bindings/1/model.gcode": b'private-account-model',
            "treed/models/e0.pth": b'private-weights',
        }
        paths = {}
        for relative, data in private.items():
            path = Path(self.env["TREED_UPDATE_PI_HOME"]) / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            paths[path] = data
        for key in core.AUTOPRINTER_MODULES:
            self.payload[key] = b"version = 2\n"
        self.manifest["files"] = [{"path": key, "sha256": core.sha(data)}
                                  for key, data in self.payload.items()]
        return paths

    def test_autoprinter_update_and_recovery_preserve_private_data(self):
        private = self.seed_autoprinter_update()
        self.assertEqual("applied", self.apply()["status"])
        self.assertEqual("applied", core.recover(self.env, OPERATION)["status"])
        for path, data in private.items():
            self.assertEqual(data, path.read_bytes())
        for key in core.AUTOPRINTER_MODULES:
            self.assertEqual(self.payload[key], core.destination(key, self.locations).read_bytes())
        self.assertTrue(all(any(row["path"] == "moonraker/treed_autoprinter.py"
                                for row in call.args[0]["files"])
                            for call in self.wait.call_args_list))

    def test_failed_autoprinter_readiness_rolls_back_and_preserves_private_data(self):
        private = self.seed_autoprinter_update()
        self.wait.side_effect = [False, True]
        self.assertEqual("rolled_back", self.apply()["status"])
        self.assert_restored()
        for path, data in private.items():
            self.assertEqual(data, path.read_bytes())
        self.assertFalse(any(core.destination(key, self.locations).exists()
                             for key in core.AUTOPRINTER_MODULES))
        self.assertEqual(self.previous, self.wait.call_args_list[-1].args[0])
        self.assertEqual(2, self.wait.call_count)
        self.assertTrue(any(row["path"] == "moonraker/treed_vision_client.py"
                            for row in self.wait.call_args_list[0].args[0]["files"]))
        self.assertEqual(self.previous, self.wait.call_args_list[1].args[0])

    def test_provisioning_interruption_never_runtime_rolls_back(self):
        journal = core.journal_path(self.env, OPERATION)
        journal.parent.mkdir(parents=True)
        core.write_json(journal, {"schema": 1, "stage": "provisioning_failed", "entries": []})
        result = core.recover(self.env, OPERATION)
        self.assertEqual(result["resultCode"], "provisioning_interrupted")
        self.assertEqual(core.read_json(journal)["stage"], "provisioning_failed")
        self.assertEqual((self.locations["config"] / "printer.cfg").read_bytes(),
                         self.old["config/printer.cfg"])

    def interrupted_provisioning(self):
        journal, transaction = self.prepared_install_transaction()
        source = journal.parent / "source"
        files = {"VERSION": "0.2.0\n", "loader/loader.sh": "#!/bin/bash\nexit 0\n",
                 "runtime-versions.env": 'TREED_KLIPPER_REF="' + "a" * 40 + '"\nTREED_MOONRAKER_REF="' + "b" * 40 + '"\n',
                 "runtime-scripts/treed-vision/models.json": "{}\n"}
        with zipfile.ZipFile(journal.parent / core.SOURCE_ASSET, "w") as archive:
            for name, content in files.items():
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8", newline="\n")
                archive.writestr(name, content)
        expected = core.read_json(journal.parent / "manifest.new.json")
        expected.update(requires={"klipper": "a" * 40, "moonraker": "b" * 40},
                        provisioning={"schema": 1, "revision": core.provisioning_revision(source)})
        core.write_json(journal.parent / "manifest.new.json", expected)
        transaction.update(stage="provisioning_failed", provisioningStarted=True)
        core.write_json(journal, transaction)
        return journal, source

    def test_provisioning_retry_preserves_data_and_allows_next_update(self):
        private = self.seed_autoprinter_update()
        journal, source = self.interrupted_provisioning()
        with patch.object(core, "run_managed_loader", side_effect=[1, 0]) as loader:
            self.assertEqual("provisioning_failed", core.recover(self.env, OPERATION)["resultCode"])
            self.assertEqual("0.1.0", core.read_json(core.manifest_path(self.env))["version"])
            self.assertEqual("applied", core.recover(self.env, OPERATION)["status"])
            self.assertEqual(loader.call_args.args[0], source)
            self.assertEqual(loader.call_args.args[1]["TREED_UPDATE_OPERATION_ID"], OPERATION)
        self.assertEqual("committed", core.read_json(journal)["stage"])
        for path, data in private.items():
            self.assertEqual(data, path.read_bytes())
        self.manifest.update(version="0.3.0", tag="v0.3.0")
        self.assertEqual("applied", core.apply("v0.3.0", "22222222-2222-4222-8222-222222222222", self.env)["status"])

    def test_recovery_rejects_damaged_source_before_stopping_services(self):
        _, source = self.interrupted_provisioning()
        (source / "loader/loader.sh").write_text("broken", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Recovery source"):
            core.recover(self.env, OPERATION)
        self.services.assert_not_called()

    def test_provisioning_recovery_does_not_commit_without_readiness(self):
        journal, _ = self.interrupted_provisioning()
        self.wait.return_value = False
        with patch.object(core, "run_managed_loader", return_value=0), \
                self.assertRaisesRegex(RuntimeError, "readiness"):
            core.recover(self.env, OPERATION)
        self.assertEqual("0.1.0", core.read_json(core.manifest_path(self.env))["version"])
        self.assertNotEqual("committed", core.read_json(journal)["stage"])

    def test_committed_managed_provisioning_recovers_only_when_ready(self):
        journal = core.journal_path(self.env, OPERATION)
        journal.parent.mkdir(parents=True)
        expected = dict(self.manifest, files=[])
        core.write_json(core.manifest_path(self.env), expected)
        core.write_json(journal.parent / "manifest.new.json", expected)
        core.write_json(journal, {"schema": 1, "stage": "committed", "provisioningStarted": True,
                                  "entries": []})
        with patch.object(core, "wait_ready", return_value=False), \
             patch.object(core, "rollback") as rollback:
            failed = core.recover(self.env, OPERATION)
        self.assertEqual(failed["resultCode"], "provisioning_interrupted")
        rollback.assert_not_called()
        with patch.object(core, "wait_ready", return_value=True), \
             patch.object(core, "rollback") as rollback:
            ready = core.recover(self.env, OPERATION)
        self.assertEqual(ready["status"], "applied")
        rollback.assert_not_called()

    def test_committed_legacy_recovery_keeps_runtime_rollback(self):
        journal = core.journal_path(self.env, OPERATION)
        journal.parent.mkdir(parents=True)
        core.write_json(core.manifest_path(self.env), self.previous)
        core.write_json(journal.parent / "manifest.new.json", self.manifest)
        core.write_json(journal, {"schema": 1, "stage": "committed", "entries": []})
        with patch.object(core, "rollback", return_value={"status": "rolled_back"}) as rollback:
            result = core.recover(self.env, OPERATION)
        self.assertEqual(result["status"], "rolled_back")
        rollback.assert_called_once()


    def seed_pid_calibration(self, extruder_already_saved=False):
        extruder = b"[extruder]\ncontrol: pid\npid_Kp: 11.818\npid_Ki: 0.435\npid_Kd: 80.218\n"
        bed = b"[heater_bed]\ncontrol: pid\npid_Kp: 62.937\npid_Ki: 1.165\npid_Kd: 849.647\n"
        def commented(block):
            return block.replace(b"control:", b"#control:").replace(b"pid_", b"#pid_")
        saved_extruder = b"#*# [extruder]\n#*# control = pid\n#*# pid_kp = 12.0\n#*# pid_ki = 0.5\n#*# pid_kd = 81.0\n"
        saved_bed = b"#*# [heater_bed]\n#*# control = pid\n#*# pid_kp = 63.0\n#*# pid_ki = 1.2\n#*# pid_kd = 850.0\n"
        include = b"[include profiles/treed_v2_corexy_v1/macros.cfg]\n\n"
        before = include + (commented(extruder) if extruder_already_saved else extruder) + bed + b"\n" + core.MARKER + b"\n"
        if extruder_already_saved:
            before += saved_extruder
        calibrated = include + commented(extruder) + commented(bed) + b"\n" + core.MARKER + b"\n" + saved_extruder + saved_bed
        self.old["config/printer.cfg"] = calibrated
        (self.locations["config"] / "printer.cfg").write_bytes(calibrated)
        self.payload["config/printer.cfg"] = include + extruder.replace(b"11.818", b"20.0") + bed.replace(b"62.937", b"70.0")
        for row in self.previous["files"]:
            if row["path"] == "config/printer.cfg":
                row["runtimeSha256"] = core.content_hash(row["path"], before)
        core.write_json(core.manifest_path(self.env), self.previous)
        return before, calibrated

    def test_pid_save_config_is_not_treated_as_manual_edits_and_calibration_wins(self):
        _before, calibrated = self.seed_pid_calibration()
        self.assertEqual(self.apply()["status"], "applied")
        installed = (self.locations["config"] / "printer.cfg").read_bytes()
        body, saved = installed.split(core.MARKER, 1)
        self.assertEqual(saved, calibrated.split(core.MARKER, 1)[1])
        settings = configparser.RawConfigParser()
        settings.read_string(body.decode())
        self.assertFalse(settings.has_option("extruder", "control"))
        self.assertFalse(settings.has_option("heater_bed", "pid_kp"))
        settings.read_string("\n".join(line[4:] for line in saved.decode().splitlines() if line.startswith("#*# ")))
        self.assertEqual(settings.getfloat("extruder", "pid_kp"), 12.0)
        self.assertEqual(settings.getfloat("heater_bed", "pid_kp"), 63.0)
        self.assertIn(b"#pid_Kp: 20.0", body)
        self.assertIn(b"#pid_Kp: 70.0", body)

    def test_new_bed_calibration_is_compatible_with_manifest_after_extruder_calibration(self):
        self.seed_pid_calibration(extruder_already_saved=True)
        self.assertEqual(self.apply()["status"], "applied")

    def test_pid_save_config_just_before_service_stop_is_preserved(self):
        before, calibrated = self.seed_pid_calibration()
        path = self.locations["config"] / "printer.cfg"
        path.write_bytes(before)
        def stop(action):
            if action == "stop":
                path.write_bytes(calibrated)
        self.services.side_effect = stop
        self.assertEqual(self.apply()["status"], "applied")
        self.assertEqual(path.read_bytes().split(core.MARKER, 1)[1], calibrated.split(core.MARKER, 1)[1])

    def test_manual_printer_edits_remain_rejected_after_pid_save_config(self):
        self.seed_pid_calibration()
        path = self.locations["config"] / "printer.cfg"
        path.write_bytes(path.read_bytes().replace(b"macros.cfg", b"changed.cfg"))
        with self.assertRaisesRegex(ValueError, "Локально изменён"):
            self.apply()
        self.services.assert_not_called()

    def test_commented_pid_values_cannot_hide_manual_changes(self):
        self.seed_pid_calibration()
        path = self.locations["config"] / "printer.cfg"
        path.write_bytes(path.read_bytes().replace(b"#pid_Kp: 11.818", b"#pid_Kp: 99.0"))
        with self.assertRaisesRegex(ValueError, "Локально изменён"):
            self.apply()
        self.services.assert_not_called()

    def test_pid_comments_without_matching_autosave_fields_remain_rejected(self):
        _before, calibrated = self.seed_pid_calibration()
        path = self.locations["config"] / "printer.cfg"
        path.write_bytes(calibrated.split(core.MARKER, 1)[0] + core.MARKER + b"\n#*# [input_shaper]\n#*# shaper_freq_x = 71\n")
        with self.assertRaisesRegex(ValueError, "Локально изменён"):
            self.apply()
        self.services.assert_not_called()

    def test_baseline_accepts_stock_pid_comments_and_saved_calibration(self):
        before, _calibrated = self.seed_pid_calibration()
        source = self.root / "source-printer.cfg"
        source.write_bytes(before.split(core.MARKER, 1)[0])
        with patch.object(core, "package_manifest", return_value=dict(self.manifest, files=[])), \
             patch.object(core, "sources", return_value={"config/printer.cfg": source}):
            core.baseline(self.root, self.env)
        self.assertEqual(core.read_json(core.manifest_path(self.env))["version"], "0.2.0")

    def test_heater_shutdown_failure_does_not_stop_services_or_replace_files(self):
        self.stop_heating.side_effect = ValueError("heater still active")
        with self.assertRaisesRegex(ValueError, "heater still active"):
            self.apply()
        self.assertEqual(self.admission.call_args_list, [call("BEGIN"), call("END")])
        self.services.assert_not_called()
        self.assert_restored()

    def test_update_gate_precedes_heater_shutdown_and_service_stop(self):
        events = []
        self.admission.side_effect = lambda action: events.append(action)
        self.stop_heating.side_effect = lambda: events.append("heaters")
        self.services.side_effect = lambda action: events.append(action)

        self.assertEqual(self.apply()["status"], "applied")
        self.assertEqual(events[:3], ["BEGIN", "heaters", "stop"])
        self.assertEqual(events[-1], "start")

    def test_pre_gate_updater_installs_gate_and_next_update_uses_it(self):
        # Закреплённая версия до admission: переход выполняет старый процесс.
        self.assert_upgrade_from("0f7d77108cf57730e2bc0cfffc6d52e4fd9c654d", has_gate=False)

    def test_previous_updater_installs_new_package_and_can_update_again(self):
        self.assert_upgrade_from("4c3e11d864084f810001137b4d98cc97825167c9", has_gate=True)

    def test_v033_updater_installs_new_package_and_can_update_again(self):
        self.assert_upgrade_from("f1472b6fbb0c6ac2ae261d1cdc1356bfba677cf0", has_gate=True)

    def test_v034_updater_installs_new_package_and_can_update_again(self):
        self.assert_upgrade_from("de76ff54c3a66db8a860394b8e6b44e250338e32", has_gate=True)

    def assert_upgrade_from(self, revision, has_gate):
        # CI работает без root; проверяем запрос владельца, сохраняя реальные записи/fsync/rename.
        chown = self.stack.enter_context(patch.object(core.os, "fchown", create=True))
        try:
            source = subprocess.check_output([
                "git", "-C", str(ROOT), "show",
                f"{revision}:runtime-scripts/treed-update/treed-core-update"],
                stderr=subprocess.PIPE, timeout=10)
        except subprocess.CalledProcessError:
            self.skipTest("Для upgrade-проверки нужна история Git с " + revision)
        legacy = types.ModuleType("pre_gate_updater")
        exec(compile(source, "pre_gate_updater", "exec"), legacy.__dict__)
        self.assertEqual(has_gate, hasattr(legacy, "update_admission"))
        if has_gate:
            self.stack.enter_context(patch.object(legacy, "update_admission", self.admission))
            self.stack.enter_context(patch.object(legacy, "services_inactive", self.services_inactive))
        gate_key = "config/profiles/treed_v2_corexy_v1/macros_core.cfg"
        account_key = "config/moonraker/base/00-core.conf"
        account = b"[treed_autoprinter]\ncredentials_path: /private/device.json\nenabled: true\n"
        path = core.destination(account_key, self.locations)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(account)
        self.previous["files"].append({"path": account_key, "runtimeSha256": core.content_hash(account_key, account)})
        core.write_json(core.manifest_path(self.env), self.previous)
        # Установленный профиль содержит SAVE_CONFIG в файле, который обновление заменяет.
        saved_macro_key = "config/profiles/treed_v2_corexy_v1/macros_core.cfg"
        saved_macro = subprocess.check_output(["git", "-C", str(ROOT), "show",
                                               f"{revision}:klipper/profiles/treed_v2_corexy_v1/macros_core.cfg"],
                                              timeout=10)
        saved_macro_path = core.destination(saved_macro_key, self.locations)
        saved_macro_path.parent.mkdir(parents=True, exist_ok=True)
        saved_macro_path.write_bytes(saved_macro)
        self.previous["files"].append({"path": saved_macro_key,
                                       "runtimeSha256": core.content_hash(saved_macro_key, saved_macro)})
        self.payload[account_key] = b"[treed_autoprinter]\ncredentials_path:\n"
        # Подлинные конфиги установленного выпуска; новый код не подменяет старый updater.
        for key in LIGHT_KEYS[1:]:
            relative = key.replace('config/', 'klipper/', 1)
            old_config = subprocess.check_output(['git', '-C', str(ROOT), 'show',
                                                  f'{revision}:{relative}'], timeout=10)
            self.old[key] = old_config
            destination = core.destination(key, self.locations)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(old_config)
            self.previous['files'].append({'path': key, 'runtimeSha256': core.content_hash(key, old_config)})
        variables = self.locations['config'] / 'treed_variables.cfg'
        variables.write_bytes(b'[Variables]\nlight_on_startup = 1\nlight_on_print_start = 0\n')
        saved_variables = variables.read_bytes()
        core.write_json(core.manifest_path(self.env), self.previous)
        # Реальные release archives проходят исторические download/package/extract пути.
        release_dir = self.root / "historical-release"
        release_dir.mkdir()
        package_archive = release_dir / core.ASSET
        source_archive = release_dir / core.SOURCE_ASSET
        core.build(ROOT, package_archive)
        build_source_with_worktree_files(ROOT, source_archive)
        target_manifest, target_payload = core_load_package(
            package_archive, "v" + (ROOT / "VERSION").read_text(encoding="utf-8").strip())
        self.payload[saved_macro_key] = target_payload[saved_macro_key]
        expected_plan = core.managed_runtime_plan(ROOT)
        self.assertEqual(target_manifest.get("provisioning", {}).get("managedRevision"),
                         expected_plan["revision"])
        package_data, source_data = package_archive.read_bytes(), source_archive.read_bytes()
        release_api = "https://api.github.com/repos/test/printer-core/releases"
        core_url = ("https://github.com/test/printer-core/releases/download/"
                    + target_manifest["tag"] + "/" + core.ASSET)
        source_url = ("https://github.com/test/printer-core/releases/download/"
                      + target_manifest["tag"] + "/" + core.SOURCE_ASSET)
        release = {"tag_name": target_manifest["tag"], "draft": False, "prerelease": False,
                   "assets": [
                       {"name": core.ASSET, "digest": "sha256:" + hashlib.sha256(package_data).hexdigest(),
                        "browser_download_url": core_url},
                       {"name": core.SOURCE_ASSET, "digest": "sha256:" + hashlib.sha256(source_data).hexdigest(),
                        "browser_download_url": source_url},
                   ]}

        def open_release(request, timeout=10):
            url = request.full_url if hasattr(request, "full_url") else request
            if str(url).startswith(release_api):
                return io.BytesIO(json.dumps(release).encode())
            if url == core_url:
                return io.BytesIO(package_data)
            if url == source_url:
                return io.BytesIO(source_data)
            raise AssertionError("Unexpected historical updater URL: " + str(url))

        # Моки ограничены системными, Git и hardware boundaries.
        for name in ("roots", "vision_units_installed", "phase", "services", "idle",
                     "stop_heating", "wait_ready", "owner"):
            self.stack.enter_context(patch.object(legacy, name, getattr(core, name)))
        self.stack.enter_context(patch.object(legacy.urllib.request, "urlopen", side_effect=open_release))
        upstream_pins = itertools.cycle([target_manifest["requires"]["klipper"],
                                         target_manifest["requires"]["moonraker"]])
        self.stack.enter_context(patch.object(
            legacy, "git_head", side_effect=lambda _path: next(upstream_pins)))
        if hasattr(legacy, "_upstream_dirty"):
            self.stack.enter_context(patch.object(legacy, "_upstream_dirty", return_value=False))
        else:
            self.stack.enter_context(patch.object(legacy, "check_compatibility", return_value=None))

        def run_verified_loader(source_path, child_env, log_path, timeout=1500):
            # Systemd and hardware effects are external; source validation and registry hashing remain real.
            plan = core.managed_runtime_plan(source_path)
            self.assertEqual(plan["revision"], expected_plan["revision"])
            self.assertEqual(plan["steps"], expected_plan["steps"])
            self.services("start")
            return 0

        self.stack.enter_context(patch.object(legacy, "run_managed_loader",
                                              side_effect=run_verified_loader))
        if os.name == "nt" and GIT_BASH.is_file():
            original_run = subprocess.run

            def run_with_git_bash(command, *args, **kwargs):
                if command and Path(command[0]).name.lower() == "bash" and command[1:2] == ["-n"]:
                    command = [str(GIT_BASH), *command[1:]]
                return original_run(command, *args, **kwargs)

            self.stack.enter_context(patch.object(subprocess, "run", side_effect=run_with_git_bash))

        def service(action):
            if action == "start":
                for key in (gate_key, "sbin/treed-core-update", *LIGHT_KEYS):
                    self.assertEqual(core.destination(key, self.locations).read_bytes(),
                                     target_payload[key])
        self.services.side_effect = service
        update_env = dict(self.env, TREED_CORE_RELEASE_API_URL=release_api)
        first_result = legacy.apply(target_manifest["tag"], OPERATION, update_env)
        self.assertEqual(first_result["status"], "applied", first_result)
        if os.name != "nt":
            chown.assert_any_call(ANY, 0, 0)
        chown.reset_mock()
        if has_gate:
            self.admission.assert_called_once_with("BEGIN")
        else:
            self.admission.assert_not_called()
        self.admission.reset_mock()
        self.services.assert_has_calls([call("stop"), call("start")])
        self.assertEqual(core.read_json(core.manifest_path(self.env))["version"], target_manifest["version"])
        self.assertIn(b"credentials_path: /private/device.json", path.read_bytes())

        self.services.reset_mock()
        self.manifest = dict(target_manifest, version="0.3.0", tag="v0.3.0")
        self.payload = target_payload
        next_update_pins = itertools.cycle([target_manifest["requires"]["klipper"],
                                            target_manifest["requires"]["moonraker"]])
        with patch.object(core, "check_upstream_clean"), patch.object(
                core, "git_head", side_effect=lambda _path: next(next_update_pins)):
            result = core.apply("v0.3.0", "22222222-2222-4222-8222-222222222222", self.env)
        self.assertEqual(result["status"], "applied")
        self.admission.assert_called_once_with("BEGIN")
        if os.name != "nt":
            chown.assert_any_call(ANY, 0, 0)
        self.services.assert_has_calls([call("stop"), call("start")])
        self.assertEqual(core.read_json(core.manifest_path(self.env))["version"], "0.3.0")
        self.assertNotIn(b"enabled", path.read_bytes())
        self.assertIn(b"credentials_path: /private/device.json", path.read_bytes())
        self.assertEqual(variables.read_bytes(), saved_variables)
        for key in LIGHT_KEYS:
            self.assertEqual(core.destination(key, self.locations).read_bytes(), self.payload[key])

    def test_admission_sends_the_private_begin_and_end_gcode(self):
        with patch.object(core, "request_json") as request:
            request.side_effect = [None,
                {"status": {"gcode_macro _TREED_UPDATE_ADMISSION_STATE": {"active": 1}}},
                None,
                {"status": {"gcode_macro _TREED_UPDATE_ADMISSION_STATE": {"active": 0}}},
            ]
            core_update_admission("BEGIN")
            core_update_admission("END")
        self.assertEqual(request.call_args_list, [
            call("/printer/gcode/script", {"script": "_TREED_UPDATE_GATE ACTION=BEGIN"}),
            call("/printer/objects/query?gcode_macro%20_TREED_UPDATE_ADMISSION_STATE"),
            call("/printer/gcode/script", {"script": "_TREED_UPDATE_GATE ACTION=END"}),
            call("/printer/objects/query?gcode_macro%20_TREED_UPDATE_ADMISSION_STATE"),
        ])

    def test_unknown_command_ack_without_gate_status_fails_closed(self):
        with patch.object(core, "request_json", side_effect=[
                "ok", {"status": {}}]) as request:
            with self.assertRaisesRegex(RuntimeError, "не подтвердил состояние admission gate"):
                core_update_admission("BEGIN")
        self.assertEqual(request.call_count, 2)

    def test_admission_rejects_a_status_value_that_does_not_match_the_action(self):
        cases = [("BEGIN", 0), ("END", 1), ("BEGIN", "1")]
        for action, active in cases:
            with self.subTest(action=action, active=active), \
                 patch.object(core, "request_json", side_effect=[None, {
                     "status": {"gcode_macro _TREED_UPDATE_ADMISSION_STATE": {"active": active}}
                 }]):
                with self.assertRaisesRegex(RuntimeError, "не подтвердил состояние admission gate"):
                    core_update_admission(action)

    def test_missing_update_gate_fails_before_heaters_or_service_stop(self):
        self.admission.side_effect = RuntimeError("unknown command _TREED_UPDATE_GATE")
        with self.assertRaisesRegex(RuntimeError, "unknown command"):
            self.apply()
        self.stop_heating.assert_not_called()
        self.services.assert_not_called()

    def test_validated_recovery_releases_gate_after_services_are_ready(self):
        events = []
        self.admission.side_effect = lambda action: events.append(action)
        self.phase.side_effect = lambda name, _message: (_ for _ in ()).throw(RuntimeError("phase failed")) \
            if name == "installing" else None
        result = self.apply()
        self.assertEqual(result["resultCode"], "core_not_installed")
        self.assertEqual(events, ["BEGIN", "END"])
        self.services.assert_called_once_with("start")

    def test_validated_recovery_does_not_report_completion_when_gate_release_fails(self):
        self.phase.side_effect = lambda name, _message: (_ for _ in ()).throw(RuntimeError("phase failed")) \
            if name == "installing" else None
        self.admission.side_effect = [None, RuntimeError("Klipper response lost")]
        with self.assertRaisesRegex(RuntimeError, "не смогла снять admission gate"):
            self.apply()
        self.services.assert_called_once_with("start")
        self.assertEqual(core.read_json(core.journal_path(self.env, OPERATION))["stage"], "validated")

    def prepared_install_transaction(self):
        journal = core.journal_path(self.env, OPERATION)
        core.prepare(self.manifest, self.payload, self.env, journal)
        transaction = core.read_json(journal)
        transaction["stage"] = "installing"
        core.write_json(journal, transaction)
        for key, data in self.payload.items():
            core.destination(key, self.locations).write_bytes(data)
        return journal, transaction

    def test_rollback_refuses_to_stop_running_services_without_gate(self):
        journal, transaction = self.prepared_install_transaction()
        self.admission.side_effect = RuntimeError("Klipper unavailable")
        self.services_inactive.return_value = False
        with self.assertRaisesRegex(RuntimeError, "не подтверждён как остановленный"):
            core.rollback(self.env, journal, transaction)
        self.services.assert_not_called()
        self.assertEqual(core.destination("klipper/treed_driver_mode.py", self.locations).read_bytes(),
                         self.payload["klipper/treed_driver_mode.py"])

    def test_rollback_can_recover_offline_when_systemd_confirms_both_services_stopped(self):
        journal, transaction = self.prepared_install_transaction()
        self.admission.side_effect = RuntimeError("Klipper unavailable")
        self.assertEqual(core.rollback(self.env, journal, transaction)["status"], "rolled_back")
        self.services.assert_has_calls([call("stop"), call("start")])
        self.assert_restored()

    def test_rollback_does_not_restore_files_if_service_stop_is_unconfirmed(self):
        journal, transaction = self.prepared_install_transaction()
        self.services_inactive.side_effect = [False]
        with self.assertRaisesRegex(RuntimeError, "не подтвердил остановку"):
            core.rollback(self.env, journal, transaction)
        self.services.assert_called_once_with("stop")
        self.assertEqual(core.destination("klipper/treed_driver_mode.py", self.locations).read_bytes(),
                         self.payload["klipper/treed_driver_mode.py"])

    def test_systemd_unknown_state_is_not_treated_as_offline(self):
        unknown = subprocess.CompletedProcess([], 0, stdout="deactivating\n", stderr="")
        with patch.object(core.subprocess, "run", return_value=unknown):
            self.assertFalse(core_services_inactive())

    # Блок 2: Успех и сохранность принадлежащего принтеру состояния.
    def test_selected_config_reset_uses_package_and_retains_manual_backup(self):
        key = "config/profiles/treed_v2_corexy_v1/macros.cfg"
        path = core.destination(key, self.locations)
        manual = b"# custom fan settings\n"
        path.write_bytes(manual)
        selection = [{"path": key, "sha256": core.sha(manual)}]
        self.assertEqual(core.config_conflicts(self.env), selection)
        with self.assertRaisesRegex(ValueError, "Локально изменён"):
            self.apply()
        self.assertEqual(path.read_bytes(), manual)
        result = core.apply("v0.2.0", OPERATION, self.env, selection)
        self.assertEqual(result["status"], "applied")
        self.assertEqual(path.read_bytes(), self.payload[key])
        journal = core.journal_path(self.env, OPERATION)
        row = next(row for row in core.read_json(journal)["entries"] if row["path"] == key)
        self.assertEqual((journal.parent / row["backup"]).read_bytes(), manual)

    def test_selected_runtime_reset_replaces_all_managed_groups_and_retains_backups(self):
        additions = {"camera/stream_detect.py": b"old camera\n",
                     "sbin/treed-core-update": b"old updater\n",
                     "config/moonraker/base/00-core.conf": b"old moonraker config\n"}
        for key, data in additions.items():
            path = core.destination(key, self.locations)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            self.previous["files"].append({"path": key, "runtimeSha256": core.content_hash(key, data)})
            self.payload[key] = b"# new runtime\n"
        core.write_json(core.manifest_path(self.env), self.previous)
        keys = ["klipper/treed_driver_mode.py", "moonraker/treed_update.py", *additions]
        manual = b"manual runtime change\n"
        for key in keys:
            core.destination(key, self.locations).write_bytes(manual)
        selection = core.config_conflicts(self.env)
        self.assertEqual({row["path"] for row in selection}, set(keys))
        with self.assertRaisesRegex(ValueError, "Локально изменён"):
            self.apply()
        camera = core.destination("camera/stream_detect.py", self.locations)
        camera.write_bytes(manual + b"# newer edit\n")
        with self.assertRaisesRegex(ValueError, "Файл изменился после выбора сброса"):
            core.apply("v0.2.0", OPERATION, self.env, selection)
        self.assertEqual(camera.read_bytes(), manual + b"# newer edit\n")
        camera.write_bytes(manual)
        with patch.object(core.os, "fchown", create=True) as chown:
            result = core.apply("v0.2.0", OPERATION, self.env, selection)
        if os.name != "nt":
            chown.assert_any_call(ANY, 0, 0)
        self.assertEqual(result["status"], "applied")
        journal = core.journal_path(self.env, OPERATION)
        entries = {row["path"]: row for row in core.read_json(journal)["entries"]}
        for key in keys:
            self.assertEqual(core.destination(key, self.locations).read_bytes(), self.payload[key])
            self.assertEqual((journal.parent / entries[key]["backup"]).read_bytes(), manual)

    def test_selected_removed_managed_file_is_deleted_with_backup(self):
        key = "klipper/treed_driver_mode.py"
        manual = b"manual runtime change\n"
        core.destination(key, self.locations).write_bytes(manual)
        del self.payload[key]
        result = core.apply("v0.2.0", OPERATION, self.env, core.config_conflicts(self.env))
        self.assertEqual(result["status"], "applied")
        self.assertFalse(core.destination(key, self.locations).exists())
        journal = core.journal_path(self.env, OPERATION)
        row = next(row for row in core.read_json(journal)["entries"] if row["path"] == key)
        self.assertEqual((journal.parent / row["backup"]).read_bytes(), manual)

    def test_reset_consent_does_not_cover_subsequent_edit_or_unmanaged_files(self):
        for key in ("config/printer.cfg", "klipper/treed_driver_mode.py", "config/local_overrides.cfg", "config/../printer.cfg"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                core.prepare(self.manifest, self.payload, self.env, core.journal_path(self.env, OPERATION),
                             [{"path": key, "sha256": "0" * 64}])
        self.assert_restored()
        self.services.assert_not_called()

    def test_edit_during_service_stop_is_not_overwritten_by_reset_or_rollback(self):
        key = "config/printer.cfg"
        path = core.destination(key, self.locations)
        manual = b"# manual\n" + self.old[key]
        path.write_bytes(manual)
        selection = core.config_conflicts(self.env)
        newer = manual + b"# newer edit\n"
        self.services.side_effect = lambda action: path.write_bytes(newer) if action == "stop" else None
        result = core.apply("v0.2.0", OPERATION, self.env, selection)
        self.assertEqual(result["status"], "error")
        self.assertEqual(path.read_bytes(), newer)
        self.assertEqual(core.recover(self.env, OPERATION)["status"], "error")
        self.assertEqual(path.read_bytes(), newer)

    def test_selected_printer_reset_preserves_calibration_and_rolls_back_manual_file(self):
        key = "config/printer.cfg"
        manual = b"# manual\n" + self.old[key]
        core.destination(key, self.locations).write_bytes(manual)
        self.old[key] = manual
        def start(action):
            if action == "start":
                self.assertIn(core.MARKER + manual.split(core.MARKER, 1)[1], core.destination(key, self.locations).read_bytes())
        self.services.side_effect = start
        self.wait.side_effect = [False, True]
        result = core.apply("v0.2.0", OPERATION, self.env, core.config_conflicts(self.env))
        self.assertEqual(result["status"], "rolled_back")
        self.assert_restored()

    def test_apply_preserves_save_config_overrides_generated_data_and_unknown_files(self):
        preserved = {"local_overrides.cfg": b"custom settings\n", "treed_variables.cfg": b"light = True\n",
                     "filament_motion_runtime.cfg": b"detection_length: 17\n",
                     "moonraker/generated/50-webcam-treed.conf": b"webcam settings\n",
                     "my_custom.cfg": b"user-owned\n"}
        for relative, data in preserved.items():
            path = self.locations["config"] / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.assertEqual(self.apply()["status"], "applied")
        printer = (self.locations["config"] / "printer.cfg").read_bytes()
        self.assertIn(b"# new release", printer)
        self.assertIn(core.MARKER + self.old["config/printer.cfg"].split(core.MARKER, 1)[1], printer)
        for relative, data in preserved.items():
            self.assertEqual((self.locations["config"] / relative).read_bytes(), data)
        installed = core.read_json(core.manifest_path(self.env))
        self.assertEqual(installed["version"], "0.2.0")
        self.assertEqual(core.read_json(core.journal_path(self.env, OPERATION))["stage"], "committed")

    def test_save_config_just_before_service_stop_is_preserved(self):
        path = self.locations["config"] / "printer.cfg"
        def stop(action):
            if action == "stop":
                path.write_bytes(self.old["config/printer.cfg"] + b"#*# shaper_freq_y = 55\n")
        self.services.side_effect = stop
        self.assertEqual(self.apply()["status"], "applied")
        self.assertIn(b"shaper_freq_y = 55", path.read_bytes())

    def test_baseline_cannot_confirm_undelivered_checkout_files(self):
        source = self.root / "source-driver.py"
        source.write_bytes(self.payload["klipper/treed_driver_mode.py"])
        with patch.object(core, "package_manifest", return_value=dict(self.manifest, files=[])), \
             patch.object(core, "sources", return_value={"klipper/treed_driver_mode.py": source}):
            with self.assertRaisesRegex(ValueError, "не соответствует"):
                core.baseline(self.root, self.env)
        self.assertEqual(core.read_json(core.manifest_path(self.env))["version"], "0.1.0")

    def test_baseline_confirms_delivered_source_with_device_save_config(self):
        source = self.root / "source-printer.cfg"
        source.write_bytes(self.old["config/printer.cfg"].split(core.MARKER, 1)[0].rstrip(b"\n"))
        with patch.object(core, "package_manifest", return_value=dict(self.manifest, files=[])), \
             patch.object(core, "sources", return_value={"config/printer.cfg": source}):
            core.baseline(self.root, self.env)
        self.assertEqual(core.read_json(core.manifest_path(self.env))["version"], "0.2.0")

    def test_loader_baseline_uses_release_tag_of_its_checkout(self):
        repo = self.root / "loader-source"
        repo.mkdir()
        (repo / "VERSION").write_text("0.2.0\n", encoding="utf-8")
        (repo / "runtime-versions.env").write_text(
            'TREED_KLIPPER_REF="' + "a" * 40 + '"\nTREED_MOONRAKER_REF="' + "b" * 40 + '"\n',
            encoding="utf-8",
        )
        registry = json.loads((ROOT / core.MANAGED_RUNTIME).read_text(encoding="utf-8"))
        for relative in [core.MANAGED_RUNTIME, *registry["inputs"], *(
                f"loader/steps/{name}.sh" for name in registry["steps"])]:
            if relative == "runtime-versions.env":
                continue
            target = repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
        source = repo / "source-driver.py"
        source.write_bytes(self.old["klipper/treed_driver_mode.py"])
        def git(*args):
            return subprocess.check_output(
                ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
                 "-C", str(repo), *args], text=True, stderr=subprocess.DEVNULL).strip()
        git("init")
        git("add", ".")
        git("commit", "-m", "bootstrap")
        git("tag", "v9.0.0")
        git("commit", "--allow-empty", "-m", "release")
        with patch.object(core, "sources", return_value={"klipper/treed_driver_mode.py": source}):
            core.baseline(repo, self.env)
            self.assertEqual(core.read_json(core.manifest_path(self.env))["version"], "0.2.0")
            git("tag", "v0.35.1")
            git("tag", "v0.35.2")
            core.baseline(repo, self.env)
        recorded = core.read_json(core.manifest_path(self.env))
        self.assertEqual((recorded["version"], recorded["tag"], recorded["sha"]),
                         ("0.35.2", "v0.35.2", git("rev-parse", "HEAD")))
        self.assertEqual(core.package_manifest(repo)["version"], "0.2.0")
        self.assertEqual(core.config_conflicts(self.env), [])

    def test_only_previous_manifest_owned_files_can_be_removed(self):
        self.payload.pop("config/profiles/treed_v2_corexy_v1/macros.cfg")
        unrelated = self.locations["config"] / "profiles/treed_v2_corexy_v1/user.cfg"
        unrelated.write_text("keep", encoding="utf-8")
        self.assertEqual(self.apply()["status"], "applied")
        self.assertFalse(core.destination("config/profiles/treed_v2_corexy_v1/macros.cfg", self.locations).exists())
        self.assertEqual(unrelated.read_text(), "keep")

    # Блок 3: Отказ до мутаций, rollback и повторное recovery после обрыва.
    def test_local_managed_edits_are_rejected_before_stopping_services(self):
        core.destination("klipper/treed_driver_mode.py", self.locations).write_bytes(b"local edits\n")
        with self.assertRaisesRegex(ValueError, "Локально изменён"):
            self.apply()
        self.services.assert_not_called()
        self.assertEqual(core.read_json(core.manifest_path(self.env))["version"], "0.1.0")

    def test_incompatible_stack_is_rejected_before_backup_and_service_stop(self):
        self.compatibility.side_effect = ValueError("different Klipper SHA")
        with self.assertRaisesRegex(ValueError, "different Klipper"):
            self.apply()
        self.services.assert_not_called()
        self.assert_restored()
        self.assertFalse(core.journal_path(self.env, OPERATION).exists())

    def test_missing_loader_vision_units_fails_before_mutation(self):
        self.vision_units.return_value = False
        with self.assertRaisesRegex(ValueError, "сначала запустите loader"):
            self.apply()
        self.services.assert_not_called()
        self.stop_heating.assert_not_called()
        self.assertEqual(core.read_json(core.manifest_path(self.env))["version"], "0.1.0")
        self.assertFalse(core.journal_path(self.env, OPERATION).exists())

    def test_vision_service_and_manifest_are_in_runtime_bundle_not_model_weights(self):
        items = core.sources(ROOT)
        self.assertEqual(items["vision/service.py"], ROOT / "runtime-scripts/treed-vision/service.py")
        self.assertEqual(items["vision/models.json"], ROOT / "runtime-scripts/treed-vision/models.json")
        self.assertIn("moonraker/treed_vision_client.py", core.AUTOPRINTER_MODULES)
        self.assertNotIn("vision/fdm-detail-e0-20261016-8de7e8aa22e2.pth", items)

    def test_readiness_failure_restores_every_file_and_confirmed_version(self):
        self.wait.side_effect = [False, True]
        self.assertEqual(self.apply()["status"], "rolled_back")
        self.assert_restored()

    def test_partial_install_failure_restores_before_images(self):
        original = core.write_file
        injected = False
        def write(path, data, *args, **kwargs):
            nonlocal injected
            if path == self.locations["moonraker"] / "treed_update.py" and not injected:
                injected = True
                raise OSError("disk write failed")
            return original(path, data, *args, **kwargs)
        with patch.object(core, "write_file", side_effect=write):
            self.assertEqual(self.apply()["status"], "rolled_back")
        self.assert_restored()

    def test_phase_notification_failure_does_not_block_file_rollback(self):
        def notify(name, message):
            if name == "rolling_back":
                raise OSError("new service entrypoint unavailable")
        self.phase.side_effect = notify
        self.wait.side_effect = [False, True]
        self.assertEqual(self.apply()["status"], "rolled_back")
        self.assert_restored()

    def test_unknown_new_destination_is_not_overwritten(self):
        self.payload["klipper/treed_new_feature.py"] = b"version = 2\n"
        path = self.locations["klipper"] / "treed_new_feature.py"
        path.write_bytes(b"existing user file\n")
        with self.assertRaisesRegex(ValueError, "занято неизвестным"):
            self.apply()
        self.assertEqual(path.read_bytes(), b"existing user file\n")
        self.services.assert_not_called()

    def test_power_loss_after_partial_install_recovers_idempotently(self):
        variables = self.locations['config'] / 'treed_variables.cfg'
        saved = b'[Variables]\nlight_on_startup = 1\nlight_on_print_start = 0\n'
        variables.write_bytes(saved)
        original = core.write_file
        def write(path, data, *args, **kwargs):
            if path == self.locations["moonraker"] / "treed_update.py":
                raise KeyboardInterrupt("power loss")
            return original(path, data, *args, **kwargs)
        with patch.object(core, "write_file", side_effect=write):
            with self.assertRaises(KeyboardInterrupt):
                self.apply()
        self.assertEqual(core.recover(self.env, OPERATION)["status"], "rolled_back")
        self.assert_restored()
        self.services.reset_mock()
        self.assertEqual(core.recover(self.env, OPERATION)["status"], "rolled_back")
        self.services.assert_not_called()
        self.assertEqual(variables.read_bytes(), saved)
        self.assertEqual(core.apply('v0.2.0', '22222222-2222-4222-8222-222222222222', self.env)['status'], 'applied')
        for key in LIGHT_KEYS:
            self.assertEqual(core.destination(key, self.locations).read_bytes(), self.payload[key])
        self.assertEqual(variables.read_bytes(), saved)

    def test_committed_operation_recovers_as_applied(self):
        self.apply()
        self.services.reset_mock()
        self.assertEqual(core.recover(self.env, OPERATION)["status"], "applied")
        self.services.assert_not_called()

    def test_committed_journal_cannot_claim_a_different_installed_version(self):
        self.apply()
        core.write_json(core.manifest_path(self.env), self.previous)
        self.assertEqual(core.recover(self.env, OPERATION)["status"], "rolled_back")
        self.assert_restored()

    def test_bytecode_of_changed_modules_is_removed_before_restart(self):
        cache = self.locations["klipper"] / "__pycache__"
        cache.mkdir()
        changed = cache / "treed_driver_mode.cpython-311.pyc"
        other = cache / "unrelated.cpython-311.pyc"
        changed.write_bytes(b"new bytecode")
        other.write_bytes(b"keep")
        self.wait.side_effect = [False, True]
        self.assertEqual(self.apply()["status"], "rolled_back")
        self.assertFalse(changed.exists())
        self.assertEqual(other.read_bytes(), b"keep")

    def test_failed_rollback_readiness_does_not_claim_success(self):
        self.wait.return_value = False
        with self.assertRaisesRegex(RuntimeError, "Файлы восстановлены"):
            self.apply()

    def test_symlink_destination_cannot_overwrite_an_unrelated_file(self):
        unrelated = self.root / "outside.txt"
        unrelated.write_text("keep")
        destination = self.locations["klipper"] / "treed_driver_mode.py"
        destination.unlink()
        try:
            destination.symlink_to(unrelated)
        except OSError:
            self.skipTest("Создание symlink недоступно в этом Windows-сеансе")
        with self.assertRaises(ValueError):
            self.apply()
        self.assertEqual(unrelated.read_text(), "keep")
        self.services.assert_not_called()

    @unittest.skipIf(os.name == "nt", "Linux parent_fd contract")
    def test_missing_nested_managed_parent_is_absent_then_created_and_symlink_is_rejected(self):
        key = "config/moonraker/base/00-core.conf"
        self.payload[key] = b"[server]\n"
        path = core.destination(key, self.locations)
        self.assertFalse(path.parent.exists())
        self.assertEqual((None, None), core.read_file(path))
        self.assertEqual("applied", self.apply()["status"])
        self.assertEqual(b"[server]\n", path.read_bytes())

        linked_root = self.root / "config-link"
        linked_root.symlink_to(self.locations["config"], target_is_directory=True)
        with self.assertRaises(OSError):
            core.read_file(linked_root / "moonraker/base/00-core.conf")


class ManagedLoaderProcessTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "Real Bash process fixture runs on Linux")
    def test_repeated_loader_process_appends_to_existing_operation_log(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)
            (source / "loader").mkdir()
            (source / "loader/loader.sh").write_text("printf 'step completed\\n'\n", encoding="utf-8")
            log = source / "loader.log"
            for _ in range(2):
                self.assertEqual(0, core.run_managed_loader(source, dict(os.environ), log, timeout=5))
            self.assertEqual(b"step completed\nstep completed\n", log.read_bytes())

    def test_timeout_terminates_process_group_and_keeps_durable_log(self):
        class Process:
            pid = 4123
            returncode = -9

            def __init__(self):
                self.waits = 0

            def wait(self, timeout=None):
                self.waits += 1
                if self.waits == 1:
                    raise subprocess.TimeoutExpired("loader", timeout)
                return self.returncode

        process = Process()
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "loader.log"

            def spawn(*_args, **kwargs):
                kwargs["stdout"].write(b"loader started\n")
                return process

            with patch.object(core.subprocess, "Popen", side_effect=spawn) as popen, \
                 patch.object(core.os, "killpg", create=True) as killpg, \
                 patch.object(core.signal, "SIGKILL", 9, create=True), \
                 patch.object(core, "process_group_exists", side_effect=[True, True]), \
                 patch.object(core.time, "monotonic", side_effect=[0, 6]), \
                 patch.object(core.time, "sleep"):
                self.assertEqual(124, core.run_managed_loader(Path(temp), {}, log, timeout=1))
            self.assertEqual(b"loader started\n", log.read_bytes())
            if os.name != "nt":
                self.assertEqual(0o600, log.stat().st_mode & 0o777)
            popen.assert_called_once()
            self.assertTrue(popen.call_args.kwargs["start_new_session"])
            self.assertEqual([call(4123, core.signal.SIGTERM), call(4123, getattr(core.signal, "SIGKILL", 9))],
                             killpg.call_args_list)
            self.assertEqual(2, process.waits)


class PackageTests(unittest.TestCase):
    # Блок 4: Реальный release artifact; пути и checksum проверяются без extractall.
    def test_incomplete_autoprinter_archive_is_rejected_but_legacy_is_supported(self):
        base = {key: b"# fake runtime\n" for key in (
            "config/printer.cfg", "config/moonraker.conf", "config/moonraker/base/00-core.conf",
            "klipper/treed_driver_mode.py", "moonraker/treed_update.py", "sbin/treed-core-update",
        )}
        complete = {**base, "camera/stream_detect.py": b"# fake camera\n",
                    "vision/service.py": b"# fake service\n", "vision/models.json": b"{}",
                    **{key: b"# fake helper\n" for key in core.AUTOPRINTER_MODULES}}
        cases = [base, complete] + [{key: value for key, value in complete.items() if key != missing}
                                   for missing in sorted(core.AUTOPRINTER_MODULES)]
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / core.ASSET
            for index, payload in enumerate(cases):
                with self.subTest(case=index):
                    manifest = {"kind": core.KIND, "schema": 1, "version": "0.2.0", "tag": "v0.2.0",
                                "sha": "1" * 40, "requires": {}, "files": [
                                    {"path": key, "sha256": core.sha(data)} for key, data in payload.items()]}
                    with zipfile.ZipFile(archive, "w") as bundle:
                        bundle.writestr("manifest.json", json.dumps(manifest))
                        for key, data in payload.items():
                            bundle.writestr("payload/" + key, data)
                    if index < 2:
                        self.assertEqual(payload, core.load_package(archive, "v0.2.0")[1])
                    else:
                        with self.assertRaisesRegex(ValueError, "неполный AutoPrinter"):
                            core.load_package(archive, "v0.2.0")

    def test_delivered_autoprinter_imports_without_ml_or_neighbour_checkout(self):
        sources = core.sources(ROOT)
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp) / "delivered"
            package.mkdir()
            (package / "__init__.py").write_bytes(b"")
            for key in core.AUTOPRINTER_MODULES:
                (package / Path(key).name).write_bytes(sources[key].read_bytes())
            script = '''import importlib.abc, sys
class NoML(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.split(".")[0] in {"torch", "torchvision", "rfdetr", "PIL"}:
            raise AssertionError("heavy runtime imported during component load")
sys.meta_path.insert(0, NoML())
sys.path.insert(0, sys.argv[1])
from delivered import treed_autoprinter as component
class Server:
    def register_endpoint(self, *args, **kwargs): pass
    def register_event_handler(self, *args): pass
class Config:
    def get_server(self): return Server()
    def get(self, key, default=None): return default
instance = component.load_component(Config())
assert instance._store is None and instance._task is None
print("DELIVERED_IMPORT_OK")
'''
            result = subprocess.run([sys.executable, "-I", "-B", "-c", script, temp],
                                    cwd=temp, capture_output=True, text=True, timeout=15)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("DELIVERED_IMPORT_OK", result.stdout.strip())

    def test_repository_build_contains_only_runtime_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / core.ASSET
            core.build(ROOT, archive)
            manifest, payload = core.load_package(archive, "v" + (ROOT / "VERSION").read_text().strip())
        self.assertEqual(manifest["kind"], core.KIND)
        self.assertIn("sbin/treed-core-update", payload)
        self.assertIn("sbin/treed-loading-screen", payload)
        self.assertEqual(payload["sbin/treed-loading-screen"],
                         core.sources(ROOT)["sbin/treed-loading-screen"].read_bytes())
        self.assertIn("klipper/treed_driver_mode.py", payload)
        for key in LIGHT_KEYS:
            self.assertEqual(payload[key], core.sources(ROOT)[key].read_bytes())
        self.assertTrue({"vision/service.py", "vision/models.json", "camera/stream_detect.py",
                         "moonraker/treed_vision_client.py"} <= payload.keys())
        self.assertTrue(core.AUTOPRINTER_MODULES <= payload.keys())
        self.assertFalse(any(key.startswith("vision/") and key.endswith(".pth") for key in payload))
        self.assertFalse(any("local_overrides.cfg" in key for key in payload))
        self.assertFalse(any(key.startswith("loader/") for key in payload))
        self.assertEqual(manifest["provisioning"]["revision"], core.provisioning_revision(ROOT))
        self.assertEqual(manifest["provisioning"]["managedRevision"], core.managed_runtime_plan(ROOT)["revision"])
        self.assertEqual(manifest["provisioning"]["steps"], core.managed_runtime_plan(ROOT)["steps"])

    def test_source_asset_matches_target_and_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            archive = temp / core.SOURCE_ASSET
            build_source_with_worktree_files(ROOT, archive)
            with zipfile.ZipFile(archive) as bundle:
                files = {name: bundle.read(name) for name in bundle.namelist()}
            directories = set()
            for name in files:
                parent = PurePosixPath(name).parent
                while parent.as_posix() != ".":
                    directories.add(parent.as_posix() + "/")
                    parent = parent.parent
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
                for name in sorted(directories):
                    bundle.writestr(name, b"")
                for name, data in files.items():
                    bundle.writestr(name, data)
            target_archive = temp / core.ASSET
            core.build(ROOT, target_archive)
            manifest, payload = core.load_package(target_archive, "v" + (ROOT / "VERSION").read_text().strip())
            with zipfile.ZipFile(archive) as bundle:
                self.assertTrue(any(row.is_dir() for row in bundle.infolist()))
            core.extract_source(archive, temp / "source", manifest, payload)
            bad_manifest = json.loads(json.dumps(manifest))
            bad_manifest["provisioning"]["managedRevision"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "registry/hash"):
                core.extract_source(archive, temp / "bad-managed-hash", bad_manifest, payload)
            changed = dict(files)
            registry = json.loads(changed[core.MANAGED_RUNTIME])
            registry["inputs"][0] = "../../outside"
            changed[core.MANAGED_RUNTIME] = json.dumps(registry).encode()
            bad_registry = temp / "bad-registry.zip"
            with zipfile.ZipFile(bad_registry, "w", zipfile.ZIP_DEFLATED) as bundle:
                for name, data in changed.items():
                    bundle.writestr(name, data)
            with self.assertRaises(ValueError):
                core.extract_source(bad_registry, temp / "bad-registry", manifest, payload)
            with zipfile.ZipFile(archive, "a") as bundle:
                bundle.writestr("../escape", b"bad")
            with self.assertRaises(ValueError):
                core.extract_source(archive, temp / "bad-source", manifest, payload)

    def test_source_asset_runtime_mapping_must_match_verified_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            archive, runtime = temp / core.SOURCE_ASSET, temp / core.ASSET
            build_source_with_worktree_files(ROOT, archive); core.build(ROOT, runtime)
            manifest, payload = core.load_package(runtime, "v" + (ROOT / "VERSION").read_text().strip())
            with zipfile.ZipFile(archive) as bundle:
                files = {name: bundle.read(name) for name in bundle.namelist()}
            files["klipper-host/treed_driver_mode.py"] += b"# mismatch\n"
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
                for name, data in files.items():
                    bundle.writestr(name, data)
            with self.assertRaisesRegex(ValueError, "не совпадает с target payload"):
                core.extract_source(archive, temp / "source", manifest, payload)

    def test_managed_revision_hashes_only_the_registry_plan_and_its_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)
            plan = core.managed_runtime_plan(ROOT)
            registry = json.loads((ROOT / core.MANAGED_RUNTIME).read_text(encoding="utf-8"))
            for relative in [core.MANAGED_RUNTIME, *registry["inputs"], *(
                    f"loader/steps/{name}.sh" for name in registry["steps"]),
                    "loader/steps/boot-hdmi-config.sh"]:
                destination = source / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / relative, destination)
            before = core.managed_runtime_plan(source)["revision"]
            boot_step = source / "loader/steps/boot-hdmi-config.sh"
            boot_step.write_bytes(boot_step.read_bytes() + b"\n# boot-only edit\n")
            self.assertEqual(core.managed_runtime_plan(source)["revision"], before)
            timer_unit = source / "runtime-scripts/treed-update/treed-update-retry.timer"
            original_timer = timer_unit.read_bytes()
            timer_unit.write_bytes(original_timer + b"\n# retry policy edit\n")
            self.assertNotEqual(core.managed_runtime_plan(source)["revision"], before)
            timer_unit.write_bytes(original_timer)
            runtime_lib = source / "loader/lib/runtime-repo.sh"
            original_lib = runtime_lib.read_bytes()
            runtime_lib.write_bytes(original_lib + b"\n# managed input edit\n")
            self.assertNotEqual(core.managed_runtime_plan(source)["revision"], before)
            runtime_lib.write_bytes(original_lib)
            managed_step = source / "loader/steps/check-env.sh"
            managed_step.write_bytes(managed_step.read_bytes() + b"\n# managed edit\n")
            self.assertNotEqual(core.managed_runtime_plan(source)["revision"], before)

    def test_managed_metadata_rejects_extra_fields_and_traversal_steps(self):
        valid = {"schema": 1, "revision": "a" * 64, "managedRevision": "b" * 64,
                 "steps": ["check-env", "update-recovery-bootstrap", "maintenance-stop",
                           "maintenance-start", "verify"]}
        invalid = []
        invalid.append(dict(valid, extra="unexpected"))
        invalid.append(dict(valid, managedRevision="../escape"))
        invalid.append(dict(valid, steps=[*valid["steps"][:-1], "../escape"]))
        invalid.append({key: value for key, value in valid.items() if key != "steps"})
        for metadata in invalid:
            with self.subTest(metadata=metadata), self.assertRaises(ValueError):
                core.validate_managed_metadata(metadata)

    def test_legacy_provisioning_revision_algorithm_remains_compatible(self):
        source = subprocess.check_output(
            ["git", "-C", str(ROOT), "show", "HEAD:runtime-scripts/treed-update/treed-core-update"],
            timeout=10,
        )
        legacy = types.ModuleType("legacy_core_revision")
        exec(compile(source, "legacy_core_revision", "exec"), legacy.__dict__)
        self.assertEqual(core.provisioning_revision(ROOT), legacy.provisioning_revision(ROOT))

    def test_provisioning_route_uses_revision_upstream_refs_and_required_units(self):
        target = {"requires": {"klipper": "a" * 40, "moonraker": "b" * 40},
                  "provisioning": {"schema": 1, "revision": "c" * 64}}
        installed = {"kind": core.KIND, "schema": 1, "provisioning": target["provisioning"]}
        roots = {"klipper": Path("/home/pi/klipper/klippy/extras"),
                 "moonraker": Path("/home/pi/moonraker/moonraker/components")}
        with patch.object(core, "roots", return_value=roots), \
             patch.object(core, "vision_units_installed", return_value=True), \
             patch.object(core, "git_head", side_effect=["a" * 40, "b" * 40]):
            self.assertFalse(core.needs_provisioning(target, installed, {}))
        for mutate in (lambda target, baseline: baseline.update(provisioning={"schema": 1, "revision": "d" * 64}),
                       lambda target, baseline: target["requires"].update(klipper="e" * 40)):
            candidate, baseline = dict(target, requires=dict(target["requires"])), dict(installed)
            mutate(candidate, baseline)
            with patch.object(core, "roots", return_value=roots), \
                 patch.object(core, "vision_units_installed", return_value=True), \
                 patch.object(core, "git_head", side_effect=["a" * 40, "b" * 40]):
                self.assertTrue(core.needs_provisioning(candidate, baseline, {}))
        with patch.object(core, "roots", return_value=roots), \
             patch.object(core, "vision_units_installed", return_value=False):
            self.assertTrue(core.needs_provisioning(target, installed, {}))
        managed = dict(target, provisioning={"schema": 1, "revision": "c" * 64,
                                             "managedRevision": "d" * 64,
                                             "steps": ["check-env", "update-recovery-bootstrap", "maintenance-stop",
                                                       "maintenance-start", "verify"]})
        legacy_baseline = {"kind": core.KIND, "schema": 1,
                           "provisioning": {"schema": 1, "revision": "c" * 64}}
        with patch.object(core, "roots", return_value=roots), \
             patch.object(core, "vision_units_installed", return_value=True), \
             patch.object(core, "git_head", side_effect=["a" * 40, "b" * 40]):
            self.assertFalse(core.needs_provisioning(managed, legacy_baseline, {}))
        current = dict(legacy_baseline, provisioning=managed["provisioning"])
        changed = dict(managed, provisioning=dict(managed["provisioning"], managedRevision="e" * 64))
        with patch.object(core, "roots", return_value=roots), \
             patch.object(core, "vision_units_installed", return_value=True):
            self.assertTrue(core.needs_provisioning(changed, current, {}))

    def test_archive_corruption_extra_entries_and_duplicates_are_rejected(self):
        for kind in ("corruption", "extra", "duplicate", "system-bundle"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp:
                archive = Path(temp) / core.ASSET
                core.build(ROOT, archive)
                with zipfile.ZipFile(archive) as bundle:
                    content = {name: bundle.read(name) for name in bundle.namelist()}
                if kind == "corruption":
                    content["payload/klipper/treed_driver_mode.py"] = b"corrupted"
                elif kind == "extra":
                    content["../../etc/passwd"] = b"overwrite"
                elif kind == "system-bundle":
                    manifest = json.loads(content["manifest.json"])
                    manifest["kind"] = "system-bundle"
                    content["manifest.json"] = json.dumps(manifest).encode()
                with zipfile.ZipFile(archive, "w") as bundle:
                    for name, data in content.items():
                        bundle.writestr(name, data)
                    if kind == "duplicate":
                        import warnings
                        with warnings.catch_warnings():
                            warnings.simplefilter("ignore")
                            bundle.writestr("manifest.json", content["manifest.json"])
                with self.assertRaises(ValueError):
                    core.load_package(archive, "v" + (ROOT / "VERSION").read_text().strip())

    def test_paths_cannot_write_os_or_preserved_printer_data(self):
        for key in ("../etc/passwd", "sbin/ssh", "config/local_overrides.cfg", "config/treed_variables.cfg",
                    "config/moonraker/generated/webcam.conf", "config/profiles/treed_v2_corexy_v1/../user.cfg",
                    "config\\printer.cfg", "klipper/../../etc/shadow"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                core.allowed(key)

    def test_release_download_rejects_missing_and_mismatching_github_digest(self):
        for digest in (None, "sha256:" + "0" * 64):
            release = {"tag_name": "v0.2.0", "assets": [{"name": core.ASSET,
                       "digest": digest, "browser_download_url": "https://github.com/org/repo/releases/download/v0.2.0/core.zip"}]}
            responses = [io.BytesIO(json.dumps(release).encode()), io.BytesIO(b"wrong archive")]
            with self.subTest(digest=digest), tempfile.TemporaryDirectory() as temp, \
                 patch.object(core.urllib.request, "urlopen", side_effect=responses):
                with self.assertRaises(ValueError):
                    core.download("v0.2.0", Path(temp) / core.ASSET, {})

    def test_download_classifies_only_confirmed_transient_network_failures(self):
        errors = (
            (core.urllib.error.URLError(ConnectionError("connection reset")), True),
            (core.urllib.error.HTTPError("https://api.github.com", 408, "timeout", {}, None), True),
            (core.urllib.error.HTTPError("https://api.github.com", 429, "rate limit", {}, None), True),
            (core.urllib.error.HTTPError("https://api.github.com", 503, "unavailable", {}, None), True),
            (core.urllib.error.HTTPError("https://api.github.com", 404, "missing", {}, None), False),
            (ValueError("invalid response"), False),
        )
        for error, transient in errors:
            with self.subTest(error=type(error).__name__, transient=transient), tempfile.TemporaryDirectory() as temp, \
                 patch.object(core.urllib.request, "urlopen", side_effect=error):
                expected = core.TransientDownloadError if transient else type(error)
                with self.assertRaises(expected):
                    core.download("v0.2.0", Path(temp) / core.ASSET, {})
        self.assertEqual(core.update_error_result(core.TransientDownloadError("network"))["resultCode"],
                         "core_download_failed")
        self.assertEqual(core.update_error_result(core.PrinterNotReady("busy"))["resultCode"],
                         "core_not_ready")
        self.assertEqual(core.update_error_result(ValueError("bad sha"))["resultCode"],
                         "core_validation_failed")
        self.assertEqual(core.update_error_result(RuntimeError("service failed"))["resultCode"],
                         "core_update_failed")

    def test_render_retains_device_updater_wiring_and_templates(self):
        key = "config/moonraker/base/00-core.conf"
        old = b"[update_manager mainsail]\npath: /my/device/path\n\n[treed_update]\n"
        new = b"[update_manager mainsail]\npath: /default\n\n[update_manager crowsnest]\npath: {{PI_HOME}}/crowsnest\n\n[treed_update]\nrepo_path: {{PI_HOME}}/treed/printer-core\n"
        result = core.render(key, new, old, {"TREED_UPDATE_PI_HOME": "/home/radxa", "TREED_UPDATE_PI_USER": "radxa"})
        self.assertIn(b"path: /my/device/path", result)
        self.assertIn(b"repo_path: /home/radxa/treed/printer-core", result)
        self.assertNotIn(b"[update_manager crowsnest]", result)

    def test_render_preserves_local_autoprinter_identity_and_adds_new_defaults(self):
        key = "config/moonraker/base/00-core.conf"
        old = ("[treed_autoprinter]\nprinter_id: local-id\napi_origin: https://local.example\n"
               "enabled=true\nexecution_enabled: false\nbed_check_enabled = false\n"
               "credentials_path: /private/credentials.json\nstorage_path: /private/state.sqlite3\n").encode()
        new = ("[treed_autoprinter]\nprinter_id: new-default\napi_origin: https://default.example\n"
               "credentials_path: /new/default.json\nstorage_path: /new/state.sqlite3\n"
               "bed_check_socket: /home/radxa/treed/vision/run/vision.sock\n").encode()
        result = core.render(key, new, old, {"TREED_UPDATE_PI_HOME": "/home/radxa",
                                             "TREED_UPDATE_PI_USER": "radxa"}).decode()
        self.assertIn("printer_id: local-id", result)
        self.assertIn("api_origin: https://local.example", result)
        self.assertIn("credentials_path: /private/credentials.json", result)
        self.assertIn("storage_path: /private/state.sqlite3", result)
        self.assertIn("bed_check_socket: /home/radxa/treed/vision/run/vision.sock", result)
        self.assertNotIn("printer_id: new-default", result)
        self.assertNotIn("enabled", result)
        equals = core.render(key, new, old.replace(b"credentials_path:", b"credentials_path ="), {
            "TREED_UPDATE_PI_HOME": "/home/radxa", "TREED_UPDATE_PI_USER": "radxa"}).decode()
        self.assertEqual(1, equals.count("credentials_path"))
        self.assertIn("credentials_path = /private/credentials.json", equals)

    def test_compatibility_checks_both_pinned_upstreams_and_tracked_edits(self):
        manifest = {"requires": {"klipper": "a" * 40, "moonraker": "b" * 40}}
        env = {"TREED_UPDATE_PI_HOME": "/home/radxa"}
        locations = patch.object(core, "roots", return_value={
            "klipper": ROOT / "klippy/extras", "moonraker": ROOT / "moonraker/components"})
        locations.start()
        self.addCleanup(locations.stop)
        with patch.object(core, "git_head", side_effect=["a" * 40, "b" * 40]), \
             patch.object(core.subprocess, "check_output", return_value=""):
            core.check_compatibility(manifest, env)
        with patch.object(core, "git_head", return_value="c" * 40):
            with self.assertRaisesRegex(ValueError, "другую версию"):
                core.check_compatibility(manifest, env)
        with patch.object(core, "git_head", return_value="a" * 40), \
             patch.object(core.subprocess, "check_output", return_value=" M klippy.py"):
            with self.assertRaisesRegex(ValueError, "Изменены исходники"):
                core.check_compatibility(manifest, env)


# Блок 5: Реальный Git с checkout другого владельца, без глобального доверия.
class CoreGitOwnershipTests(unittest.TestCase):
    def test_only_hash_matching_manifest_managed_untracked_upstream_modules_are_allowed(self):
        with tempfile.TemporaryDirectory(prefix="treed managed upstream ") as temp, \
             patch.dict(os.environ, {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}):
            home = Path(temp)
            env = {"TREED_UPDATE_PI_HOME": str(home)}
            locations, requirements, previous_rows, target_rows = {}, {}, [], []
            for name, suffix in (("klipper", Path("klippy/extras")),
                                 ("moonraker", Path("moonraker/components"))):
                repo = home / name
                repo.mkdir()
                subprocess.run(["git", "init", "-q", str(repo)], check=True, timeout=10)
                (repo / "tracked.txt").write_text("original\n", encoding="utf-8")
                subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True, timeout=10)
                subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c",
                                "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
                                "commit", "-qm", "Initial"], check=True, timeout=10)
                requirements[name] = core.git_head(repo)
                locations[name] = repo / suffix
                locations[name].mkdir(parents=True)
                key = f"{name}/treed_calibration.py"
                data = f"# managed {name}\n".encode()
                (locations[name] / "treed_calibration.py").write_bytes(data)
                previous_rows.append({"path": key, "runtimeSha256": core.content_hash(key, data)})
                target_rows.append({"path": key, "sha256": core.sha(data)})

            previous = {"files": previous_rows}
            target = {"requires": requirements, "files": target_rows}
            with patch.object(core, "roots", return_value=locations):
                core.check_upstream_clean(locations, previous)
                core.check_compatibility(target, env, previous)
                unknown = locations["klipper"] / "locally_created.py"
                unknown.write_text("# not managed\n", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "Изменены исходники установленного klipper"):
                    core.check_upstream_clean(locations, previous)
                unknown.unlink()
                managed = locations["moonraker"] / "treed_calibration.py"
                managed.write_text("# locally modified\n", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "Изменены исходники установленного moonraker"):
                    core.check_compatibility(target, env, previous)
                klipper_module = locations["klipper"] / "treed_calibration.py"
                failed_target = b"# failed target\n"
                klipper_module.write_bytes(failed_target)
                managed.write_text("# locally modified but explicitly selected\n", encoding="utf-8")
                installation_base = {"files": [
                    {"path": "klipper/treed_calibration.py",
                     "runtimeSha256": core.content_hash("klipper/treed_calibration.py", failed_target)},
                    {"path": "moonraker/treed_calibration.py",
                     "runtimeSha256": target_rows[1]["sha256"]},
                ]}
                reset = [{"path": "moonraker/treed_calibration.py", "sha256": core.sha(
                    managed.read_bytes())}]
                core.check_upstream_clean(locations, previous, reset, installation_base)
                unknown.write_text("# not managed\n", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "Изменены исходники установленного klipper"):
                    core.check_upstream_clean(locations, previous, reset, installation_base)
                unknown.unlink()
                tracked = home / "klipper/tracked.txt"
                tracked.write_text("local edit\n", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "Изменены исходники установленного klipper"):
                    core.check_upstream_clean(locations, previous, reset, installation_base)

    def test_cross_owner_read_preserves_version_and_dirty_checks(self):
        with tempfile.TemporaryDirectory(prefix="treed git ownership ") as temp, \
             patch.dict(os.environ, {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}):
            home = Path(temp)
            env = {"TREED_UPDATE_PI_HOME": str(home)}
            requires = {}
            for name in ("klipper", "moonraker"):
                repo = home / name
                subprocess.run(["git", "init", "-q", str(repo)], check=True, timeout=10)
                (repo / "tracked.txt").write_text("original\n", encoding="utf-8")
                subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True, timeout=10)
                subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c",
                                "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
                                "commit", "-qm", "Initial"], check=True, timeout=10)
                requires[name] = core.git_head(repo)
            manifest = {"requires": requires}
            locations = {"klipper": home / "klipper/klippy/extras",
                         "moonraker": home / "moonraker/moonraker/components"}
            with patch.dict(os.environ, {"GIT_TEST_ASSUME_DIFFERENT_OWNER": "1"}), \
                 patch.object(core, "roots", return_value=locations):
                for name, expected in requires.items():
                    repo = home / name
                    rejected = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                                              capture_output=True, text=True, timeout=10)
                    self.assertEqual(rejected.returncode, 128)
                    self.assertIn("dubious ownership", rejected.stderr)
                    self.assertEqual(core.git_head(repo), expected)
                core.check_compatibility(manifest, env)
                incompatible = {"requires": dict(requires, klipper="0" * 40)}
                with self.assertRaisesRegex(ValueError, "другую версию klipper"):
                    core.check_compatibility(incompatible, env)
                for name in requires:
                    with self.subTest(name=name):
                        tracked = home / name / "tracked.txt"
                        tracked.write_text("local edit\n", encoding="utf-8")
                        with self.assertRaisesRegex(ValueError, f"Изменены исходники установленного {name}"):
                            core.check_compatibility(manifest, env)
                        tracked.write_text("original\n", encoding="utf-8")


class PrinterUpdateGuardTests(unittest.TestCase):
    def test_readiness_requires_candidate_autoprinter_and_rejects_failed_components(self):
        loaded = ["treed_update", "treed_recovery", "treed_detection", "treed_host_network",
                  "treed_filament_sensor", "treed_shell_command"]
        legacy = {"klippy_state": "ready", "components": loaded, "failed_components": []}
        candidate = {"files": [{"path": "moonraker/treed_autoprinter.py"},
                               {"path": "moonraker/treed_vision_client.py"}]}
        for info, manifest, expected in (
            (legacy, {"files": []}, True),
            (legacy, candidate, False),
            (legacy | {"components": loaded + ["treed_vision_client"]}, candidate, False),
            (legacy | {"components": loaded + ["treed_autoprinter"]}, candidate, True),
            (legacy | {"components": loaded + ["treed_autoprinter"],
                       "failed_components": ["treed_vision_client"]}, candidate, True),
            (legacy | {"components": loaded + ["treed_autoprinter"],
                       "failed_components": ["treed_autoprinter"]}, candidate, False),
            (legacy | {"failed_components": ["treed_update"]}, {"files": []}, False),
            (legacy | {"components": "treed_update"}, candidate, False),
            (legacy | {"failed_components": None}, candidate, False),
        ):
            with self.subTest(info=info, candidate=bool(manifest["files"])), \
                    patch.object(core, "request_json", return_value=info), \
                    patch.object(core.time, "sleep"), \
                    patch.object(core.time, "monotonic", side_effect=[0, 1, 2, 3, 61]):
                self.assertEqual(expected, core.wait_ready(manifest))

    def test_wait_ready_uses_registered_component_gate_for_candidate_and_rollback(self):
        candidate = {"files": [{"path": "moonraker/treed_autoprinter.py"},
                               {"path": "moonraker/treed_vision_client.py"}]}
        previous = {"files": [{"path": "moonraker/treed_autoprinter.py"}]}
        with patch.object(core, "ready", return_value=True) as ready, \
             patch.object(core.time, "sleep"):
            self.assertTrue(core.wait_ready(candidate))
            self.assertTrue(core.wait_ready(previous))
        self.assertEqual([("treed_autoprinter",)] * 6,
                         [call.args[0] for call in ready.call_args_list])

    def test_cancelled_job_with_stale_pause_and_heat_is_idle(self):
        status = {"print_stats": {"state": " CANCELLED "}, "pause_resume": {"is_paused": True},
                  "extruder": {"target": 140}, "heater_bed": {"target": 60}}
        with patch.object(core, "ready", return_value=True), \
             patch.object(core, "request_json", return_value={"status": status}):
            self.assertTrue(core.idle())

    def test_paused_print_and_unknown_state_are_not_idle(self):
        for state in ("printing", "paused", "unknown", None, ""):
            with self.subTest(state=state), patch.object(core, "ready", return_value=True), \
                 patch.object(core, "request_json", return_value={"status": {"print_stats": {"state": state}}}):
                self.assertFalse(core.idle())

    def test_shutdown_heaters_requires_zero_targets_and_inactive_job(self):
        for state, nozzle, bed in (("cancelled", 0, 0), ("cancelled", 140, 0),
                                   ("cancelled", 0, 60), ("printing", 0, 0), ("paused", 0, 0)):
            status = {"print_stats": {"state": state}, "extruder": {"target": nozzle}, "heater_bed": {"target": bed}}
            with self.subTest(state=state, nozzle=nozzle, bed=bed), \
                 patch.object(core, "request_json", side_effect=["ok", {"status": status}]) as request:
                if state == "cancelled" and nozzle == bed == 0:
                    core.stop_heating()
                else:
                    with self.assertRaises(ValueError):
                        core.stop_heating()
                self.assertEqual(request.call_args_list[0].args, ("/printer/gcode/script", {"script": "TURN_OFF_HEATERS"}))


if __name__ == "__main__":
    unittest.main()
