from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash")


@unittest.skipUnless(BASH, "bash is required for managed loader shell fixtures")
class ManagedLoaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        temp_parent = ROOT / ".unlazy" / "loader-transition"
        cls.created_temp_dirs = []
        parent = temp_parent
        while not parent.exists():
            cls.created_temp_dirs.append(parent)
            parent = parent.parent
        temp_parent.mkdir(parents=True, exist_ok=True)
        cls.temp_directory = tempfile.TemporaryDirectory(
            prefix="test_managed_loader_", dir=temp_parent
        )
        cls.temp_root = Path(cls.temp_directory.name)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_directory.cleanup()
        for path in cls.created_temp_dirs:
            try:
                path.rmdir()
            except OSError:
                break

    def run_shell(self, script: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                [BASH, "-c", script],
                cwd=ROOT,
                env={**os.environ, **env},
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as error:
            self.skipTest(f"bash cannot be started in this environment: {error}")

    def test_managed_registry_is_fixed_and_excludes_full_provisioning(self) -> None:
        source = (ROOT / "loader/loader.sh").read_text(encoding="utf-8")
        managed = re.search(
            r'if \[ "\$\{TREED_UPDATE_MANAGED_LOADER\}" = "1" \]; then\s*STEPS=\((.*?)\n  \)\nfi',
            source,
            re.DOTALL,
        )
        self.assertIsNotNone(managed)
        steps = re.findall(r'"([^"]+)"', managed.group(1))
        self.assertEqual(
            steps,
            [
                "check-env",
                "maintenance-stop",
                "packages-core",
                "runtime-bootstrap",
                "moonraker-config",
                "treed-cam",
                "maintenance-start",
                "verify",
            ],
        )
        self.assertNotIn("firmware-build", steps)
        self.assertNotIn("klipper-core", steps)
        self.assertNotIn("klipper-sync", steps)

    def test_managed_moonraker_route_preserves_unknown_config(self) -> None:
        source = (ROOT / "loader/steps/moonraker-config.sh").read_text(encoding="utf-8")
        branch_start = source.index('if [ "${TREED_UPDATE_MANAGED_LOADER:-0}" = "1" ]; then', source.index("# Блок 4:"))
        branch_end = source.index("\nfi\n\nvalidate_repo_moonraker_layout", branch_start) + len("\nfi")
        branch = source[branch_start:branch_end]
        with tempfile.TemporaryDirectory(dir=self.temp_root) as directory:
            config = Path(directory) / "moonraker.conf"
            config.write_text("# user-owned setting\n", encoding="utf-8")
            calls = Path(directory) / "calls"
            result = self.run_shell(
                "ensure_moonraker_allowed_service(){ printf 'allowed:%s ' \"$1\" >> \"$CALL_LOG\"; }; "
                "deploy_treed_update_command(){ printf 'updater ' >> \"$CALL_LOG\"; }; "
                "restart_and_verify_moonraker_components(){ printf 'restart ' >> \"$CALL_LOG\"; }; "
                "log_info(){ :; }; "
                f"{branch}\n"
                ,
                {"TREED_UPDATE_MANAGED_LOADER": "1", "CALL_LOG": str(calls)},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(calls.read_text(encoding="utf-8"), "allowed:treed-shell updater restart ")
            self.assertEqual(config.read_text(encoding="utf-8"), "# user-owned setting\n")

    def test_managed_camera_sync_preserves_unknown_runtime_files(self) -> None:
        source = (ROOT / "loader/steps/treed-cam.sh").read_text(encoding="utf-8")
        sync_start = source.index("# Блок 6: Атомарная синхронизация")
        sync_end = source.index("# Блок 7:", sync_start)
        sync_block = source[sync_start:sync_end]
        with tempfile.TemporaryDirectory(dir=self.temp_root) as directory:
            camera_bin = Path(directory) / "camera" / "bin"
            vision_bin = Path(directory) / "vision" / "bin"
            camera_bin.mkdir(parents=True)
            vision_bin.mkdir(parents=True)
            camera_unknown = camera_bin / "local-camera-hook"
            vision_unknown = vision_bin / "local-model-adapter.py"
            camera_unknown.write_text("camera-local\n", encoding="utf-8")
            vision_unknown.write_text("vision-local\n", encoding="utf-8")
            result = self.run_shell(
                f"{sync_block}\n",
                {"TREED_UPDATE_MANAGED_LOADER": "1"},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(camera_unknown.read_text(encoding="utf-8"), "camera-local\n")
            self.assertEqual(vision_unknown.read_text(encoding="utf-8"), "vision-local\n")

    def test_managed_camera_chown_leaves_unknown_file_metadata(self) -> None:
        source = (ROOT / "loader/steps/treed-cam.sh").read_text(encoding="utf-8")
        rights_start = source.index("# Блок 7: Исполняемость shell-скриптов")
        rights_end = source.index("# Блок 8:", rights_start)
        rights_block = source[rights_start:rights_end]
        with tempfile.TemporaryDirectory(dir=self.temp_root) as directory:
            root = Path(directory) / "cam"
            bin_dir = root / "bin"
            data_dir = root / "prints"
            config_dir = root / "config"
            for path in (bin_dir, data_dir, config_dir):
                path.mkdir(parents=True)
            unknown = bin_dir / "local-hook"
            unknown.write_text("keep\n", encoding="utf-8")
            unknown.chmod(0o640)
            before = unknown.stat()
            calls = Path(directory) / "chown-calls"
            result = self.run_shell(
                "chown(){ printf '%s\\n' \"$*\" >> \"$CALL_LOG\"; "
                "if [ \"${1:-}\" = '-R' ]; then chmod 0600 \"$UNKNOWN_FILE\"; fi; };\n"
                f"{rights_block}\n",
                {
                    "TREED_UPDATE_MANAGED_LOADER": "1",
                    "PI_USER": "printer",
                    "grp": "printer",
                    "DST_ROOT": str(root),
                    "DST_BIN": str(bin_dir),
                    "DST_DATA": str(data_dir),
                    "CALL_LOG": str(calls),
                    "UNKNOWN_FILE": str(unknown),
                },
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            after = unknown.stat()
            self.assertEqual((after.st_mode, after.st_uid, after.st_gid), (before.st_mode, before.st_uid, before.st_gid))
            self.assertNotIn("-R", calls.read_text(encoding="utf-8"))

    def test_managed_verify_does_not_record_baseline(self) -> None:
        source = (ROOT / "loader/steps/verify.sh").read_text(encoding="utf-8")
        marker = 'if [ "${fail}" -eq 0 ]; then'
        final_block = source[source.rfind(marker) :]
        self.assertIn("TREED_UPDATE_MANAGED_LOADER", final_block)
        with tempfile.TemporaryDirectory(dir=self.temp_root) as directory:
            sentinel = Path(directory) / "baseline-called"
            stub = Path(directory) / "core-update"
            stub.write_text(f"#!/bin/sh\nprintf called > '{sentinel}'\n", encoding="utf-8")
            stub.chmod(0o755)
            final_block = final_block.replace(
                "/usr/local/sbin/treed-core-update", str(stub)
            )
            result = self.run_shell(
                "fail=0; ok=1; VERIFY_DIAGNOSTIC_FAILS=0; "
                "log_info(){ :; }; log_warn(){ :; }; " + final_block,
                {"TREED_UPDATE_MANAGED_LOADER": "1", "TREED_ALLOW_HARDWARE_NOT_READY": "0"},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(sentinel.exists())

    def test_verify_pins_only_updated_runtimes_in_managed_mode(self) -> None:
        source = (ROOT / "loader/steps/verify.sh").read_text(encoding="utf-8")
        start = source.index('if [ -f "${TREED_MAINSAIL_WEB_PATH}/release_info.json" ]; then', source.index("# Блок 7:"))
        end = source.index('if [ -f "${TREED_MAINSAIL_WEB_PATH}/index.html" ]; then', start)
        checks = source[start:end]
        with tempfile.TemporaryDirectory(dir=self.temp_root) as directory:
            web_root = Path(directory) / "mainsail"
            web_root.mkdir()
            (web_root / "release_info.json").write_text("{}\n", encoding="utf-8")
            stubs = (
                "pass(){ :; }; failf(){ :; }; log_info(){ :; }; "
                "mainsail_manifest_version_check(){ printf 'mainsail '; }; "
                "runtime_repo_check(){ printf 'repo:%s ' \"$1\"; }\n"
            )
            for managed, expected in (
                ("1", "repo:Klipper repo:Moonraker "),
                ("0", "mainsail repo:Klipper repo:Moonraker repo:KlipperScreen repo:Crowsnest "),
            ):
                result = self.run_shell(
                    f"{stubs}\n{checks}\nprintf '%s' \"$CHECKS\"",
                    {
                        "TREED_UPDATE_MANAGED_LOADER": managed,
                        "TREED_MAINSAIL_WEB_PATH": str(web_root),
                        "TREED_CROWSNEST_INSTALL": "1",
                    },
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, expected)

    def test_managed_verify_requires_vision_services(self) -> None:
        source = (ROOT / "loader/steps/verify.sh").read_text(encoding="utf-8")
        check_start = source.index('check_required_service_active "nginx.service"')
        block_start = source.index(
            'if [ "${TREED_UPDATE_MANAGED_LOADER:-0}" = "1" ]; then', check_start
        )
        block_end = source.index("\nfi", block_start) + len("\nfi")
        checks = source[block_start:block_end]
        for managed, expected in (
            ("1", "treed-vision.service treed-detection.service "),
            ("0", ""),
        ):
            result = self.run_shell(
                "check_required_service_active(){ printf '%s ' \"$1\"; };\n"
                f"{checks}\n",
                {"TREED_UPDATE_MANAGED_LOADER": managed},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, expected)


if __name__ == "__main__":
    unittest.main()
