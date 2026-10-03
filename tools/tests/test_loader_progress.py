"""Read-only: настоящий цикл loader на временных шагах; без provisioning и устройства."""
import os
from pathlib import Path
import pty
import select
import signal
import shlex
import subprocess
import tempfile
import time
import unittest

# Блок 1: Берём цикл и fail-fast/optional функции из текущего оркестратора.
ROOT = Path(__file__).resolve().parents[2]
LOADER = (ROOT / "loader/loader.sh").read_text(encoding="utf-8")
HELPERS = LOADER.split("# Блок 17:", 1)[1].split("# Блок 19:", 1)[0]
LOOP = "# Блок 20:" + LOADER.split("# Блок 20:", 1)[1]
ERROR_TRAP = next(line for line in LOADER.splitlines() if line.startswith("trap ") and line.endswith(" ERR"))

class LoaderProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="treed-progress-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        steps = self.root / "loader/steps"
        steps.mkdir(parents=True)
        (self.root / "loader/lib").symlink_to(ROOT / "loader/lib", target_is_directory=True)
        self.step("first", "printf '[WARN] first warning\\n'; printf 'WARNING: external\\n' >&2; "
                  "printf 'fragment'; sleep 0.2; printf ' continued\\n\\n'")
        self.step("crowsnest-webcam", "printf '[ERROR] camera unavailable\\n' >&2; exit 7")
        self.step("last", "printf 'LAST_STEP_EXECUTED\\n'")
        self.script = (
            "set -euo pipefail\n"
            f"REPO_DIR={shlex.quote(str(self.root))}\n"
            'PI_USER=""\n. "${REPO_DIR}/loader/lib/common.sh"\n'
            'STEPS=(first crowsnest-webcam last)\nOPTIONAL_STEPS=(crowsnest-webcam)\n'
            'write_device_state_snapshot() { printf "STATE_SNAPSHOT\\n"; }\n'
            + ERROR_TRAP + "\n# Блок 17:" + HELPERS + "\n" + LOOP
        )

    def step(self, name, body):
        (self.root / f"loader/steps/{name}.sh").write_text("#!/bin/bash\nset -euo pipefail\n" + body + "\n", encoding="utf-8")

    def run_loader(self, mode="plain", **env):
        return subprocess.run(["bash", "-c", self.script], text=True, capture_output=True,
                              env={**os.environ, "TREED_LOADER_PROGRESS": mode, **env}, timeout=5)

    # Блок 2: Учёт логов и шагов не меняет required/optional семантику.
    def test_optional_failure_continues_and_counts_messages(self):
        result = self.run_loader()
        self.assertEqual(result.returncode, 0, result.stderr)
        for expected in ["LAST_STEP_EXECUTED", "fragment continued\n\n", "STATE_SNAPSHOT",
                         "100% · выполнено 3/3 · ошибок 1 · предупреждений 3", "необязательных сбоев 1"]:
            self.assertIn(expected, result.stdout)
        self.assertNotIn("\x1b", result.stdout)
        self.assertNotIn("\x1e", result.stdout)

    def test_required_failure_keeps_exit_code_and_stops(self):
        self.step("first", "printf '[ERROR] failed\\n' >&2; exit 9")
        result = self.run_loader()
        self.assertEqual(result.returncode, 9)
        self.assertIn("Остановлено на шаге 1/3: first (rc=9)", result.stdout)
        self.assertIn("0% · выполнено 0/3 · ошибок 1", result.stdout)
        self.assertNotIn("LAST_STEP_EXECUTED", result.stdout)
        self.assertNotIn("finished successfully", result.stdout)

    def test_silent_failure_gets_an_error_diagnostic(self):
        self.step("first", "exit 9")
        result = self.run_loader()
        self.assertEqual(result.returncode, 9)
        self.assertIn("[ERROR] Loader stopped: step=first rc=9", result.stdout)
        self.assertIn("ошибок 1", result.stdout)

    def test_unterminated_command_output_does_not_hide_step_markers(self):
        self.step("first", "printf 'NO_NEWLINE'")
        result = self.run_loader()
        self.assertEqual(result.returncode, 0)
        self.assertIn("NO_NEWLINE", result.stdout)
        self.assertIn("100% · выполнено 3/3", result.stdout)
        self.assertNotIn("\x1e", result.stdout)

    def test_missing_optional_step_counts_skip(self):
        (self.root / "loader/steps/crowsnest-webcam.sh").unlink()
        result = self.run_loader()
        self.assertEqual(result.returncode, 0)
        self.assertIn("пропущено 1 · необязательных сбоев 0", result.stdout)
        self.assertIn("100% · выполнено 3/3", result.stdout)

    def test_camera_required_stops_on_same_error(self):
        result = self.run_loader(TREED_CAMERA_REQUIRED="1")
        self.assertEqual(result.returncode, 7)
        self.assertIn("Остановлено на шаге 2/3", result.stdout)
        self.assertIn("33% · выполнено 1/3", result.stdout)
        self.assertNotIn("LAST_STEP_EXECUTED", result.stdout)

    def test_off_preserves_regular_output_and_stdin(self):
        self.step("first", 'read -r token; printf "STDIN=%s\\n" "$token"')
        result = subprocess.run(["bash", "-c", self.script], text=True, capture_output=True,
                                input="test-token\n", env={**os.environ, "TREED_LOADER_PROGRESS": "off"}, timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertIn("STDIN=test-token", result.stdout)
        self.assertNotIn("[progress]", result.stdout)
        self.assertIn("[ERROR] camera unavailable", result.stderr)
        result = subprocess.run(["bash", "-c", self.script], text=True, capture_output=True,
                                input="test-token\n", env={**os.environ, "TREED_LOADER_PROGRESS": "plain"}, timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertIn("STDIN=test-token", result.stdout)

    def test_auto_redirected_output_has_no_terminal_codes(self):
        result = self.run_loader(mode="auto")
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("\x1b", result.stdout)
        self.assertIn("100% · выполнено 3/3", result.stdout)

    def test_interrupt_closes_renderer_and_keeps_signal_exit_status(self):
        self.step("first", "printf 'READY_FOR_INTERRUPT\\n'; sleep 30")
        process = subprocess.Popen(["bash", "-c", self.script], stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, start_new_session=True,
                                   env={**os.environ, "TREED_LOADER_PROGRESS": "plain"})
        output = bytearray()
        try:
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if select.select([process.stdout], [], [], 0.1)[0]:
                    line = process.stdout.readline()
                    output.extend(line)
                    if b"READY_FOR_INTERRUPT" in line:
                        break
            self.assertIn(b"READY_FOR_INTERRUPT", output)
            os.killpg(process.pid, signal.SIGINT)
            tail, _ = process.communicate(timeout=3)
            output.extend(tail)
            self.assertIn(process.returncode, [130, -signal.SIGINT], output.decode("utf-8"))
            self.assertIn("Остановлено на шаге 1/3", output.decode("utf-8"))
            self.assertIn("(rc=130)", output.decode("utf-8"))
            self.assertNotIn("not a child", output.decode("utf-8"))
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()

    # Блок 3: Настоящий PTY проверяет анимацию во время молчания дочерней команды.
    def test_tty_animates_scale_without_losing_logs(self):
        master, slave = pty.openpty()
        process = subprocess.Popen(["bash", "-c", self.script], stdin=subprocess.DEVNULL,
                                   stdout=slave, stderr=slave,
                                   env={**os.environ, "TERM": "xterm", "TREED_LOADER_PROGRESS": "auto"})
        os.close(slave)
        output = bytearray()
        deadline = time.monotonic() + 5
        try:
            while time.monotonic() < deadline:
                if select.select([master], [], [], 0.1)[0]:
                    try:
                        data = os.read(master, 65536)
                    except OSError:
                        break
                    if not data:
                        break
                    output.extend(data)
            self.assertEqual(process.wait(timeout=1), 0)
        finally:
            os.close(master)
            if process.poll() is None:
                process.kill()
                process.wait()
        text = output.decode("utf-8")
        self.assertGreater(text.count("\x1b[2K"), 4)
        self.assertIn("░", text)
        self.assertIn("█", text)
        self.assertIn("fragment", text)
        self.assertIn("continued", text)
        self.assertIn("100% · выполнено 3/3", text)

if __name__ == "__main__":
    unittest.main()
