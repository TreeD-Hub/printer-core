"""Назначение: адресные проверки пакета, сохранности данных и journal recovery.
Контур: временные файлы и mock services/network; без принтера и systemd.
"""

# Блок 1: Реальный installer в изолированном runtime.
import contextlib
import configparser
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
        self.stop_heating = self.stack.enter_context(patch.object(core, "stop_heating"))
        self.wait = self.stack.enter_context(patch.object(core, "wait_ready", return_value=True))
        self.stack.enter_context(patch.object(core, "owner", return_value=(
            getattr(os, "getuid", lambda: 0)(), getattr(os, "getgid", lambda: 0)())))

    def apply(self):
        return core.apply("v0.2.0", OPERATION, self.env)

    def assert_restored(self):
        for key, data in self.old.items():
            self.assertEqual(core.destination(key, self.locations).read_bytes(), data, key)
        self.assertEqual(core.read_json(core.manifest_path(self.env))["version"], "0.1.0")

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
        self.services.assert_not_called()
        self.assert_restored()

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


class PrinterUpdateGuardTests(unittest.TestCase):
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
