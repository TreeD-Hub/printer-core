from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
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

    def test_full_registry_includes_klipperscreen_only_for_selected_ks(self) -> None:
        source = (ROOT / "loader/loader.sh").read_text(encoding="utf-8")
        start = source.index("UI_STEPS=()")
        end = source.index("# Блок 16:", start)
        registry = source[start:end]
        script = (
            'resolve_treed_ui_mode(){ printf \'%s\\n\' "$TREED_UI_MODE"; };\n'
            f"{registry}\n"
            'printf \'%s\\n\' "${STEPS[@]}"'
        )

        for mode, includes_ks in (("ts", False), ("ks", True)):
            result = self.run_shell(script, {"TREED_UI_MODE": mode})
            self.assertEqual(result.returncode, 0, result.stderr)
            steps = result.stdout.splitlines()
            self.assertEqual("klipperscreen-install" in steps, includes_ks)
            self.assertEqual("klipperscreen-theme" in steps, includes_ks)
            self.assertEqual("klipperscreen-integr" in steps, includes_ks)
            self.assertIn("treed-shell-install", steps)

        managed = self.run_shell(
            script,
            {"TREED_UI_MODE": "ks", "TREED_UPDATE_MANAGED_LOADER": "1"},
        )
        self.assertEqual(managed.returncode, 0, managed.stderr)
        self.assertEqual(
            managed.stdout.splitlines(),
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

    def test_treed_shell_configures_xwrapper_idempotently(self) -> None:
        source = (ROOT / "loader/steps/treed-shell-install.sh").read_text(encoding="utf-8")
        start = source.index("configure_xwrapper() {")
        end = source.index("\n}\n", start) + len("\n}")
        function = source[start:end]

        with tempfile.TemporaryDirectory(dir=self.temp_root) as directory:
            wrapper = Path(directory) / "Xwrapper.config"
            shell_wrapper = str(wrapper).replace("\\", "/")
            fixture = function.replace('"/etc/X11/Xwrapper.config"', f'"{shell_wrapper}"')
            backup_stub = (
                'backup_file_once(){ if [ ! -f "$1.bak" ]; then cp "$1" "$1.bak"; fi; }\n'
            )

            fresh = self.run_shell(f"{backup_stub}{fixture}\nconfigure_xwrapper", {})
            self.assertEqual(fresh.returncode, 0, fresh.stderr)
            self.assertEqual(
                wrapper.read_text(encoding="utf-8"),
                "allowed_users=anybody\nneeds_root_rights=yes\n",
            )
            self.assertFalse(Path(f"{wrapper}.bak").exists())

            wrapper.write_text(
                "# keep this comment\nallowed_users=console\nlocal_setting=preserve\n"
                "needs_root_rights=no\n",
                encoding="utf-8",
            )
            before = wrapper.read_text(encoding="utf-8")
            first = self.run_shell(f"{backup_stub}{fixture}\nconfigure_xwrapper", {})
            self.assertEqual(first.returncode, 0, first.stderr)
            expected = (
                "# keep this comment\nlocal_setting=preserve\n"
                "allowed_users=anybody\nneeds_root_rights=yes\n"
            )
            self.assertEqual(wrapper.read_text(encoding="utf-8"), expected)
            backup = Path(f"{wrapper}.bak")
            self.assertEqual(backup.read_text(encoding="utf-8"), before)

            second = self.run_shell(f"{backup_stub}{fixture}\nconfigure_xwrapper", {})
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(wrapper.read_text(encoding="utf-8"), expected)
            self.assertEqual(backup.read_text(encoding="utf-8"), before)

            wrapper.unlink()
            backup.unlink()
            wrapper.mkdir()
            nonregular = self.run_shell(
                f'log_error(){{ :; }};\n{backup_stub}{fixture}\nconfigure_xwrapper', {}
            )
            self.assertNotEqual(nonregular.returncode, 0)
            self.assertTrue(wrapper.is_dir())

    def test_treed_shell_rejects_xwrapper_symlink(self) -> None:
        source = (ROOT / "loader/steps/treed-shell-install.sh").read_text(encoding="utf-8")
        start = source.index("configure_xwrapper() {")
        end = source.index("\n}\n", start) + len("\n}")
        function = source[start:end]

        with tempfile.TemporaryDirectory(dir=self.temp_root) as directory:
            backup_stub = (
                'backup_file_once(){ if [ ! -f "$1.bak" ]; then cp "$1" "$1.bak"; fi; }\n'
            )
            symlink_dir = Path(directory) / "symlink-case"
            symlink_dir.mkdir()
            symlink = symlink_dir / "Xwrapper.config"
            target = symlink_dir / "target.config"
            target.write_text("keep target\n", encoding="utf-8")
            try:
                os.symlink(target, symlink)
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"Windows symlink creation is unavailable: {error}")

            symlink_fixture = function.replace(
                '"/etc/X11/Xwrapper.config"', f'"{str(symlink).replace(chr(92), "/")}"'
            )
            rejected_link = self.run_shell(
                f'log_error(){{ :; }};\n{backup_stub}{symlink_fixture}\nconfigure_xwrapper', {}
            )
            self.assertNotEqual(rejected_link.returncode, 0)
            self.assertTrue(symlink.is_symlink())
            self.assertEqual(target.read_text(encoding="utf-8"), "keep target\n")

    def test_update_command_install_failure_keeps_previous_executable(self) -> None:
        source = (ROOT / "loader/steps/moonraker-config.sh").read_text(encoding="utf-8")
        start = source.index("install_update_file() {")
        function = source[start:source.index("\n}\n", start) + len("\n}")]
        with tempfile.TemporaryDirectory(dir=self.temp_root) as directory:
            root = Path(directory)
            old, new = root / "updater", root / "source"
            old.write_text("old complete executable", encoding="utf-8")
            new.write_text("new complete executable", encoding="utf-8")
            command = f'install_update_file 0755 "{new.as_posix()}" "{old.as_posix()}"\n'
            failure = ('set -e\n' + function + '\ninstall(){ printf partial > "$4"; return 1; }\n' + command)
            self.assertNotEqual(0, self.run_shell(failure, {}).returncode)
            self.assertEqual("old complete executable", old.read_text())
            retry = self.run_shell('set -e\n' + function + '\n' + command, {})
            self.assertEqual(0, retry.returncode, retry.stderr)
            self.assertEqual("new complete executable", old.read_text())

    def test_managed_moonraker_route_preserves_unknown_config(self) -> None:
        source = (ROOT / "loader/steps/moonraker-config.sh").read_text(encoding="utf-8")
        branch_start = source.index('if [ "${TREED_UPDATE_MANAGED_LOADER:-0}" = "1" ]; then', source.index("# Блок 4:"))
        branch_end = source.index("\nfi\n\nvalidate_repo_moonraker_layout", branch_start) + len("\nfi")
        branch = source[branch_start:branch_end]
        with tempfile.TemporaryDirectory(dir=self.temp_root) as directory:
            config = Path(directory) / "moonraker.conf"
            config.write_text("# user-owned setting\n", encoding="utf-8")
            base_dir = Path(directory) / "base"
            base_dir.mkdir()
            core = base_dir / "00-core.conf"
            original_core = (
                "[other]\nENABLED = leave-this\n\n"
                "[treed_autoprinter]\n# keep credentials and comments\n"
                "# enabled: comment remains\n"
                "credentials_path: /private/credentials.json\n"
                "execution_enabled = false\nunknown_option: preserve-this\n"
                "BeD_Check_Enabled: true\nEnabled = true\n\n"
                "[update_manager mainsail]\npath: /var/www/mainsail\n"
            )
            core.write_text(original_core, encoding="utf-8")
            core.chmod(0o640)
            original_stat = core.stat()
            calls = Path(directory) / "calls"
            script = (
                'python3(){ python "$@"; };\n'
                'backup_file_once(){ if [ ! -f "$1.bak" ]; then cp -p "$1" "$1.bak"; fi; }; '
                "log_error(){ printf '%s\\n' \"$*\" >&2; }; log_info(){ :; }; "
                "ensure_moonraker_allowed_service(){ printf 'allowed:%s ' \"$1\" >> \"$CALL_LOG\"; }; "
                "deploy_treed_update_command(){ printf 'updater ' >> \"$CALL_LOG\"; }; "
                "restart_and_verify_moonraker_components(){ printf 'restart ' >> \"$CALL_LOG\"; }; "
                f"{branch}\n"
            )
            env = {
                "TREED_UPDATE_MANAGED_LOADER": "1",
                "CALL_LOG": str(calls),
                "DST_BASE_DIR": str(base_dir).replace("\\", "/"),
            }
            result = self.run_shell(script, env)
            self.assertEqual(result.returncode, 0, result.stderr)
            expected_core = original_core
            self.assertEqual(core.read_text(encoding="utf-8"), expected_core)
            backup = Path(f"{core}.bak")
            self.assertFalse(backup.exists())
            migrated_stat = core.stat()
            self.assertEqual(migrated_stat.st_mode & 0o777, original_stat.st_mode & 0o777)
            self.assertEqual((migrated_stat.st_uid, migrated_stat.st_gid), (original_stat.st_uid, original_stat.st_gid))
            migrated_mtime = migrated_stat.st_mtime_ns

            result = self.run_shell(script, env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(core.read_text(encoding="utf-8"), expected_core)
            self.assertEqual(core.stat().st_mtime_ns, migrated_mtime)
            self.assertFalse(backup.exists())
            self.assertEqual(calls.read_text(encoding="utf-8"), "allowed:treed-shell updater restart " * 2)
            self.assertEqual(config.read_text(encoding="utf-8"), "# user-owned setting\n")

            without_final_newline = expected_core.rstrip("\n")
            core.write_text(without_final_newline, encoding="utf-8")
            clean_mtime = core.stat().st_mtime_ns
            result = self.run_shell(script, env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(core.read_text(encoding="utf-8"), without_final_newline)
            self.assertEqual(core.stat().st_mtime_ns, clean_mtime)
            self.assertFalse(backup.exists())

    def test_full_moonraker_merge_removes_only_legacy_autoprinter_options(self) -> None:
        source = (ROOT / "loader/steps/moonraker-config.sh").read_text(encoding="utf-8")
        start = source.index("merge_preserved_autoprinter_section() {")
        end = source.index("\n}\n", start) + len("\n}")
        function = source[start:end]
        with tempfile.TemporaryDirectory(dir=self.temp_root) as directory:
            old = Path(directory) / "old.conf"
            new = Path(directory) / "new.conf"
            old.write_text(
                "[treed_autoprinter]\n# credential comment\n"
                "credentials_path: /private/credentials.json\n"
                "ENABLED = false\nexecution_enabled: true\nBed_Check_Enabled = false\n"
                "custom_setting: keep-me\n\n[other]\nenabled: keep-this\n",
                encoding="utf-8",
            )
            new.write_text(
                "[treed_autoprinter]\nprinter_id: default\n"
                "credentials_path: /default/credentials.json\n\n"
                "[other]\nenabled = keep-this\n",
                encoding="utf-8",
            )
            result = self.run_shell(
                'python3(){ python "$@"; };\n'
                f'{function}\nmerge_preserved_autoprinter_section "$OLD" "$NEW"',
                {"OLD": str(old).replace("\\", "/"), "NEW": str(new).replace("\\", "/")},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                new.read_text(encoding="utf-8"),
                "[treed_autoprinter]\n# credential comment\n"
                "credentials_path: /private/credentials.json\ncustom_setting: keep-me\n"
                "\nprinter_id: default\n[other]\nenabled = keep-this\n",
            )


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

    def test_camera_waits_for_print_status_without_allowing_active_prints(self) -> None:
        source = (ROOT / "loader/steps/treed-cam.sh").read_text(encoding="utf-8")
        check = source[source.index("# Блок 4:"):source.index("VISION_WAS_ACTIVE=0")]
        fixture = r'''
set -euo pipefail
log_error(){ printf '%s\n' "$*" >&2; }
sleep(){ :; }
systemctl(){ [[ "$*" = "is-active --quiet moonraker.service" ]]; }
python3(){ "$FIXTURE_PYTHON" "$@"; }
curl(){
  local attempt
  attempt=$(cat "$FIXTURE_DIR/attempts")
  attempt=$((attempt+1))
  printf '%s' "$attempt" > "$FIXTURE_DIR/attempts"
  if [ "$attempt" -le "$FIXTURE_HTTP_FAILURES" ]; then return 22; fi
  printf '{"result":{"status":{"print_stats":{"state":"%s"}}}}' "$FIXTURE_PRINT_STATE"
}
'''
        for failures, state, allowed, attempts in (
            (1, "standby", True, 2),
            (3, "standby", False, 3),
            (0, "printing", False, 1),
            (0, "paused", False, 1),
            (0, "complete", True, 1),
        ):
            with self.subTest(failures=failures, state=state), tempfile.TemporaryDirectory(dir=self.temp_root) as directory:
                counter = Path(directory) / "attempts"
                counter.write_text("0", encoding="utf-8")
                script = Path(directory) / "camera-idle.sh"
                script.write_text(fixture + check + '\nprintf "allowed"\n', encoding="utf-8", newline="\n")
                result = self.run_shell(
                    f'. "{script.as_posix()}"',
                    {
                        "FIXTURE_DIR": Path(directory).as_posix(),
                        "FIXTURE_PYTHON": Path(sys.executable).as_posix(),
                        "FIXTURE_HTTP_FAILURES": str(failures),
                        "FIXTURE_PRINT_STATE": state,
                        "TREED_MOONRAKER_HTTP_RETRIES": "3",
                    },
                )
                self.assertEqual(result.returncode, 0 if allowed else 1, result.stderr)
                self.assertEqual(result.stdout, "allowed" if allowed else "")
                self.assertEqual(counter.read_text(encoding="utf-8"), str(attempts))

    def test_verify_waits_for_klipper_startup(self) -> None:
        source = (ROOT / "loader/steps/verify.sh").read_text(encoding="utf-8")
        functions = "\n".join(
            re.search(rf"^{name}\(\) \{{\n.*?^\}}", source, re.MULTILINE | re.DOTALL).group(0)
            for name in (
                "pass", "failf", "diagnostic_failf", "hardware_failf", "is_true",
                "json_string_value", "printer_info_check",
            )
        )
        fixture = r'''
set -euo pipefail
ok=0; fail=0; VERIFY_DIAGNOSTIC_FAILS=0
log_info(){ :; }
log_warn(){ :; }
sleep(){ :; }
mktemp(){ command mktemp "$FIXTURE_DIR/info.XXXXXX"; }
curl(){
  local output="" attempt index
  local states=($FIXTURE_STATES)
  while [ "$#" -gt 0 ]; do
    if [ "$1" = "-o" ]; then output="$2"; shift; fi
    shift
  done
  attempt=$(cat "$FIXTURE_DIR/attempts")
  attempt=$((attempt+1))
  printf '%s' "$attempt" > "$FIXTURE_DIR/attempts"
  index=$((attempt-1))
  if [ "$index" -ge "${#states[@]}" ]; then index=$((${#states[@]}-1)); fi
  printf '{"result":{"state":"%s","state_message":"fixture"}}' "${states[$index]}" > "$output"
  printf '200'
}
'''
        cases = (
            ("ready", "ready", 0, 1, 1),
            ("startup startup ready", "ready", 0, 1, 3),
            ("startup", "startup", 1, 0, 3),
            ("startup error ready", "error", 1, 0, 2),
            ("shutdown ready", "shutdown", 1, 0, 1),
        )
        for states, state, failures, ready, attempts in cases:
            with self.subTest(states=states), tempfile.TemporaryDirectory(dir=self.temp_root) as directory:
                counter = Path(directory) / "attempts"
                counter.write_text("0", encoding="utf-8")
                script = Path(directory) / "readiness.sh"
                script.write_text(
                    fixture + functions + '\nprinter_info_check "Klipper" "http://fixture"\n'
                    'printf "%s|%s|%s" "$KLIPPER_STATE" "$fail" "$MOONRAKER_READY_OK"',
                    encoding="utf-8", newline="\n",
                )
                result = self.run_shell(
                    f'. "{script.as_posix()}"',
                    {
                        "FIXTURE_DIR": Path(directory).as_posix(),
                        "FIXTURE_STATES": states,
                        "TREED_MOONRAKER_HTTP_RETRIES": "3",
                        "TREED_ALLOW_HARDWARE_NOT_READY": "0",
                    },
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, f"{state}|{failures}|{ready}", result.stderr)
                self.assertEqual(counter.read_text(encoding="utf-8"), str(attempts))

    def test_verify_records_baseline_and_completion_only_for_standalone_loader(self) -> None:
        source = (ROOT / "loader/steps/verify.sh").read_text(encoding="utf-8")
        marker = 'if [ "${fail}" -eq 0 ]; then'
        final_block = source[source.rfind(marker) :]
        self.assertIn("TREED_UPDATE_MANAGED_LOADER", final_block)
        with tempfile.TemporaryDirectory(dir=self.temp_root) as directory:
            sentinel = Path(directory) / "baseline-called"
            stub = Path(directory) / "core-update"
            stub.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$1\" >> '{sentinel}'\n", encoding="utf-8")
            stub.chmod(0o755)
            final_block = final_block.replace(
                "/usr/local/sbin/treed-core-update", str(stub)
            ).replace(
                "/usr/local/sbin/treed-update-service", str(stub)
            )
            for managed in ("1", "0"):
                result = self.run_shell(
                    "fail=0; ok=1; VERIFY_DIAGNOSTIC_FAILS=0; "
                    "log_info(){ :; }; log_warn(){ :; }; " + final_block,
                    {"TREED_UPDATE_MANAGED_LOADER": managed, "TREED_ALLOW_HARDWARE_NOT_READY": "0"},
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                if managed == "1":
                    self.assertFalse(sentinel.exists())
                else:
                    self.assertEqual(sentinel.read_text().splitlines(), ["baseline", "complete-loader"])

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
            for managed, ui_mode, expected in (
                ("1", "ts", "repo:Klipper repo:Moonraker "),
                ("0", "ts", "mainsail repo:Klipper repo:Moonraker repo:Crowsnest "),
                (
                    "0",
                    "ks",
                    "mainsail repo:Klipper repo:Moonraker repo:KlipperScreen repo:Crowsnest ",
                ),
            ):
                result = self.run_shell(
                    f"{stubs}\n{checks}\nprintf '%s' \"$CHECKS\"",
                    {
                        "TREED_UPDATE_MANAGED_LOADER": managed,
                        "TREED_UI_MODE": ui_mode,
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
