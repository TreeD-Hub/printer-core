"""Назначение: адресные проверки пакета, сохранности данных и journal recovery.
Контур: временные файлы и mock services/network; без принтера и systemd.
"""

# Блок 1: Реальный installer в изолированном runtime.
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "runtime-scripts/treed-update/treed-core-update"
core = types.ModuleType("treed_core_update")
exec(compile(PATH.read_text(encoding="utf-8"), str(PATH), "exec"), core.__dict__)
OPERATION = "11111111-1111-4111-8111-111111111111"


class CoreUpdateTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.env = {"TREED_UPDATE_PI_HOME": str(self.root / "home"),
                    "TREED_UPDATE_PI_USER": "radxa", "TREED_UPDATE_PI_GROUP": "radxa",
                    "TREED_CORE_MANIFEST_FILE": str(self.root / "state/core-manifest.json"),
                    "TREED_CORE_STATE_DIR": str(self.root / "state/core")}
        self.locations = {name: self.root / name for name in ("config", "klipper", "moonraker", "camera", "sbin")}
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
        self.compatibility = self.stack.enter_context(patch.object(core, "check_compatibility"))
        self.phase = self.stack.enter_context(patch.object(core, "phase"))
        self.services = self.stack.enter_context(patch.object(core, "services"))
        self.stack.enter_context(patch.object(core, "idle", return_value=True))
        self.wait = self.stack.enter_context(patch.object(core, "wait_ready", return_value=True))
        self.stack.enter_context(patch.object(core, "owner", return_value=(
            getattr(os, "getuid", lambda: 0)(), getattr(os, "getgid", lambda: 0)())))

    def apply(self):
        return core.apply("v0.2.0", OPERATION, self.env)

    def assert_restored(self):
        for key, data in self.old.items():
            self.assertEqual(core.destination(key, self.locations).read_bytes(), data, key)
        self.assertEqual(core.read_json(core.manifest_path(self.env))["version"], "0.1.0")

    # Блок 2: Успех и сохранность принадлежащего принтеру состояния.
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


class PackageTests(unittest.TestCase):
    # Блок 4: Реальный release artifact; пути и checksum проверяются без extractall.
    def test_repository_build_contains_only_runtime_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / core.ASSET
            core.build(ROOT, archive)
            manifest, payload = core.load_package(archive, "v" + (ROOT / "VERSION").read_text().strip())
        self.assertEqual(manifest["kind"], core.KIND)
        self.assertIn("sbin/treed-core-update", payload)
        self.assertIn("klipper/treed_driver_mode.py", payload)
        self.assertFalse(any("local_overrides.cfg" in key for key in payload))
        self.assertFalse(any(key.startswith("loader/") for key in payload))

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

    def test_render_retains_device_updater_wiring_and_templates(self):
        key = "config/moonraker/base/00-core.conf"
        old = b"[update_manager mainsail]\npath: /my/device/path\n\n[treed_update]\n"
        new = b"[update_manager mainsail]\npath: /default\n\n[update_manager crowsnest]\npath: {{PI_HOME}}/crowsnest\n\n[treed_update]\nrepo_path: {{PI_HOME}}/treed/printer-core\n"
        result = core.render(key, new, old, {"TREED_UPDATE_PI_HOME": "/home/radxa", "TREED_UPDATE_PI_USER": "radxa"})
        self.assertIn(b"path: /my/device/path", result)
        self.assertIn(b"repo_path: /home/radxa/treed/printer-core", result)
        self.assertNotIn(b"[update_manager crowsnest]", result)

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


if __name__ == "__main__":
    unittest.main()
