"""Проверяет lifecycle экрана loader без обращения к реальным службам и Plymouth."""

import importlib.machinery
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

# Блок 1: Изолированный импорт helper и временный каталог под transition-сценарием.
ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "runtime-scripts/treed-update/treed-loading-screen"
TEMP_PARENT = ROOT / ".unlazy/loader-transition/test-temp"
_created_temp_dirs = []
parent = TEMP_PARENT
while not parent.exists():
    _created_temp_dirs.append(parent)
    parent = parent.parent
TEMP_PARENT.mkdir(parents=True, exist_ok=True)

loader = importlib.machinery.SourceFileLoader("treed_loading_screen", str(SOURCE))
spec = importlib.util.spec_from_loader(loader.name, loader)
screen = importlib.util.module_from_spec(spec)
try:
    loader.exec_module(screen)
except ImportError:
    screen = None


class LoadingScreenTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        for path in _created_temp_dirs:
            try:
                path.rmdir()
            except OSError:
                break

    def setUp(self):
        if screen is None:
            self.skipTest("fcntl is available only on the Linux target")
        temp = tempfile.TemporaryDirectory(dir=TEMP_PARENT)
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.state_dir = self.root / "run/treed-loading-screen"
        self.theme = self.root / "themes/treed/treed.plymouth"
        self.theme.parent.mkdir(parents=True)
        self.theme.touch()
        self.active_units = {unit: False for unit in screen.UI_UNITS}
        self.state_sequences = {}
        self.deactivating_units = set()
        self.calls = []
        self.ping = True
        self.fail_quit = False
        self.fail_ui_start = False
        self.stack = []
        for name, value in {
            "STATE_DIR": self.state_dir,
            "STATE_FILE": self.state_dir / "state.json",
            "LOCK_FILE": self.state_dir / "lock",
            "THEME_FILE": self.theme,
            "STATE_OWNER_UID": os.getuid(),
        }.items():
            self.enter(patch.object(screen, name, value))
        self.enter(patch.object(screen.os, "geteuid", return_value=0))
        self.enter(patch.object(screen.shutil, "which", return_value="/usr/bin/plymouth"))
        self.enter(patch.object(screen.time, "sleep", return_value=None))
        self.enter(patch.object(screen, "current_vt", return_value="tty2"))
        self.enter(patch.object(screen, "command", side_effect=self.fake_command))
        self.enter(patch.object(screen, "command_output", side_effect=self.fake_command_output))

    def enter(self, context):
        self.stack.append(context)
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)

    def fake_command(self, args, timeout=4, check=False):
        self.calls.append(tuple(args))
        if args[:3] == ["systemctl", "is-active", "--quiet"]:
            return self.active_units[args[3]]
        if args[:3] == ["systemctl", "stop", "--no-block"]:
            self.active_units[args[3]] = False
            if args[3] in self.deactivating_units:
                self.state_sequences[args[3]] = ["deactivating", "inactive"]
            return True
        if args[:3] == ["systemctl", "start", "--no-block"]:
            if self.fail_ui_start:
                return False
            self.active_units[args[3]] = True
            return True
        if args[:2] == ["plymouth", "--ping"]:
            return self.ping
        if args[:2] == ["plymouth", "quit"]:
            if not self.fail_quit:
                self.ping = False
            return not self.fail_quit
        if args[0] == "plymouthd":
            pid_file = Path(next(item.split("=", 1)[1] for item in args if item.startswith("--pid-file=")))
            pid_file.write_text(str(os.getpid()), encoding="ascii")
            self.ping = True
            return True
        return True

    def fake_command_output(self, args, timeout=4):
        self.calls.append(tuple(args))
        if args[:5] == ["systemctl", "show", "-p", "ActiveState", "--value"]:
            unit = args[5]
            sequence = self.state_sequences.get(unit, [])
            if sequence:
                return sequence.pop(0)
            return "active" if self.active_units[unit] else "inactive"
        return ""

    # Блок 2: Сценарии показывают восстановление UI и fail-closed cleanup без systemd/Plymouth.
    def test_start_and_stop_restore_only_previously_active_ui(self):
        self.active_units["treed-shell.service"] = True
        ui_mode = self.root / "treed-ui.env"
        ui_mode.write_text("TREED_UI_MODE=ts\nLOCAL_VALUE=keep\n", encoding="utf-8")

        self.assertEqual(screen.start(), 0)
        state = screen.load_state()
        self.assertTrue(state["wasTreedShellActive"])
        self.assertFalse(state["wasKlipperScreenActive"])
        self.assertTrue(state["daemonModeChanged"])
        self.assertEqual(self.active_units, {"treed-shell.service": False, "KlipperScreen.service": False})
        self.assertLess(self.calls.index(("plymouth", "change-mode", "--updates")),
                        self.calls.index(("plymouth", "show-splash")))

        self.assertEqual(screen.stop(), 0)
        self.assertTrue(self.active_units["treed-shell.service"])
        self.assertFalse(self.active_units["KlipperScreen.service"])
        self.assertIn(("plymouth", "change-mode", "--boot"), self.calls)
        self.assertNotIn(("plymouth", "quit"), self.calls)
        self.assertEqual(ui_mode.read_text(encoding="utf-8"), "TREED_UI_MODE=ts\nLOCAL_VALUE=keep\n")
        self.assertFalse(screen.STATE_FILE.exists())
        self.assertFalse(any(args[0] == "systemctl" and args[-1] in {"klipper.service", "moonraker.service"}
                             for args in self.calls))

    def test_owned_daemon_is_quit_only_while_private_pid_matches(self):
        self.ping = False
        self.enter(patch.object(screen, "pid_alive", side_effect=[True, False, False]))

        self.assertEqual(screen.start(), 0)
        state = screen.load_state()
        self.assertTrue(state["daemonOwned"])
        self.assertEqual(state["daemonPid"], os.getpid())
        self.assertEqual(screen.stop(), 0)
        self.assertIn(("plymouth", "quit"), self.calls)

    def test_owned_daemon_with_replaced_private_pid_is_left_running(self):
        self.ping = False
        self.enter(patch.object(screen, "pid_alive", return_value=True))
        self.assertEqual(screen.start(), 0)
        self.enter(patch.object(screen, "private_pid", return_value=os.getpid() + 1))

        self.assertEqual(screen.stop(), 1)
        self.assertNotIn(("plymouth", "quit"), self.calls)
        self.assertTrue(screen.STATE_FILE.exists())

    def test_ui_handoff_that_already_quit_owned_daemon_cleans_state(self):
        self.ping = False
        self.enter(patch.object(screen, "pid_alive", return_value=True))
        self.assertEqual(screen.start(), 0)
        (self.state_dir / "plymouthd.pid").unlink()
        self.enter(patch.object(screen, "pid_alive", return_value=False))

        self.assertEqual(screen.stop(), 0)
        self.assertNotIn(("plymouth", "quit"), self.calls)
        self.assertFalse(screen.STATE_FILE.exists())

    def test_owned_daemon_is_quit_when_ui_restarted_during_loader(self):
        self.ping = False
        self.enter(patch.object(screen, "pid_alive", side_effect=[True, False, False]))
        self.assertEqual(screen.start(), 0)
        self.active_units["treed-shell.service"] = True

        self.assertEqual(screen.stop(), 0)
        self.assertIn(("plymouth", "quit"), self.calls)
        self.assertTrue(self.active_units["treed-shell.service"])
        self.assertFalse(screen.STATE_FILE.exists())

    def test_plymouth_starts_after_ui_reaches_inactive(self):
        self.active_units["treed-shell.service"] = True
        self.deactivating_units.add("treed-shell.service")

        self.assertEqual(screen.start(), 0)
        state_calls = [index for index, args in enumerate(self.calls)
                       if args[:5] == ("systemctl", "show", "-p", "ActiveState", "--value")]
        self.assertGreaterEqual(len(state_calls), 2)
        self.assertGreater(self.calls.index(("chvt", "1")), state_calls[-1])
        self.assertEqual(self.state_sequences["treed-shell.service"], [])
        self.assertGreater(self.calls.index(("plymouth", "show-splash")), state_calls[-1])

    def test_stop_without_state_is_idempotent(self):
        self.assertEqual(screen.stop(), 0)
        self.assertEqual(self.calls, [])

    def test_failed_cleanup_preserves_state_for_retry_and_returns_failure(self):
        self.active_units["treed-shell.service"] = True
        self.ping = False
        self.fail_quit = True
        self.fail_ui_start = True
        self.enter(patch.object(screen, "pid_alive", return_value=True))
        self.enter(patch.object(screen, "SERVICE_TIMEOUT", 0))

        self.assertEqual(screen.start(), 0)
        self.assertEqual(screen.stop(), 1)
        self.assertTrue(screen.STATE_FILE.exists())


if __name__ == "__main__":
    unittest.main()
