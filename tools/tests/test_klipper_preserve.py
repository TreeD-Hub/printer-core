from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash")
SOURCE = (ROOT / "loader/steps/klipper-core.sh").read_text(encoding="utf-8")
PRESERVE_START = SOURCE.index("# Блок 4: Preserve-режим")
PRESERVE_END = SOURCE.index("# Блок 5:", PRESERVE_START)
PRESERVE_BLOCK = SOURCE[PRESERVE_START:PRESERVE_END]
RESTORE_START = SOURCE.index("# Блок 7:")
RESTORE_END = SOURCE.index("# Блок 8:", RESTORE_START)
RESTORE_BLOCK = SOURCE[RESTORE_START:RESTORE_END]
SANITIZE_START = SOURCE.index("sanitize_save_config_block() {")
SANITIZE_END = SOURCE.index("\n}\n", SANITIZE_START) + len("\n}")
SANITIZE_FUNCTION = SOURCE[SANITIZE_START:SANITIZE_END]
CLEANUP_START = SOURCE.index('if [ "${DEPLOY_MODE}" = "preserve" ]; then', SOURCE.index("# Блок 5:"))
CLEANUP_END = SOURCE.index("# Полная раскладка дерева из staging", CLEANUP_START)
CLEANUP_BLOCK = SOURCE[CLEANUP_START:CLEANUP_END]
SAVE_CONFIG_MARKER = "#*# <---------------------- SAVE_CONFIG ---------------------->"


@unittest.skipUnless(BASH, "bash is required for Klipper preserve fixtures")
class KlipperPreserveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_parent = ROOT / ".unlazy" / "contour-fixes-20261022"
        cls.temp_parent.mkdir(parents=True, exist_ok=True)

    def run_shell(self, script: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [BASH, "-c", script],
            cwd=ROOT,
            env={**os.environ, **env},
            capture_output=True,
            text=True,
            check=False,
        )

    def common_setup(self) -> str:
        return (
            'set -euo pipefail\n'
            'CONFIG_DIR="$TEST_CONFIG_DIR"\n'
            f'SAVE_CONFIG_MARKER={SAVE_CONFIG_MARKER!r}\n'
            'log_info(){ :; }; log_warn(){ :; }; '
            'log_error(){ printf "%s\\n" "$*" >&2; }\n'
            'mktemp(){ if [ "${1:-}" = "-d" ]; then mkdir -p "$TEST_TMP_KEEP"; '
            'printf "%s\\n" "$TEST_TMP_KEEP"; else command mktemp "$@"; fi; }\n'
        )

    def new_fixture(self, root: Path) -> tuple[Path, Path, dict[str, str]]:
        config = root / "config"
        config.mkdir()
        keep = root / "keep"
        env = {
            "TEST_CONFIG_DIR": str(config).replace("\\", "/"),
            "TEST_TMP_KEEP": str(keep).replace("\\", "/"),
        }
        return config, keep, env

    def test_preserve_restores_and_normalizes_local_data(self) -> None:
        with tempfile.TemporaryDirectory(dir=self.temp_parent) as directory:
            config, keep, env = self.new_fixture(Path(directory))
            (config / "filament_motion_runtime.cfg").write_text(
                "[filament_motion_sensor filament_motion]\ndetection_length: 7\n",
                encoding="utf-8",
            )
            (config / "printer.cfg").write_text(
                "[extruder]\npid_kp: 1\n"
                f"{SAVE_CONFIG_MARKER}\n#*# [extruder]\n#*# pid_kp = 42\n",
                encoding="utf-8",
            )
            snapshot = (
                self.common_setup()
                + SANITIZE_FUNCTION
                + "\n"
                + 'DEPLOY_MODE="preserve"\n'
                + PRESERVE_BLOCK
            )
            result = self.run_shell(snapshot, env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((keep / "filament_motion_runtime.cfg").is_file())
            self.assertTrue((keep / "printer_save_config.block").is_file())

            (config / "printer.cfg").write_text("[extruder]\npid_kp: 1\n", encoding="utf-8")
            restore = self.common_setup() + 'TMP_KEEP="$TEST_TMP_KEEP"\n' + RESTORE_BLOCK
            result = self.run_shell(restore, env)
            self.assertEqual(result.returncode, 0, result.stderr)
            filament = (config / "filament_motion_runtime.cfg").read_text(encoding="utf-8")
            self.assertIn("[treed_filament_motion_sensor filament_motion]", filament)
            printer = (config / "printer.cfg").read_text(encoding="utf-8")
            self.assertIn("#pid_kp: 1", printer)
            self.assertIn("#*# pid_kp = 42", printer)
            self.assertFalse(keep.exists())

    def test_snapshot_copy_failure_stops_before_runtime_cleanup(self) -> None:
        with tempfile.TemporaryDirectory(dir=self.temp_parent) as directory:
            config, keep, env = self.new_fixture(Path(directory))
            protected = config / "old-runtime.cfg"
            protected.write_text("keep runtime\n", encoding="utf-8")
            (config / "filament_motion_runtime.cfg").write_text("local setting\n", encoding="utf-8")
            script = (
                self.common_setup()
                + SANITIZE_FUNCTION
                + "\n"
                + '\ncp(){ case "$2" in */filament_motion_runtime.cfg) return 1;; '
                '*) command cp "$@";; esac; }\n'
                + 'DEPLOY_MODE="preserve"\n'
                + PRESERVE_BLOCK
                + CLEANUP_BLOCK
            )
            result = self.run_shell(script, env)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn(str(keep).replace("\\", "/"), result.stderr)
            self.assertEqual(protected.read_text(encoding="utf-8"), "keep runtime\n")
            self.assertTrue(keep.is_dir())

    def test_grep_read_error_is_not_treated_as_missing_save_config(self) -> None:
        with tempfile.TemporaryDirectory(dir=self.temp_parent) as directory:
            config, keep, env = self.new_fixture(Path(directory))
            protected = config / "old-runtime.cfg"
            protected.write_text("keep runtime\n", encoding="utf-8")
            (config / "printer.cfg").write_text("[extruder]\n", encoding="utf-8")
            script = (
                self.common_setup()
                + SANITIZE_FUNCTION
                + "\n"
                + '\ngrep(){ return 2; }\n'
                + 'DEPLOY_MODE="preserve"\n'
                + PRESERVE_BLOCK
                + CLEANUP_BLOCK
            )
            result = self.run_shell(script, env)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("cannot read printer.cfg", result.stderr)
            self.assertEqual(protected.read_text(encoding="utf-8"), "keep runtime\n")

    def test_awk_snapshot_failure_stops_before_runtime_cleanup(self) -> None:
        with tempfile.TemporaryDirectory(dir=self.temp_parent) as directory:
            config, keep, env = self.new_fixture(Path(directory))
            protected = config / "old-runtime.cfg"
            protected.write_text("keep runtime\n", encoding="utf-8")
            (config / "printer.cfg").write_text(
                f"[extruder]\n{SAVE_CONFIG_MARKER}\n#*# pid_kp = 42\n",
                encoding="utf-8",
            )
            script = (
                self.common_setup()
                + SANITIZE_FUNCTION
                + "\n"
                + 'awk(){ case "$*" in *printer.cfg*) return 1;; '
                '*) command awk "$@";; esac; }\n'
                + 'DEPLOY_MODE="preserve"\n'
                + PRESERVE_BLOCK
                + CLEANUP_BLOCK
            )
            result = self.run_shell(script, env)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("cannot snapshot printer.cfg SAVE_CONFIG", result.stderr)
            self.assertEqual(protected.read_text(encoding="utf-8"), "keep runtime\n")

    def test_empty_save_config_snapshot_stops_before_runtime_cleanup(self) -> None:
        with tempfile.TemporaryDirectory(dir=self.temp_parent) as directory:
            config, keep, env = self.new_fixture(Path(directory))
            protected = config / "old-runtime.cfg"
            protected.write_text("keep runtime\n", encoding="utf-8")
            (config / "printer.cfg").write_text(
                f"[extruder]\n{SAVE_CONFIG_MARKER}\n#*# pid_kp = 42\n",
                encoding="utf-8",
            )
            script = (
                self.common_setup()
                + SANITIZE_FUNCTION
                + "\n"
                + 'awk(){ case "$*" in *printer.cfg*) return 0;; '
                '*) command awk "$@";; esac; }\n'
                + 'DEPLOY_MODE="preserve"\n'
                + PRESERVE_BLOCK
                + CLEANUP_BLOCK
            )
            result = self.run_shell(script, env)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("snapshot is empty", result.stderr)
            self.assertEqual(protected.read_text(encoding="utf-8"), "keep runtime\n")
            self.assertTrue((keep / "printer_save_config.block").is_file())

    def test_nonregular_preserved_data_stops_before_runtime_cleanup(self) -> None:
        with tempfile.TemporaryDirectory(dir=self.temp_parent) as directory:
            config, keep, env = self.new_fixture(Path(directory))
            protected = config / "old-runtime.cfg"
            protected.write_text("keep runtime\n", encoding="utf-8")
            (config / "filament_motion_runtime.cfg").mkdir()
            script = (
                self.common_setup()
                + SANITIZE_FUNCTION
                + "\n"
                + 'DEPLOY_MODE="preserve"\n'
                + PRESERVE_BLOCK
                + CLEANUP_BLOCK
            )
            result = self.run_shell(script, env)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("must be a regular file", result.stderr)
            self.assertEqual(protected.read_text(encoding="utf-8"), "keep runtime\n")

    def test_symlink_preserved_data_stops_before_runtime_cleanup(self) -> None:
        with tempfile.TemporaryDirectory(dir=self.temp_parent) as directory:
            config, keep, env = self.new_fixture(Path(directory))
            protected = config / "old-runtime.cfg"
            protected.write_text("keep runtime\n", encoding="utf-8")
            target = config / "local-filament.cfg"
            target.write_text("local setting\n", encoding="utf-8")
            link = config / "filament_motion_runtime.cfg"
            try:
                os.symlink(target, link)
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"Windows symlink creation is unavailable: {error}")
            script = (
                self.common_setup()
                + SANITIZE_FUNCTION
                + "\n"
                + 'DEPLOY_MODE="preserve"\n'
                + PRESERVE_BLOCK
                + CLEANUP_BLOCK
            )
            result = self.run_shell(script, env)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("must be a regular file", result.stderr)
            self.assertTrue(link.is_symlink())
            self.assertEqual(target.read_text(encoding="utf-8"), "local setting\n")
            self.assertEqual(protected.read_text(encoding="utf-8"), "keep runtime\n")

    def test_restore_copy_failure_keeps_recovery_snapshot(self) -> None:
        with tempfile.TemporaryDirectory(dir=self.temp_parent) as directory:
            config, keep, env = self.new_fixture(Path(directory))
            keep.mkdir()
            saved = keep / "filament_motion_runtime.cfg"
            saved.write_text("local setting\n", encoding="utf-8")
            script = (
                self.common_setup()
                + 'TMP_KEEP="$TEST_TMP_KEEP"\n'
                + 'cp(){ case "$2" in "$TEST_TMP_KEEP/filament_motion_runtime.cfg") '
                'return 1;; *) command cp "$@";; esac; }\n'
                + RESTORE_BLOCK
            )
            result = self.run_shell(script, env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("recovery copy retained", result.stderr)
            self.assertEqual(saved.read_text(encoding="utf-8"), "local setting\n")
            self.assertTrue(keep.is_dir())


if __name__ == "__main__":
    unittest.main()
