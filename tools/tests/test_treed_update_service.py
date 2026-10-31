"""Изолированные проверки root update queue и persistent operation state."""

import contextlib
import configparser
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SERVICE_PATH = ROOT / "runtime-scripts/treed-update/treed-update-service"
service = types.ModuleType("treed_update_service")
sys.modules[service.__name__] = service
service.__file__ = str(SERVICE_PATH)
exec(compile(SERVICE_PATH.read_text(encoding="utf-8"), str(SERVICE_PATH), "exec"), service.__dict__)


class FakeLock:
    def fileno(self):
        return 42

    def close(self):
        pass


class UpdateServiceTests(unittest.TestCase):
    def test_v034_ui_installer_accepts_new_package_updater_envelope(self):
        if os.name == "nt" or not shutil.which("bash"):
            self.skipTest("Исторический UI installer требует Linux Bash")
        revision = "de76ff54c3a66db8a860394b8e6b44e250338e32"
        source = subprocess.check_output([
            "git", "-C", str(ROOT), "show", f"{revision}:runtime-scripts/treed-update/treed-update-apply"
        ], text=True, timeout=10)
        start = source.index("apply_shell_update() {")
        end = source.index("\n}\n", start) + len("\n}")
        installer = source[start:end].replace("/usr/local/sbin/treed-update-service", "phase_stub")
        runtime = self.root / "legacy-ui"
        current = runtime / "ui"
        current.mkdir(parents=True)
        (current / "index.html").write_bytes(b"prior UI")
        package = self.root / "ui-package.zip"
        code = b"# package-owned updater\n"
        manifest = dict(name="treed-shell-ui", mode="live", sha="a" * 40, runNumber=123, runAttempt=1,
                        updater=dict(protocol=1, entrypoint=".treed-update/updater.py",
                                     sha256=hashlib.sha256(code).hexdigest()))
        with zipfile.ZipFile(package, "w") as bundle:
            bundle.writestr("index.html", b"new UI")
            bundle.writestr("treed-shell-ui-manifest.json", json.dumps(manifest))
            bundle.writestr(".treed-update/updater.py", code)
        environment = dict(os.environ, SHELL_RUNTIME_DIR=str(runtime), SHELL_WEB_DIR=str(current),
                           SHELL_ARCHIVE_PATH=str(runtime / "treed-shell-ui.zip"), TARGET_TAG="ui-main-123-1",
                           PI_USER="fixture", PI_GROUP="fixture", SHELL_SERVICE="fixture.service",
                           PACKAGE=str(package), TREED_UPDATE_OPERATION_ID=str(uuid.uuid4()),
                           DIGEST="sha256:" + hashlib.sha256(package.read_bytes()).hexdigest())
        # Только внешние границы: release/download, systemd, account и phase CLI.
        boundaries = '''
phase_stub() { :; }
resolve_shell_asset_url() { printf 'https://fixture.invalid/package.zip\t%s\n' "$DIGEST"; }
curl() { while [ "$1" != '-o' ]; do shift; done; cp "$PACKAGE" "$2"; }
systemctl() { :; }
chown() { :; }
wait_shell_ready() { return 0; }
'''
        result = subprocess.run(["bash", "-c", "set -euo pipefail\n" + boundaries + installer + "\napply_shell_update\n"],
                                env=environment, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((current / "index.html").read_bytes(), b"new UI")
        self.assertEqual((runtime / "ui.previous/index.html").read_bytes(), b"prior UI")
        self.assertEqual((runtime / "treed-shell-ui.zip").read_bytes(), package.read_bytes())

    def test_standalone_environment_keeps_custom_lock_for_retry_worker(self):
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("Bash недоступен для проверки updater environment")
        source = (ROOT / "loader/steps/moonraker-config.sh").read_text(encoding="utf-8")
        start = source.index("write_treed_update_environment() {")
        end = source.index("\n}\n", start) + len("\n}")
        function = source[start:end]
        apply_source = (ROOT / "runtime-scripts/treed-update/treed-update-apply").read_text(encoding="utf-8")
        reader_start = apply_source.index("load_treed_update_environment() {")
        reader_end = apply_source.index("\n}\n", reader_start) + len("\n}")
        reader = apply_source[reader_start:reader_end]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lock_component = 'custom lock "quoted" \\backslash $HOME ' + chr(96)
            lock_component += "touch tick-sentinel" + chr(96) + " $(touch dollar-sentinel)"
            lock_path = root / lock_component / "update.lock"
            lock_path.parent.mkdir(parents=True)
            lock_path.touch()
            env_file = root / "treed-update.env"
            env = dict(os.environ, TREED_UPDATE_LOCK_FILE=str(lock_path), TREED_UPDATE_ENV_FILE=str(env_file),
                       TREED_UPDATE_MANAGED_LOADER="0",
                       PI_HOME=str(root), PI_USER="pi", PI_GROUP="pi", MOONRAKER_RUNTIME_DIR=str(root / "moonraker"))
            worker_probe = (
                "import runpy; "
                f"print(runpy.run_path({str(SERVICE_PATH)!r})['LOCK'])"
            )
            script = ("set -e\nENV_FILE=\"$TREED_UPDATE_ENV_FILE\"\n"
                      "log_error() { :; }\nlog_info() { :; }\n" + function + "\n" + reader
                      + '\nwrite_treed_update_environment "$TREED_UPDATE_ENV_FILE"\n'
                      + "\nload_treed_update_environment\n"
                      + f"\npython3 -c {shlex.quote(worker_probe)}\n")
            result = subprocess.run([bash, "-c", script], cwd=root, env=env,
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), str(lock_path))
            self.assertFalse((root / "tick-sentinel").exists())
            self.assertFalse((root / "dollar-sentinel").exists())
            lock_line = next(line for line in env_file.read_text(encoding="utf-8").splitlines()
                             if line.startswith("TREED_UPDATE_LOCK_FILE="))
            loaded_path = shlex.split(lock_line.partition("=")[2])[0]
            self.assertEqual(loaded_path, str(lock_path))
            retry_unit = SERVICE_PATH.with_name("treed-update-retry.service").read_text(encoding="utf-8")
            self.assertIn("EnvironmentFile=-/etc/default/treed-update", retry_unit)
            worker_lock_path = Path(result.stdout.strip())
            self.assertEqual(worker_lock_path, lock_path)
            self.assertEqual(os.stat(worker_lock_path).st_ino, os.stat(lock_path).st_ino)
            malformed_env = root / "malformed.env"
            malformed_env.write_text('TREED_UPDATE_LOCK_FILE="unterminated\n', encoding="utf-8")
            bad_script = 'ENV_FILE="$1"\n' + reader + "\nload_treed_update_environment\n"
            rejected = subprocess.run([bash, "-c", bad_script, "bash", str(malformed_env)], cwd=root,
                                      capture_output=True, text=True, timeout=10)
            self.assertNotEqual(rejected.returncode, 0)

    def test_loader_preserves_account_config_before_moonraker_merge(self):
        git = shutil.which("git")
        bash = str(Path(git).parents[1] / "bin/bash.exe") if sys.platform == "win32" and git else shutil.which("bash")
        if not bash or not Path(bash).is_file():
            self.skipTest("Bash недоступен для проверки сохранения аккаунта")
        klipper = (ROOT / "loader/steps/klipper-core.sh").read_text(encoding="utf-8")
        rebuild = klipper.split("# Блок 5:", 1)[1].split("# Блок 6:", 1)[0].split("\n", 1)[1]
        moonraker = (ROOT / "loader/steps/moonraker-config.sh").read_text(encoding="utf-8")
        merge_start = moonraker.index("merge_preserved_autoprinter_section() {")
        merge = moonraker[merge_start:moonraker.index("\n}\n", merge_start) + len("\n}")]
        deploy = "deploy_base_fragments() {" + moonraker.split("deploy_base_fragments() {", 1)[1].split("\nprune_treed_generated_fragments()", 1)[0]
        old = "[treed_autoprinter]\nprinter_id: permanent-device\napi_origin: https://device.example\n"
        defaults = "[treed_autoprinter]\nprinter_id:\napi_origin:\nbed_check_socket: /private/vision.sock\n"
        for mode in ("preserve", "clean"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                config = root / "config"
                (config / "moonraker/base").mkdir(parents=True)
                (config / "moonraker/base/00-core.conf").write_text(old, encoding="utf-8")
                (config / "moonraker.conf").write_text("old entrypoint\n", encoding="utf-8")
                (config / "printer.cfg").write_text("old printer config\n", encoding="utf-8")
                (root / "stage").mkdir()
                (root / "stage/printer.cfg").write_text("new printer config\n", encoding="utf-8")
                (root / "source").mkdir()
                (root / "source/00-core.conf").write_text(defaults, encoding="utf-8")
                prelude = ('set -e\nTMPDIR="$PWD"\nCONFIG_DIR=./config\nSTAGE_DIR=./stage\n'
                           'SRC_BASE_DIR=./source\nDST_BASE_DIR=./config/moonraker/base\n'
                           'PI_USER=unused\ngrp=unused\n'
                           'ensure_dir() { mkdir -p "$1"; }\n'
                           'render_base_fragment_templates() { :; }\n'
                           'configure_mainsail_updater_section() { :; }\n'
                           'configure_crowsnest_updater_section() { :; }\n'
                           'log_info() { :; }\nchown() { :; }\n')
                prelude += f'python3() {{ "{Path(sys.executable).as_posix()}" "$@"; }}\n'
                # Обе настоящие функции выполняются в порядке loader, системные операции изолированы.
                script = prelude + f"DEPLOY_MODE={mode}\n" + rebuild + "\n" + merge + "\n" + deploy + "\ndeploy_base_fragments\n"
                if mode == "preserve":
                    interrupted = script.replace("\ndeploy_base_fragments\n",
                        '\ncp(){ if [ "$2" = "./source/." ]; then return 1; fi; command cp "$@"; }\ndeploy_base_fragments\n')
                    failed = subprocess.run([bash, "-c", interrupted], cwd=temp, capture_output=True, timeout=30)
                    self.assertNotEqual(0, failed.returncode)
                    self.assertEqual(old, (config / "moonraker/base.preserve-core.conf").read_text())
                result = subprocess.run([bash, "-c", script], cwd=temp, capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
                final = (config / "moonraker/base/00-core.conf").read_text(encoding="utf-8")
                self.assertIn("bed_check_socket: /private/vision.sock", final)
                self.assertEqual("printer_id: permanent-device" in final, mode == "preserve")
                self.assertEqual("api_origin: https://device.example" in final, mode == "preserve")
                self.assertEqual((config / "moonraker.conf").exists(), mode == "preserve")
                self.assertEqual((config / "printer.cfg").read_text(), "new printer config\n")

    def test_override_copy_failure_aborts_before_runtime_rebuild(self):
        git = shutil.which("git")
        bash = str(Path(git).parents[1] / "bin/bash.exe") if sys.platform == "win32" and git else shutil.which("bash")
        if not bash or not Path(bash).is_file():
            self.skipTest("Bash недоступен для проверки сохранения override")
        loader = (ROOT / "loader/steps/klipper-core.sh").read_text(encoding="utf-8")
        block = loader.split("# Блок 4:", 1)[1].split("# Блок 5:", 1)[0].split("\n", 1)[1]
        # Достижение следующего блока означает разрешение на полную раскладку runtime.
        for fails in (False, True):
            with self.subTest(copy_fails=fails), tempfile.TemporaryDirectory() as temp:
                config = Path(temp) / "config"
                config.mkdir()
                override = config / "local_overrides.cfg"
                override.write_bytes(b"[extruder]\npressure_advance: 0.123\n")
                prelude = 'set -e\nTMPDIR="$PWD"\nDEPLOY_MODE=preserve\nCONFIG_DIR=./config\nlog_info() { :; }\nlog_error() { :; }\n'
                if fails:
                    prelude += 'cp() { return 1; }\n'
                script = prelude + block + '\ntouch rebuild_allowed\n'
                result = subprocess.run([bash, "-c", script], cwd=temp, capture_output=True)
                self.assertEqual(result.returncode, 1 if fails else 0, result.stderr)
                self.assertEqual((Path(temp) / "rebuild_allowed").exists(), not fails)
                self.assertEqual(override.read_bytes(), b"[extruder]\npressure_advance: 0.123\n")

    def test_printer_guard_ignores_stale_flags_but_rejects_active_and_unknown_jobs(self):
        for state, expected in ((" CANCELLED ", True), ("complete", True), ("standby", True),
                                ("error", True), ("paused", False), ("printing", False),
                                ("unknown", False), ("", False), (None, False)):
            payload = {"result": {"status": {"print_stats": {"state": state},
                                            "pause_resume": {"is_paused": True}}}}
            with self.subTest(state=state), \
                 patch.object(service.urllib.request, "urlopen", return_value=io.BytesIO(json.dumps(payload).encode())):
                self.assertEqual(service.printer_is_idle(), expected)

    def test_path_unit_watches_state_file_without_creating_a_directory(self):
        config = configparser.ConfigParser()
        config.read(SERVICE_PATH.with_name("treed-update.path"), encoding="utf-8")
        self.assertEqual(config["Path"]["PathChanged"], "/var/lib/treed-update/state.json")
        self.assertFalse(config["Path"].getboolean("MakeDirectory", fallback=False))

    def test_retry_units_are_short_monotonic_and_independent_of_moonraker(self):
        service_unit = configparser.ConfigParser(strict=False)
        service_unit.read(SERVICE_PATH.with_name("treed-update-retry.service"), encoding="utf-8")
        timer = configparser.ConfigParser()
        timer.read(SERVICE_PATH.with_name("treed-update-retry.timer"), encoding="utf-8")
        self.assertEqual(service_unit["Service"]["ExecStart"],
                         "/usr/local/sbin/treed-update-service retry-due")
        self.assertEqual(service_unit["Service"]["Type"], "oneshot")
        self.assertTrue(timer["Timer"].get("OnBootSec"))
        self.assertTrue(timer["Timer"].get("OnUnitInactiveSec"))
        self.assertFalse(timer["Timer"].getboolean("Persistent", fallback=False))

    def test_retry_bootstrap_accepts_unit_owned_intervals_and_rejects_invalid_contracts(self):
        bash = shutil.which("bash")
        grep = shutil.which("grep")
        if not bash or not grep:
            self.skipTest("Bash недоступен для изолированной проверки retry bootstrap")
        bootstrap = (ROOT / "loader/steps/update-recovery-bootstrap.sh").read_text(encoding="utf-8")
        start = bootstrap.index("validate_source() {")
        end = bootstrap.index("\n\ninstall_unit()", start)
        validation = bootstrap[start:end]

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            service_file = root / "treed-update-retry.service"
            timer_file = root / "treed-update-retry.timer"
            service_file.write_text(
                "[Service]\nExecStart=/usr/local/sbin/treed-update-service retry-due\n", encoding="utf-8")
            timer_file.write_text(
                "[Timer]\nOnBootSec=75s\nOnUnitInactiveSec=2min\n"
                "Unit=treed-update-retry.service\n", encoding="utf-8")
            no_analyzer = root / "no-analyzer"
            with_analyzer = root / "with-analyzer"
            no_analyzer.mkdir()
            with_analyzer.mkdir()
            for bin_dir in (no_analyzer, with_analyzer):
                (bin_dir / "grep").symlink_to(grep)
            analyzer = with_analyzer / "systemd-analyze"
            analyzer.write_text("#!/bin/sh\nexit \"${SYSTEMD_ANALYZE_RESULT:-0}\"\n", encoding="utf-8")
            os.chmod(analyzer, 0o755)
            prelude = (
                "systemctl() {\n"
                "  case \"$1\" in\n"
                "    daemon-reload) return 0 ;;\n"
                "    show)\n"
                "      if [ \"${SYSTEMCTL_MODE:-}\" = bad-load-state ] && \\\n"
                "         [ \"$4\" = treed-update-retry.timer ]; then echo not-found; else echo loaded; fi\n"
                "      ;;\n"
                "    enable) [ \"${SYSTEMCTL_MODE:-}\" != enable-fail ] ;;\n"
                "    *) return 1 ;;\n"
                "  esac\n"
                "}\n"
                f"service_src='{service_file.as_posix()}'\n"
                f"timer_src='{timer_file.as_posix()}'\n"
                f"{validation}\n"
            )

            def run(action: str, bin_dir: Path = no_analyzer, **values: str) -> subprocess.CompletedProcess:
                env = {**os.environ, **values, "PATH": str(bin_dir)}
                return subprocess.run([bash, "-c", f"{prelude}\n{action}"], env=env,
                                      capture_output=True, timeout=10)

            self.assertEqual(run("validate_units").returncode, 0)
            self.assertEqual(run("activate_retry_timer").returncode, 0)
            self.assertEqual(run("validate_units", with_analyzer, SYSTEMD_ANALYZE_RESULT="0").returncode, 0)
            self.assertEqual(run("validate_units", with_analyzer, SYSTEMD_ANALYZE_RESULT="1").returncode, 1)
            self.assertEqual(run("activate_retry_timer", SYSTEMCTL_MODE="bad-load-state").returncode, 1)
            self.assertEqual(run("activate_retry_timer", SYSTEMCTL_MODE="enable-fail").returncode, 1)
            timer_file.write_text(
                "[Timer]\nOnBootSec=75s\nUnit=treed-update-retry.service\n", encoding="utf-8")
            self.assertEqual(run("validate_units").returncode, 1)
            timer_file.write_text(
                "[Timer]\nOnBootSec=75s\nOnUnitInactiveSec=2min\nUnit=wrong.service\n", encoding="utf-8")
            self.assertEqual(run("validate_units").returncode, 1)
            service_file.write_text("[Service]\nExecStart=/usr/bin/true\n", encoding="utf-8")
            self.assertEqual(run("validate_units").returncode, 1)
            service_file.unlink()
            self.assertEqual(run("validate_units").returncode, 1)

    def test_loader_repairs_only_empty_state_directory(self):
        git = shutil.which("git")
        bash = str(Path(git).parents[1] / "bin/bash.exe") if sys.platform == "win32" and git else shutil.which("bash")
        if not bash or not Path(bash).is_file():
            self.skipTest("Bash недоступен для проверки миграции loader")
        loader = (ROOT / "loader/steps/moonraker-config.sh").read_text(encoding="utf-8")
        block = loader.split("  # Блок 3.2:", 1)[1].split("  systemctl daemon-reload", 1)[0]
        block = block[block.index("  if "):].replace("/var/lib/treed-update/state.json", "./state.json")
        script = "set -e\nsystemctl() { echo \"$*\" >> calls; }\nlog_error() { :; }\nlog_warn() { :; }\n" + block
        for kind in ("missing", "file", "empty", "nonempty"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp:
                state = Path(temp) / "state.json"
                if kind == "file":
                    state.write_text('{"history": []}', encoding="utf-8")
                elif kind in ("empty", "nonempty"):
                    state.mkdir()
                    if kind == "nonempty":
                        (state / "keep.json").write_text("сохранить", encoding="utf-8")
                result = subprocess.run([bash, "-c", script], cwd=temp, capture_output=True)
                self.assertEqual(result.returncode, 1 if kind == "nonempty" else 0)
                if kind == "empty":
                    self.assertFalse(state.exists())
                    self.assertEqual((Path(temp) / "calls").read_text().strip(), "stop treed-update.path")
                elif kind == "nonempty":
                    self.assertEqual((state / "keep.json").read_text(encoding="utf-8"), "сохранить")
                elif kind == "file":
                    self.assertEqual(state.read_text(encoding="utf-8"), '{"history": []}')
                    self.assertFalse((Path(temp) / "calls").exists())
                else:
                    self.assertFalse(state.exists())

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        service.STATE = root / "state.json"
        service.LOCK = root / "lock"
        service.LOG = root / "worker.log"
        service.fcntl = types.SimpleNamespace(LOCK_EX=1, LOCK_NB=2, LOCK_UN=4,
                                               flock=lambda *_args: None)
        self.root = root
        fchmod = patch.object(service.os, "fchmod", lambda *_args: None, create=True)
        fchmod.start()
        self.addCleanup(fchmod.stop)
        self.loading_screen = patch.object(service, "loading_screen")
        self.loading_screen.start()
        self.addCleanup(self.loading_screen.stop)
        self.package_execution = patch.object(service, "package_execution", return_value=None)
        self.package_execution.start()
        self.addCleanup(self.package_execution.stop)

    def test_loader_completion_closes_failure_and_preserves_pending_ui(self):
        for pending_ui in (None, "ui-main-124-1"):
            with self.subTest(pending_ui=pending_ui):
                state = dict(operationId="operation-loader", requestId="request-loader", targetId="printer-core",
                             targetTag="v0.34.1", status="error", busy=False, resumeCore=True,
                             resultCode="provisioning_failed", history=[])
                if pending_ui:
                    state["pendingUiTag"] = pending_ui
                service.write_state(state)
                with patch.object(service.os, "geteuid", return_value=0, create=True), \
                     patch.object(service, "acquire", return_value=FakeLock()), \
                     patch.object(service.subprocess, "run", return_value=types.SimpleNamespace(
                         returncode=0, stdout='{"tag":"v0.35.1"}', stderr="")):
                    self.assertEqual(service.main(["treed-update-service", "complete-loader"]), 0)
                result = service.read_state()
                self.assertEqual(result["operationId"], state["operationId"])
                self.assertNotIn("resumeCore", result)
                self.assertEqual(result["targetTag"], pending_ui or "v0.35.1")
                self.assertEqual(result["status"], "queued" if pending_ui else "applied")
                self.assertEqual(result["busy"], bool(pending_ui))
                if not pending_ui:
                    self.assertEqual(result["history"][-1]["resultCode"], "core_updated_via_loader")

    def test_loader_completion_cannot_override_busy_or_unverified_state(self):
        state = dict(operationId="operation-loader", targetId="printer-core", targetTag="v0.34.1",
                     status="error", busy=False, resultCode="provisioning_failed")
        cases = (
            ({**state, "busy": True, "status": "installing"}, 0, '{"tag":"v0.35.1"}'),
            (state, 1, '{"tag":"v0.35.1"}'),
            (state, 0, '{"tag":"v0.33.1"}'),
            (state, 0, '{}'),
        )
        for original, code, payload in cases:
            with self.subTest(original=original, code=code, payload=payload):
                service.write_state(original)
                with patch.object(service.os, "geteuid", return_value=0, create=True), \
                     patch.object(service, "acquire", return_value=FakeLock()), \
                     patch.object(service.subprocess, "run", return_value=types.SimpleNamespace(
                         returncode=code, stdout=payload, stderr="verification failed")):
                    with self.assertRaises((RuntimeError, ValueError)):
                        service.main(["treed-update-service", "complete-loader"])
                self.assertEqual(service.read_state(), original)

    def test_loader_completion_requires_root_and_exclusive_lock(self):
        state = {"targetId": "printer-core", "status": "error", "resultCode": "provisioning_failed"}
        service.write_state(state)
        with patch.object(service.os, "geteuid", return_value=1000, create=True):
            with self.assertRaisesRegex(RuntimeError, "root"):
                service.complete_loader()
        with patch.object(service.os, "geteuid", return_value=0, create=True), \
             patch.object(service, "acquire", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "активного обновления"):
                service.complete_loader()
        self.assertEqual(service.read_state(), state)

    def test_loader_completion_borrows_inherited_lock_without_unlocking_it(self):
        state = {"operationId": "borrowed-op", "targetId": "printer-core", "targetTag": "v0.34.1",
                 "status": "error", "busy": False, "resultCode": "provisioning_failed"}
        service.write_state(state)
        lock_stat = types.SimpleNamespace(st_mode=service.stat.S_IFREG, st_dev=1, st_ino=2)
        calls = []
        with patch.dict(service.os.environ, {"TREED_UPDATE_LOCK_FD": "42"}), \
                patch.object(service.os, "geteuid", return_value=0, create=True), \
                patch.object(service.os, "fstat", return_value=lock_stat, create=True), \
                patch.object(service, "LOCK", types.SimpleNamespace(stat=lambda: lock_stat)), \
                patch.object(service.fcntl, "flock", side_effect=lambda *args: calls.append(args)), \
                patch.object(service.subprocess, "run", return_value=types.SimpleNamespace(
                    returncode=0, stdout='{"tag":"v0.35.1"}', stderr="")):
            self.assertEqual(service.complete_loader(), 0)
        self.assertEqual(calls, [(42, service.fcntl.LOCK_EX | service.fcntl.LOCK_NB)])
        self.assertEqual(service.read_state()["status"], "applied")

    def test_loader_completion_rejects_unrelated_inherited_lock_descriptor(self):
        state = {"operationId": "borrowed-op", "targetId": "printer-core", "targetTag": "v0.34.1",
                 "status": "error", "busy": False, "resultCode": "provisioning_failed"}
        service.write_state(state)
        lock_stat = types.SimpleNamespace(st_mode=service.stat.S_IFREG, st_dev=1, st_ino=2)
        other_stat = types.SimpleNamespace(st_mode=service.stat.S_IFREG, st_dev=1, st_ino=3)
        with patch.dict(service.os.environ, {"TREED_UPDATE_LOCK_FD": "42"}), \
                patch.object(service.os, "geteuid", return_value=0, create=True), \
                patch.object(service.os, "fstat", return_value=other_stat, create=True), \
                patch.object(service, "LOCK", types.SimpleNamespace(stat=lambda: lock_stat)), \
                patch.object(service.subprocess, "run") as verify:
            with self.assertRaisesRegex(RuntimeError, "does not match"):
                service.complete_loader()
        verify.assert_not_called()
        self.assertEqual(service.read_state(), state)

    def test_loader_completion_fails_when_inherited_lock_is_not_exclusive(self):
        state = {"operationId": "borrowed-op", "targetId": "printer-core", "targetTag": "v0.34.1",
                 "status": "error", "busy": False, "resultCode": "provisioning_failed"}
        service.write_state(state)
        lock_stat = types.SimpleNamespace(st_mode=service.stat.S_IFREG, st_dev=1, st_ino=2)
        with patch.dict(service.os.environ, {"TREED_UPDATE_LOCK_FD": "42"}), \
                patch.object(service.os, "geteuid", return_value=0, create=True), \
                patch.object(service.os, "fstat", return_value=lock_stat, create=True), \
                patch.object(service, "LOCK", types.SimpleNamespace(stat=lambda: lock_stat)), \
                patch.object(service.fcntl, "flock", side_effect=BlockingIOError), \
                patch.object(service.subprocess, "run") as verify:
            with self.assertRaisesRegex(RuntimeError, "not held exclusively"):
                service.complete_loader()
        verify.assert_not_called()
        self.assertEqual(service.read_state(), state)

    def test_submit_is_idempotent_and_does_not_replace_active_operation(self):
        command = ["11111111-1111-4111-8111-111111111111", "printer-ui", "ui-main-123-1"]
        output = io.StringIO()
        with patch.object(service.os, "geteuid", return_value=0, create=True), \
             patch.object(service, "acquire", return_value=FakeLock()), \
             patch.object(service.subprocess, "run") as start_service, \
             contextlib.redirect_stdout(output):
            self.assertEqual(service.submit(*command), 0)
            first = json.loads(output.getvalue())
            output.seek(0); output.truncate(0)
            self.assertEqual(service.submit(*command), 0)
            duplicate = json.loads(output.getvalue())
            output.seek(0); output.truncate(0)
            other = ["22222222-2222-4222-8222-222222222222", "printer-ui", "ui-main-124-1"]
            self.assertEqual(service.submit(*other), 4)
            busy = json.loads(output.getvalue())

        persisted = json.loads(service.STATE.read_text(encoding="utf-8"))
        self.assertEqual(first["operationId"], duplicate["operationId"])
        self.assertTrue(duplicate["duplicate"])
        self.assertFalse(busy["accepted"])
        self.assertEqual(persisted["requestId"], command[0])
        self.assertEqual(persisted["operationId"], first["operationId"])
        start_service.assert_called_once()

    def test_scheduled_retry_rejects_a_new_goal_without_changing_saved_consent(self):
        state = {"operationId": "11111111-1111-4111-8111-111111111111",
                 "requestId": "22222222-2222-4222-8222-222222222222", "targetId": "printer-core",
                 "targetTag": "v0.2.0", "pendingUiTag": "ui-main-123-1",
                 "resetConfigs": [{"path": "config/printer.cfg", "sha256": "a" * 64}],
                 "status": "error", "busy": False, "resultCode": "provisioning_failed",
                 "retry": {"attempts": 1, "scheduledAt": "2026-10-24T00:00:00Z",
                           "nextAttemptAt": "2099-01-01T00:00:30Z", "exhausted": False,
                           "lastResultCode": "provisioning_failed"}}
        service.write_state(state)
        before = service.STATE.read_bytes()
        output = io.StringIO()
        with patch.object(service.os, "geteuid", return_value=0, create=True), \
                patch.object(service, "acquire", return_value=FakeLock()), \
                patch.object(service.subprocess, "run") as start, \
                contextlib.redirect_stdout(output):
            self.assertEqual(service.submit("33333333-3333-4333-8333-333333333333",
                                            "printer-ui", "ui-main-124-1"), 4)
        response = json.loads(output.getvalue())
        self.assertFalse(response["accepted"])
        self.assertEqual(response["targetTag"], "v0.2.0")
        self.assertEqual(service.STATE.read_bytes(), before)
        start.assert_not_called()

    def test_newer_core_replacement_uses_new_operation_and_keeps_old_journal_and_consent_audit(self):
        old_id = "11111111-1111-4111-8111-111111111111"
        state = {"operationId": old_id, "requestId": "22222222-2222-4222-8222-222222222222",
                 "targetId": "printer-core", "targetTag": "v0.2.0", "pendingUiTag": "ui-main-123-1",
                 "resetConfigs": [{"path": "config/printer.cfg", "sha256": "a" * 64}],
                 "status": "error", "busy": False, "resultCode": "provisioning_failed",
                 "retry": {"attempts": 1, "scheduledAt": "2026-10-24T00:00:00Z",
                           "nextAttemptAt": "2099-01-01T00:00:30Z", "exhausted": False,
                           "lastResultCode": "provisioning_failed"}}
        core_dir = self.root / "core"
        journal_dir = core_dir / old_id
        journal_dir.mkdir(parents=True)
        journal_bytes = b'{"schema":1,"stage":"provisioning_failed","provisioningStarted":true,"entries":[]}'
        manifest_bytes = json.dumps({"schema": 1, "kind": "treed-core-runtime", "version": "0.2.0",
                                     "files": [{"path": "sbin/treed-core-update",
                                                "runtimeSha256": "b" * 64}]}).encode()
        source_bytes = b"unchanged prior release archive"
        (journal_dir / "journal.json").write_bytes(journal_bytes)
        (journal_dir / "manifest.new.json").write_bytes(manifest_bytes)
        (journal_dir / "treed-core-runtime.zip").write_bytes(source_bytes)
        service.write_state(state)
        selection = [{"path": "config/printer.cfg", "sha256": "c" * 64}]
        with patch.dict(service.os.environ, {"TREED_CORE_STATE_DIR": str(core_dir)}), \
             patch.object(service.os, "geteuid", return_value=0, create=True), \
             patch.object(service, "acquire", return_value=FakeLock()), \
             patch.object(service.subprocess, "run"), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, service.submit("33333333-3333-4333-8333-333333333333",
                                               "printer-core", "v0.3.0", None, json.dumps(selection)))
        replacement = service.read_state()
        self.assertNotEqual(old_id, replacement["operationId"])
        self.assertEqual(replacement["targetTag"], "v0.3.0")
        self.assertEqual(replacement["supersedesOperationId"], old_id)
        self.assertEqual(replacement["supersedesTargetTag"], "v0.2.0")
        self.assertEqual(replacement["supersededResetConfigs"], state["resetConfigs"])
        self.assertEqual(replacement["resetConfigs"], selection)
        self.assertEqual(replacement["pendingUiTag"], state["pendingUiTag"])
        self.assertNotIn("resumeCore", replacement)
        self.assertFalse((core_dir / replacement["operationId"] / "journal.json").exists())
        self.assertEqual((journal_dir / "journal.json").read_bytes(), journal_bytes)
        self.assertEqual((journal_dir / "manifest.new.json").read_bytes(), manifest_bytes)
        self.assertEqual((journal_dir / "treed-core-runtime.zip").read_bytes(), source_bytes)

    def test_packaged_error_accepts_newer_executor_without_parsing_old_journal(self):
        old_id = "11111111-1111-4111-8111-111111111111"
        state = dict(operationId=old_id, targetId="printer-core", targetTag="v0.2.0",
                     status="error", busy=False, resultCode="core_validation_failed",
                     lastFailurePhase="downloading", resetConfigs=[], history=[])
        state["packageUpdater"] = {key: state[key] for key in ("operationId", "targetId", "targetTag")}
        service.write_state(state)
        with patch.object(service.os, "geteuid", return_value=0, create=True), \
             patch.object(service, "acquire", return_value=FakeLock()), \
             patch.object(service.subprocess, "run"), contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(service.core_supersede_eligible(state, "v0.3.0"))
            service.submit(str(uuid.uuid4()), "printer-core", "v0.3.0")
            replacement = service.read_state()
            self.assertEqual(replacement["supersedesOperationId"], old_id)
            self.assertTrue(replacement["supersedesPackagedUpdater"])
            replacement["packageUpdater"] = {key: replacement[key]
                                            for key in ("operationId", "targetId", "targetTag")}
            service.finish(replacement, "error", "core_validation_failed", "broken target updater")
            service.submit(str(uuid.uuid4()), "printer-core", "v0.4.0")
        self.assertEqual(service.read_state()["supersedesOperationId"], old_id)
        self.assertEqual(service.read_state()["supersedesTargetTag"], "v0.2.0")

    def test_retry_preserves_package_pin_identity_even_without_core_journal(self):
        state = dict(operationId=str(uuid.uuid4()), targetId="printer-core", targetTag="v0.2.0",
                     status="error", busy=False, resultCode="core_validation_failed", history=[])
        state["packageUpdater"] = {key: state[key] for key in ("operationId", "targetId", "targetTag")}
        service.write_state(state)
        with patch.object(service.os, "geteuid", return_value=0, create=True), \
             patch.object(service, "acquire", return_value=FakeLock()), \
             patch.object(service.subprocess, "run"), contextlib.redirect_stdout(io.StringIO()):
            service.submit(str(uuid.uuid4()), "printer-core", "v0.2.0")
        resumed = service.read_state()
        self.assertEqual(resumed["operationId"], state["operationId"])
        self.assertEqual(resumed["packageUpdater"], state["packageUpdater"])
        self.assertTrue(resumed["resumeCore"])

    def test_prejournal_corrective_download_failure_keeps_physical_anchor_for_next_newer_goal(self):
        old_id = "11111111-1111-4111-8111-111111111111"
        failed_id = "33333333-3333-4333-8333-333333333333"
        core_dir = self.root / "core"
        old_dir = core_dir / old_id
        old_dir.mkdir(parents=True)
        old_journal = b'{"schema":1,"stage":"provisioning_failed","provisioningStarted":true}'
        old_manifest = json.dumps({
            "schema": 1, "kind": "treed-core-runtime", "version": "0.2.0",
            "files": [{"path": "sbin/treed-core-update", "runtimeSha256": "b" * 64}],
        }).encode()
        (old_dir / "journal.json").write_bytes(old_journal)
        (old_dir / "manifest.new.json").write_bytes(old_manifest)
        failed_goal = {"operationId": failed_id, "requestId": "44444444-4444-4444-8444-444444444444",
                       "targetId": "printer-core", "targetTag": "v0.3.0", "status": "installing",
                       "phase": "downloading", "busy": True, "attemptGeneration": 1,
                       "supersedesOperationId": old_id, "supersedesTargetTag": "v0.2.0",
                       "supersededResetConfigs": [{"path": "config/printer.cfg", "sha256": "a" * 64}],
                       "resetConfigs": [{"path": "config/printer.cfg", "sha256": "c" * 64}],
                       "pendingUiTag": "ui-main-123-1", "history": []}
        service.write_state(failed_goal)
        download_error = subprocess.CompletedProcess(
            [service.CORE_APPLY, "apply", "v0.3.0", failed_id], 1,
            stdout=json.dumps({"status": "error", "resultCode": "core_download_failed",
                               "message": "download failed"}))
        with patch.dict(service.os.environ, {"TREED_CORE_STATE_DIR": str(core_dir)}), \
             patch.object(service.subprocess, "run", return_value=download_error):
            self.assertEqual(1, service.run_core(failed_goal))
        error = service.read_state()
        self.assertTrue(error["canSupersedeCore"])
        self.assertEqual("core_download_failed", error["resultCode"])
        self.assertFalse((core_dir / failed_id / "journal.json").exists())

        with patch.dict(service.os.environ, {"TREED_CORE_STATE_DIR": str(core_dir)}), \
             patch.object(service.os, "geteuid", return_value=0, create=True), \
             patch.object(service, "acquire", return_value=FakeLock()), \
             patch.object(service.subprocess, "run"), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, service.submit("55555555-5555-4555-8555-555555555555",
                                               "printer-core", "v0.4.0"))
        next_goal = service.read_state()
        self.assertNotEqual(failed_id, next_goal["operationId"])
        self.assertEqual((next_goal["targetTag"], next_goal["supersedesOperationId"],
                          next_goal["supersedesTargetTag"]),
                         ("v0.4.0", old_id, "v0.2.0"))
        self.assertEqual(next_goal["supersededResetConfigs"], error["supersededResetConfigs"])
        self.assertNotIn("resetConfigs", next_goal)
        self.assertEqual(next_goal["pendingUiTag"], "ui-main-123-1")
        self.assertTrue(any(row.get("operationId") == failed_id
                            and row.get("resetConfigs") == failed_goal["resetConfigs"]
                            for row in next_goal["history"]))
        self.assertEqual((old_dir / "journal.json").read_bytes(), old_journal)
        self.assertEqual((old_dir / "manifest.new.json").read_bytes(), old_manifest)

    def test_service_start_failure_keeps_corrective_operation_available(self):
        old_id = "11111111-1111-4111-8111-111111111111"
        core_dir = self.root / "core"
        old_dir = core_dir / old_id
        old_dir.mkdir(parents=True)
        (old_dir / "journal.json").write_text(
            '{"schema":1,"stage":"provisioned","provisioningStarted":true}', encoding="utf-8")
        (old_dir / "manifest.new.json").write_text(json.dumps({
            "schema": 1, "kind": "treed-core-runtime", "version": "0.2.0",
            "files": [{"path": "sbin/treed-core-update", "runtimeSha256": "b" * 64}],
        }), encoding="utf-8")
        service.write_state({"operationId": old_id, "targetId": "printer-core", "targetTag": "v0.2.0",
                             "status": "error", "busy": False, "resultCode": "provisioning_failed",
                             "resetConfigs": [{"path": "config/printer.cfg", "sha256": "a" * 64}]})
        output = io.StringIO()
        with patch.dict(service.os.environ, {"TREED_CORE_STATE_DIR": str(core_dir)}), \
             patch.object(service.os, "geteuid", return_value=0, create=True), \
             patch.object(service, "acquire", return_value=FakeLock()), \
             patch.object(service.subprocess, "run", side_effect=OSError("systemd unavailable")), \
             contextlib.redirect_stdout(output):
            self.assertEqual(1, service.submit("33333333-3333-4333-8333-333333333333",
                                               "printer-core", "v0.3.0"))
        failed = service.read_state()
        self.assertTrue(json.loads(output.getvalue())["canSupersedeCore"])
        self.assertTrue(failed["canSupersedeCore"])
        self.assertEqual("service_start_failed", failed["resultCode"])
        self.assertEqual(failed["supersedesOperationId"], old_id)
        with patch.dict(service.os.environ, {"TREED_CORE_STATE_DIR": str(core_dir)}), \
             patch.object(service.os, "geteuid", return_value=0, create=True), \
             patch.object(service, "acquire", return_value=FakeLock()), \
             patch.object(service.subprocess, "run"), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, service.submit("44444444-4444-4444-8444-444444444444",
                                               "printer-core", "v0.4.0"))
        self.assertEqual("v0.4.0", service.read_state()["targetTag"])
        self.assertEqual(old_id, service.read_state()["supersedesOperationId"])

    def test_validation_failure_requires_recorded_pre_mutation_phase_for_anchor_carry(self):
        old_id = "11111111-1111-4111-8111-111111111111"
        failed_id = "33333333-3333-4333-8333-333333333333"
        core_dir = self.root / "core"
        old_dir = core_dir / old_id
        old_dir.mkdir(parents=True)
        (old_dir / "journal.json").write_text(
            '{"schema":1,"stage":"provisioning_failed","provisioningStarted":true}', encoding="utf-8")
        (old_dir / "manifest.new.json").write_text(json.dumps({
            "schema": 1, "kind": "treed-core-runtime", "version": "0.2.0",
            "files": [{"path": "sbin/treed-core-update", "runtimeSha256": "b" * 64}],
        }), encoding="utf-8")
        validation_error = {"operationId": failed_id, "requestId": "44444444-4444-4444-8444-444444444444",
                            "targetId": "printer-core", "targetTag": "v0.3.0", "status": "downloading",
                            "phase": "downloading", "busy": True, "resultCode": None,
                            "supersedesOperationId": old_id,
                            "supersedesTargetTag": "v0.2.0",
                            "supersededResetConfigs": [{"path": "config/printer.cfg", "sha256": "a" * 64}],
                            "resetConfigs": [{"path": "config/printer.cfg", "sha256": "c" * 64}],
                            "history": []}
        with patch.dict(service.os.environ, {"TREED_CORE_STATE_DIR": str(core_dir)}):
            service.finish(validation_error, "error", "core_validation_failed", "invalid package")
        validation_error = service.read_state()
        self.assertEqual(validation_error["lastFailurePhase"], "downloading")
        self.assertTrue(validation_error["canSupersedeCore"])
        with patch.dict(service.os.environ, {"TREED_CORE_STATE_DIR": str(core_dir)}), \
             patch.object(service.os, "geteuid", return_value=0, create=True), \
             patch.object(service, "acquire", return_value=FakeLock()), \
             patch.object(service.subprocess, "run"), contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(service.corrective_replacement_context(validation_error))
            self.assertEqual(0, service.submit("55555555-5555-4555-8555-555555555555",
                                               "printer-core", "v0.4.0"))
        replacement = service.read_state()
        self.assertEqual(replacement["supersedesOperationId"], old_id)
        self.assertEqual(replacement["supersedesTargetTag"], "v0.2.0")
        self.assertNotIn("resetConfigs", replacement)

        unsafe = dict(validation_error, lastFailurePhase="installing")
        service.write_state(unsafe)
        before = service.STATE.read_bytes()
        with patch.dict(service.os.environ, {"TREED_CORE_STATE_DIR": str(core_dir)}), \
             patch.object(service.os, "geteuid", return_value=0, create=True), \
             patch.object(service, "acquire", return_value=FakeLock()), \
             patch.object(service.subprocess, "run") as start, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(service.corrective_replacement_context(unsafe))
            with self.assertRaises(ValueError):
                service.submit("66666666-6666-4666-8666-666666666666",
                               "printer-core", "v0.4.0")
        self.assertEqual(service.STATE.read_bytes(), before)
        start.assert_not_called()

    def test_core_replacement_requires_newer_tag_and_valid_provisioning_journal(self):
        old_id = "11111111-1111-4111-8111-111111111111"
        state = {"operationId": old_id, "targetId": "printer-core", "targetTag": "v0.2.0",
                 "status": "error", "busy": False, "resultCode": "core_validation_failed"}
        core_dir = self.root / "core"
        journal_dir = core_dir / old_id
        journal_dir.mkdir(parents=True)
        journal = journal_dir / "journal.json"
        manifest = journal_dir / "manifest.new.json"
        journal_bytes = b'{"schema":1,"stage":"provisioning","provisioningStarted":true}'
        manifest_bytes = json.dumps({"schema": 1, "kind": "treed-core-runtime", "version": "0.2.0",
                                     "files": [{"path": "sbin/treed-core-update", "runtimeSha256": "b" * 64}]}).encode()
        journal.write_bytes(journal_bytes)
        manifest.write_bytes(manifest_bytes)
        service.write_state(state)
        cases = [
            ("printer-core", "v0.1.9", None),
            ("printer-ui", "ui-main-124-1", None),
            ("printer-core", "v0.3.0", {"schema": 1, "stage": "validated"}),
            ("printer-core", "v0.3.0", {"schema": 1, "stage": "validated", "provisioningStarted": "invalid"}),
        ]
        for target, tag, bad_journal in cases:
            with self.subTest(target=target, tag=tag, bad_journal=bad_journal):
                service.write_state(state)
                if bad_journal is not None:
                    journal.write_text(json.dumps(bad_journal), encoding="utf-8")
                elif tag == "v0.3.0":
                    journal.write_text('{"schema":1,"stage":"provisioning","provisioningStarted":true}',
                                       encoding="utf-8")
                if tag == "v0.3.0" and bad_journal is None:
                    manifest.write_text(json.dumps({"schema": 1, "kind": "treed-core-runtime",
                                                    "version": "0.2.0", "files": [{"path": "sbin/treed-core-update",
                                                                                  "runtimeSha256": "b" * 64}]}), encoding="utf-8")
                before = service.STATE.read_bytes()
                with patch.dict(service.os.environ, {"TREED_CORE_STATE_DIR": str(core_dir)}), \
                     patch.object(service.os, "geteuid", return_value=0, create=True), \
                     patch.object(service, "acquire", return_value=FakeLock()), \
                     patch.object(service.subprocess, "run") as start, \
                     contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaises(ValueError):
                        service.submit("33333333-3333-4333-8333-333333333333",
                                       target, tag)
                self.assertEqual(service.STATE.read_bytes(), before)
                start.assert_not_called()
        journal.write_bytes(journal_bytes)
        manifest.write_bytes(manifest_bytes)
        service.write_state(state)
        with patch.dict(service.os.environ, {"TREED_CORE_STATE_DIR": str(core_dir)}), \
             patch.object(service.os, "geteuid", return_value=0, create=True), \
             patch.object(service, "acquire", return_value=FakeLock()), \
             patch.object(service.subprocess, "run"), contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(service.core_supersede_eligible(state))
            self.assertEqual(0, service.submit("33333333-3333-4333-8333-333333333333",
                                               "printer-core", "v0.2.0"))
        self.assertEqual(old_id, service.read_state()["operationId"])
        state["busy"] = True
        state["status"] = "queued"
        service.write_state(state)
        journal.write_text('{"schema":1,"stage":"provisioning","provisioningStarted":true}', encoding="utf-8")
        manifest.write_text(json.dumps({"schema": 1, "kind": "treed-core-runtime", "version": "0.2.0",
                                        "files": [{"path": "sbin/treed-core-update", "runtimeSha256": "b" * 64}]}), encoding="utf-8")
        with patch.dict(service.os.environ, {"TREED_CORE_STATE_DIR": str(core_dir)}), \
             patch.object(service.os, "geteuid", return_value=0, create=True), \
             patch.object(service, "acquire", return_value=FakeLock()), \
             patch.object(service.subprocess, "run") as start, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(4, service.submit("33333333-3333-4333-8333-333333333333",
                                               "printer-core", "v0.3.0"))
        start.assert_not_called()

    def test_corrective_retry_defers_same_goal_until_root_confirms_safe_admission(self):
        old_id = "11111111-1111-4111-8111-111111111111"
        new_id = "33333333-3333-4333-8333-333333333333"
        core_dir = self.root / "core"
        old_dir = core_dir / old_id
        old_dir.mkdir(parents=True)
        journal_bytes = b'{"schema":1,"stage":"provisioned","provisioningStarted":true}'
        manifest_bytes = json.dumps({"schema": 1, "kind": "treed-core-runtime", "version": "0.2.0",
                                     "files": [{"path": "sbin/treed-core-update", "runtimeSha256": "b" * 64}]}).encode()
        (old_dir / "journal.json").write_bytes(journal_bytes)
        (old_dir / "manifest.new.json").write_bytes(manifest_bytes)
        operation = {"operationId": new_id, "requestId": "44444444-4444-4444-8444-444444444444",
                     "targetId": "printer-core", "targetTag": "v0.3.0", "status": "queued",
                     "phase": "queued", "busy": True, "attemptGeneration": 0,
                     "supersedesOperationId": old_id, "supersedesTargetTag": "v0.2.0",
                     "supersededResetConfigs": [{"path": "config/printer.cfg", "sha256": "a" * 64}],
                     "resetConfigs": [{"path": "config/printer.cfg", "sha256": "c" * 64}],
                     "pendingUiTag": "ui-main-123-1"}
        service.write_state(operation)
        with patch.dict(service.os.environ, {"TREED_CORE_STATE_DIR": str(core_dir)}), \
             patch.object(service, "printer_job_state", return_value=None), \
             patch.object(service, "klipper_confirmed_inactive", return_value=False):
            self.assertEqual(0, service.run_current())
        deferred = service.read_state()
        self.assertEqual((deferred["operationId"], deferred["targetTag"], deferred["status"]),
                         (new_id, "v0.3.0", "error"))
        self.assertEqual(deferred["supersedesOperationId"], old_id)
        self.assertEqual(deferred["resetConfigs"], operation["resetConfigs"])
        self.assertEqual(deferred["supersededResetConfigs"], operation["supersededResetConfigs"])
        self.assertEqual(deferred["pendingUiTag"], operation["pendingUiTag"])
        self.assertEqual(deferred["retry"]["attempts"], 1)
        self.assertEqual((old_dir / "journal.json").read_bytes(), journal_bytes)
        self.assertEqual((old_dir / "manifest.new.json").read_bytes(), manifest_bytes)

        deferred["retry"]["scheduledAt"] = "2000-01-01T00:00:00Z"
        deferred["retry"]["nextAttemptAt"] = "2000-01-01T00:00:30Z"
        service.write_state(deferred)
        with patch.dict(service.os.environ, {"TREED_CORE_STATE_DIR": str(core_dir)}), \
             patch.object(service.os, "geteuid", return_value=0, create=True), \
             patch.object(service, "acquire", return_value=FakeLock()), \
             patch.object(service, "printer_job_state", return_value="standby"), \
             patch.object(service.subprocess, "run") as start, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, service.retry_due())
        queued = service.read_state()
        self.assertEqual((queued["operationId"], queued["targetTag"], queued["status"]),
                         (new_id, "v0.3.0", "queued"))
        self.assertFalse(queued["resumeCore"])
        self.assertEqual(queued["supersedesOperationId"], old_id)
        start.assert_called_once_with(["systemctl", "start", "--no-block", service.UNIT],
                                      check=True, timeout=3)

        success = subprocess.CompletedProcess(
            [service.CORE_APPLY, "apply", "v0.3.0", new_id], 0,
            stdout=json.dumps({"status": "applied", "resultCode": "core_updated", "message": "ok"}))
        with patch.dict(service.os.environ, {"TREED_CORE_STATE_DIR": str(core_dir)}), \
             patch.object(service, "printer_job_state", return_value="standby"), \
             patch.object(service.subprocess, "run", return_value=success):
            self.assertEqual(0, service.run_current())
        self.assertEqual(("printer-ui", "ui-main-123-1", "queued"),
                         (service.read_state()["targetId"], service.read_state()["targetTag"],
                          service.read_state()["status"]))
        self.assertTrue(service.read_state()["coreUpdated"])
        self.assertEqual((old_dir / "journal.json").read_bytes(), journal_bytes)

    def test_corrective_inactive_fallback_never_overrides_confirmed_print(self):
        for job in ("printing", "paused"):
            with self.subTest(job=job), \
                 patch.object(service, "printer_job_state", return_value=job), \
                 patch.object(service.subprocess, "run") as systemd:
                self.assertFalse(service.corrective_admission_safe())
                systemd.assert_not_called()
        with patch.object(service, "printer_job_state", return_value=None), \
             patch.object(service.subprocess, "run",
                          return_value=subprocess.CompletedProcess([], 0, stdout="inactive\n")) as systemd:
            self.assertTrue(service.corrective_admission_safe())
            systemd.assert_called_once_with(
                ["systemctl", "show", "--property=ActiveState", "--value", "klipper.service"],
                capture_output=True, text=True, check=False, timeout=3)
        with patch.object(service, "printer_job_state", return_value=None), \
             patch.object(service.subprocess, "run",
                          return_value=subprocess.CompletedProcess([], 0, stdout="active\n")):
            self.assertFalse(service.corrective_admission_safe())

    def test_corrective_download_retry_uses_inactive_fallback_and_defers_unsafe_states(self):
        old_id = "11111111-1111-4111-8111-111111111111"
        new_id = "33333333-3333-4333-8333-333333333333"
        core_dir = self.root / "core"
        old_dir = core_dir / old_id
        old_dir.mkdir(parents=True)
        (old_dir / "journal.json").write_text(
            '{"schema":1,"stage":"provisioned","provisioningStarted":true}', encoding="utf-8")
        (old_dir / "manifest.new.json").write_text(json.dumps({
            "schema": 1, "kind": "treed-core-runtime", "version": "0.2.0",
            "files": [{"path": "sbin/treed-core-update", "runtimeSha256": "b" * 64}],
        }), encoding="utf-8")
        operation = {"operationId": new_id, "targetId": "printer-core", "targetTag": "v0.3.0",
                     "status": "error", "phase": "error", "busy": False,
                     "resultCode": "core_download_failed", "attemptGeneration": 1,
                     "supersedesOperationId": old_id, "supersedesTargetTag": "v0.2.0",
                     "supersededResetConfigs": [{"path": "config/printer.cfg", "sha256": "a" * 64}],
                     "resetConfigs": [{"path": "config/printer.cfg", "sha256": "c" * 64}],
                     "pendingUiTag": "ui-main-123-1",
                     "retry": {"attempts": 1, "scheduledAt": "2000-01-01T00:00:00Z",
                               "nextAttemptAt": "2000-01-01T00:00:30Z", "exhausted": False,
                               "lastResultCode": "core_download_failed"}}
        service.write_state(operation)

        with patch.dict(service.os.environ, {"TREED_CORE_STATE_DIR": str(core_dir)}), \
             patch.object(service.os, "geteuid", return_value=0, create=True), \
             patch.object(service, "acquire", return_value=FakeLock()), \
             patch.object(service, "printer_job_state", return_value=None), \
             patch.object(service.subprocess, "run") as systemd:
            self.assertEqual(0, service.retry_due())
        queued = service.read_state()
        self.assertEqual(queued["resultCode"], "core_download_failed")
        self.assertFalse(queued["resumeCore"])

        def run_systemd(command, **_kwargs):
            if command[:2] == ["systemctl", "show"]:
                return subprocess.CompletedProcess(command, 0, stdout="inactive\n")
            return subprocess.CompletedProcess(command, 0)

        with patch.dict(service.os.environ, {"TREED_CORE_STATE_DIR": str(core_dir)}), \
             patch.object(service, "printer_job_state", return_value=None), \
             patch.object(service, "printer_is_idle", side_effect=AssertionError("generic idle check")), \
             patch.object(service, "loading_screen"), \
             patch.object(service, "run_core", return_value=0) as apply, \
             patch.object(service.subprocess, "run", side_effect=run_systemd):
            self.assertTrue(service.corrective_replacement_context(queued))
            self.assertEqual(0, service.run_current())
        apply.assert_called_once()
        self.assertFalse(apply.call_args.kwargs.get("recovery", False))
        self.assertEqual((apply.call_args.args[0]["operationId"], apply.call_args.args[0]["targetTag"]),
                         (new_id, "v0.3.0"))
        systemd.assert_called_once_with(["systemctl", "start", "--no-block", service.UNIT],
                                        check=True, timeout=3)

        # A confirmed print keeps the due goal byte-for-byte unchanged.
        state = service.read_state()
        state.update(status="error", phase="error", busy=False,
                     resultCode="core_download_failed")
        state["retry"].update(scheduledAt="2000-01-01T00:00:00Z",
                              nextAttemptAt="2000-01-01T00:00:30Z")
        service.write_state(state)
        before_print = service.read_state()
        with patch.dict(service.os.environ, {"TREED_CORE_STATE_DIR": str(core_dir)}), \
             patch.object(service.os, "geteuid", return_value=0, create=True), \
             patch.object(service, "acquire", return_value=FakeLock()), \
             patch.object(service, "printer_job_state", return_value="printing"), \
             patch.object(service.subprocess, "run") as start:
            self.assertEqual(0, service.retry_due())
        after_print = service.read_state()
        self.assertTrue(after_print["canSupersedeCore"])
        before_print.pop("canSupersedeCore", None)
        after_print.pop("canSupersedeCore", None)
        self.assertEqual(after_print, before_print)
        start.assert_not_called()

        # When Moonraker is unavailable and Klipper is still active, defer the same goal.
        state = service.read_state()
        state.update(status="error", phase="error", busy=False,
                     resultCode="core_download_failed")
        state["retry"].update(scheduledAt="2000-01-01T00:00:00Z",
                              nextAttemptAt="2000-01-01T00:00:30Z")
        service.write_state(state)
        with patch.dict(service.os.environ, {"TREED_CORE_STATE_DIR": str(core_dir)}), \
             patch.object(service.os, "geteuid", return_value=0, create=True), \
             patch.object(service, "acquire", return_value=FakeLock()), \
             patch.object(service, "printer_job_state", return_value=None), \
             patch.object(service.subprocess, "run", side_effect=lambda command, **_kwargs:
                          subprocess.CompletedProcess(command, 0, stdout="active\n")):
            self.assertEqual(0, service.retry_due())
            with patch.object(service, "loading_screen"), \
                 patch.object(service, "run_core") as apply:
                self.assertEqual(0, service.run_current())
                apply.assert_not_called()
        deferred = service.read_state()
        self.assertEqual((deferred["operationId"], deferred["targetTag"], deferred["status"],
                          deferred["resultCode"]),
                         (new_id, "v0.3.0", "error", "corrective_precondition_deferred"))
        self.assertEqual(deferred["retry"]["attempts"], state["retry"]["attempts"])
        self.assertEqual(deferred["supersedesOperationId"], old_id)

    def test_retry_resumes_same_core_operation_after_service_start_failure(self):
        operation = {"operationId": "11111111-1111-4111-8111-111111111111",
                     "requestId": "22222222-2222-4222-8222-222222222222",
                     "targetId": "printer-core", "targetTag": "v0.2.0", "status": "error",
                     "resultCode": "provisioning_failed", "pendingUiTag": "ui-main-123-1",
                     "resetConfigs": [{"path": "config/printer.cfg", "sha256": "a" * 64}], "busy": False}
        journal = self.root / "core" / operation["operationId"] / "journal.json"
        journal.parent.mkdir(parents=True)
        journal.write_text("{}", encoding="utf-8")
        with patch.dict(service.os.environ, {"TREED_CORE_STATE_DIR": str(self.root / "core")}):
            service.write_state(operation)
        with patch.object(service.os, "geteuid", return_value=0, create=True), \
                patch.dict(service.os.environ, {"TREED_CORE_STATE_DIR": str(self.root / "core")}), \
                patch.object(service, "acquire", return_value=FakeLock()), \
                patch.object(service.subprocess, "run", side_effect=[OSError("systemd unavailable"), None]), \
                contextlib.redirect_stdout(io.StringIO()):
            service.submit("33333333-3333-4333-8333-333333333333", "printer-core", "v0.2.0")
            service.submit("44444444-4444-4444-8444-444444444444", "printer-core", "v0.2.0")
        saved = service.read_state()
        for key in ("operationId", "resetConfigs", "pendingUiTag"):
            self.assertEqual(operation[key], saved[key])
        with patch.object(service, "run_core", return_value=0) as recover, \
                patch.object(service, "printer_is_idle") as idle:
            self.assertEqual(0, service.run_current())
        recover.assert_called_once_with(saved, recovery=True)
        idle.assert_not_called()

    def test_core_retry_waits_then_queues_same_journal_operation_and_preserves_ui(self):
        operation = {"operationId": "retry-op", "requestId": "retry-request", "targetId": "printer-core",
                     "targetTag": "v0.2.0", "pendingUiTag": "ui-main-123-1",
                     "resetConfigs": [{"path": "config/printer.cfg", "sha256": "a" * 64}],
                     "status": "verifying", "busy": True, "history": []}
        journal = self.root / "core" / operation["operationId"] / "journal.json"
        journal.parent.mkdir(parents=True)
        journal.write_text("{}", encoding="utf-8")
        service.write_state(operation)
        with patch.dict(service.os.environ, {"TREED_CORE_STATE_DIR": str(self.root / "core")}):
            failed = subprocess.CompletedProcess(
                [service.CORE_APPLY, "apply", "v0.2.0", "retry-op"], 1,
                stdout=json.dumps({"status": "error", "resultCode": "provisioning_failed", "message": "failed"}))
            with patch.object(service.subprocess, "run", return_value=failed):
                self.assertEqual(service.run_core(operation), 1)
            saved = service.read_state()
            self.assertEqual(saved["retry"]["attempts"], 1)
            original = service.STATE.read_bytes()
            with patch.object(service.os, "geteuid", return_value=0, create=True), \
                    patch.object(service, "acquire", return_value=FakeLock()), \
                    patch.object(service.subprocess, "run") as start:
                self.assertEqual(service.retry_due(), 0)
                self.assertEqual(service.STATE.read_bytes(), original)
                start.assert_not_called()
                saved["retry"]["nextAttemptAt"] = "2000-01-01T00:00:00Z"
                service.write_state(saved)
                self.assertEqual(service.retry_due(), 0)
                start.assert_called_once_with(["systemctl", "start", "--no-block", service.UNIT],
                                              check=True, timeout=3)
            queued = service.read_state()
            self.assertEqual((queued["operationId"], queued["targetTag"], queued["resetConfigs"],
                              queued["pendingUiTag"]),
                             ("retry-op", "v0.2.0", operation["resetConfigs"], "ui-main-123-1"))
            self.assertTrue(queued["resumeCore"])
            recovered = subprocess.CompletedProcess(
                [service.CORE_APPLY, "recover", "retry-op"], 0,
                stdout=json.dumps({"status": "applied", "resultCode": "core_updated_recovered", "message": "ok"}))
            with patch.object(service.subprocess, "run", return_value=recovered) as core:
                self.assertEqual(service.run_current(), 0)
            self.assertEqual(core.call_args.args[0], [service.CORE_APPLY, "recover", "retry-op"])
            self.assertEqual(core.call_args.kwargs["env"]["TREED_UPDATE_OPERATION_ID"], "retry-op")
            self.assertEqual(core.call_args.kwargs["env"]["TREED_UPDATE_ATTEMPT_GENERATION"], "1")
            final = service.read_state()
            self.assertEqual(final["operationId"], "retry-op")
            self.assertEqual((final["targetId"], final["targetTag"], final["status"]),
                             ("printer-ui", "ui-main-123-1", "queued"))
            self.assertNotIn("retry", final)

    def test_boot_schedules_legacy_retry_once_without_immediate_worker(self):
        operation = {"operationId": "legacy-op", "targetId": "printer-core", "targetTag": "v0.2.0",
                     "status": "error", "busy": False, "resultCode": "provisioning_failed"}
        journal = self.root / "core" / operation["operationId"] / "journal.json"
        journal.parent.mkdir(parents=True)
        journal.write_text("{}", encoding="utf-8")
        service.write_state(operation)
        with patch.dict(service.os.environ, {"TREED_CORE_STATE_DIR": str(self.root / "core")}), \
                patch.object(service.os, "geteuid", return_value=0, create=True), \
                patch.object(service, "acquire", return_value=FakeLock()), \
                patch.object(service.subprocess, "run") as start:
            self.assertEqual(service.recover(), 0)
            scheduled = service.read_state()
            self.assertEqual(scheduled["retry"]["attempts"], 1)
            self.assertTrue(scheduled["retry"]["nextAttemptAt"])
            self.assertEqual(service.recover(), 0)
            self.assertEqual(service.read_state()["retry"], scheduled["retry"])
            start.assert_not_called()

    def test_retry_lock_busy_is_noop_and_permanent_or_malformed_plans_fail_closed(self):
        for retry in ({"attempts": 1, "nextAttemptAt": "2000-01-01T00:00:00Z"},
                      {"attempts": "bad", "nextAttemptAt": "2000-01-01T00:00:00Z"},
                      {"attempts": 1, "nextAttemptAt": "bad"}):
            service.write_state({"operationId": "retry", "targetId": "printer-core", "status": "error",
                                 "resultCode": "provisioning_failed", "retry": retry})
            before = service.STATE.read_bytes()
            with patch.object(service.os, "geteuid", return_value=0, create=True), \
                    patch.object(service, "acquire", return_value=None), \
                    patch.object(service.subprocess, "run") as start:
                self.assertEqual(service.retry_due(), 0)
                start.assert_not_called()
            self.assertEqual(service.STATE.read_bytes(), before)
        service.write_state({"operationId": "permanent", "targetId": "printer-core", "status": "error",
                             "resultCode": "core_validation_failed"})
        before = service.STATE.read_bytes()
        with patch.object(service.os, "geteuid", return_value=0, create=True), \
                patch.object(service, "acquire", return_value=FakeLock()), \
                patch.object(service.subprocess, "run") as start:
            self.assertEqual(service.retry_due(), 0)
            start.assert_not_called()
        self.assertEqual(service.STATE.read_bytes(), before)

    def test_retry_due_handles_clock_rollback_and_malformed_generation_without_reset(self):
        retry = {"attempts": 1, "scheduledAt": "2099-01-01T00:00:00Z",
                 "nextAttemptAt": "2099-01-01T00:00:30Z", "exhausted": False,
                 "lastResultCode": "provisioning_failed"}
        self.assertTrue(service.retry_is_due(retry))
        malformed = {"targetId": "printer-core", "status": "error", "resultCode": "core_download_failed",
                     "retry": {"attempts": "invalid"}}
        self.assertFalse(service.schedule_retry(malformed))
        self.assertEqual(malformed["retry"]["attempts"], "invalid")
        state = {"operationId": "retry", "attemptGeneration": "invalid", "targetId": "printer-core",
                 "status": "error", "resultCode": "core_download_failed", "retry": {
                     **retry, "scheduledAt": "2000-01-01T00:00:00Z",
                     "nextAttemptAt": "2000-01-01T00:00:30Z"}}
        service.write_state(state)
        before = service.STATE.read_bytes()
        with patch.object(service.os, "geteuid", return_value=0, create=True), \
                patch.object(service, "acquire", return_value=FakeLock()), \
                patch.object(service.subprocess, "run") as start:
            self.assertEqual(service.retry_due(), 0)
            start.assert_not_called()
        self.assertEqual(service.STATE.read_bytes(), before)

    def test_due_retry_waits_for_confirmed_print_and_then_queues_same_goal(self):
        state = {"operationId": "print-retry", "attemptGeneration": 4, "targetId": "printer-core",
                 "targetTag": "v0.2.0", "resetConfigs": True, "pendingUiTag": "ui-main-123-1",
                 "status": "error", "busy": False, "resultCode": "provisioning_failed",
                 "retry": {"attempts": 1, "scheduledAt": "2000-01-01T00:00:00Z",
                           "nextAttemptAt": "2000-01-01T00:00:30Z", "exhausted": False,
                           "lastResultCode": "provisioning_failed"}}
        journal = self.root / "core" / state["operationId"] / "journal.json"
        journal.parent.mkdir(parents=True)
        journal.write_text("{}", encoding="utf-8")
        service.write_state(state)
        before = service.STATE.read_bytes()
        with patch.dict(service.os.environ, {"TREED_CORE_STATE_DIR": str(self.root / "core")}), \
                patch.object(service.os, "geteuid", return_value=0, create=True), \
                patch.object(service, "acquire", return_value=FakeLock()), \
                patch.object(service, "printer_job_state", return_value="printing"), \
                patch.object(service.subprocess, "run") as start:
            self.assertEqual(service.retry_due(), 0)
            start.assert_not_called()
        self.assertEqual(service.STATE.read_bytes(), before)

        with patch.dict(service.os.environ, {"TREED_CORE_STATE_DIR": str(self.root / "core")}), \
                patch.object(service.os, "geteuid", return_value=0, create=True), \
                patch.object(service, "acquire", return_value=FakeLock()), \
                patch.object(service, "printer_job_state", return_value="standby"), \
                patch.object(service.subprocess, "run") as start:
            self.assertEqual(service.retry_due(), 0)
            start.assert_called_once_with(["systemctl", "start", "--no-block", service.UNIT],
                                         check=True, timeout=3)
        queued = service.read_state()
        self.assertEqual(queued["operationId"], state["operationId"])
        self.assertEqual(queued["targetTag"], state["targetTag"])
        self.assertEqual(queued["resetConfigs"], state["resetConfigs"])
        self.assertEqual(queued["pendingUiTag"], state["pendingUiTag"])
        self.assertEqual(queued["retry"]["attempts"], state["retry"]["attempts"])
        self.assertEqual(queued["attemptGeneration"], state["attemptGeneration"] + 1)
        self.assertTrue(queued["resumeCore"])

    def test_boot_recovery_defers_due_retry_for_confirmed_print_or_pause_without_writing(self):
        for print_state in ("printing", "paused"):
            with self.subTest(print_state=print_state):
                operation = {"operationId": f"boot-{print_state}", "targetId": "printer-core",
                             "targetTag": "v0.2.0", "status": "error", "busy": False,
                             "resultCode": "core_download_failed",
                             "retry": {"attempts": 1, "scheduledAt": "2000-01-01T00:00:00Z",
                                       "nextAttemptAt": "2000-01-01T00:00:30Z", "exhausted": False,
                                       "lastResultCode": "core_download_failed"}}
                service.write_state(operation)
                before = service.STATE.read_bytes()
                with patch.object(service.os, "geteuid", return_value=0, create=True), \
                        patch.object(service, "acquire", return_value=FakeLock()), \
                        patch.object(service, "printer_job_state", return_value=print_state), \
                        patch.object(service, "loading_screen"), \
                        patch.object(service.subprocess, "run") as start:
                    self.assertEqual(service.recover(), 0)
                    start.assert_not_called()
                self.assertEqual(service.STATE.read_bytes(), before)

    def test_recover_defers_interrupted_fresh_retry_before_journal_and_retries_same_goal(self):
        operation = {"operationId": "11111111-1111-4111-8111-111111111111",
                     "attemptGeneration": 3, "targetId": "printer-core", "targetTag": "v0.2.0",
                     "resetConfigs": [{"path": "config/printer.cfg", "sha256": "c" * 64}],
                     "pendingUiTag": "ui-main-123-1", "status": "validating", "phase": "validating",
                     "busy": True, "resultCode": "core_download_failed",
                     "retry": {"attempts": 1, "scheduledAt": "2000-01-01T00:00:00Z",
                               "nextAttemptAt": None, "exhausted": False,
                               "lastResultCode": "core_download_failed"}}
        core_state = self.root / "core"
        service.write_state(operation)
        with patch.dict(service.os.environ, {"TREED_CORE_STATE_DIR": str(core_state)}), \
                patch.object(service.os, "geteuid", return_value=0, create=True), \
                patch.object(service, "acquire", return_value=FakeLock()), \
                patch.object(service, "loading_screen"), \
                patch.object(service, "run_core") as recover_core, \
                patch.object(service.subprocess, "run") as start:
            self.assertEqual(service.recover(), 0)
            recover_core.assert_not_called()
            start.assert_not_called()
        deferred = service.read_state()
        self.assertEqual((deferred["operationId"], deferred["targetTag"], deferred["resetConfigs"],
                          deferred["pendingUiTag"]),
                         (operation["operationId"], operation["targetTag"], operation["resetConfigs"],
                          operation["pendingUiTag"]))
        self.assertEqual(deferred["status"], "error")
        self.assertFalse(deferred["busy"])
        self.assertEqual(deferred["retry"]["attempts"], 1)
        self.assertFalse((core_state / operation["operationId"] / "journal.json").exists())
        deferred["retry"]["scheduledAt"] = "2000-01-01T00:00:00Z"
        deferred["retry"]["nextAttemptAt"] = "2000-01-01T00:00:30Z"
        service.write_state(deferred)
        with patch.dict(service.os.environ, {"TREED_CORE_STATE_DIR": str(core_state)}), \
                patch.object(service.os, "geteuid", return_value=0, create=True), \
                patch.object(service, "acquire", return_value=FakeLock()), \
                patch.object(service, "printer_job_state", return_value="standby"), \
                patch.object(service.subprocess, "run"):
            self.assertEqual(service.retry_due(), 0)

        def successful_apply(state, recovery=False):
            self.assertFalse(recovery)
            self.assertEqual(state["operationId"], operation["operationId"])
            self.assertEqual(state["resetConfigs"], operation["resetConfigs"])
            self.assertEqual(state["pendingUiTag"], operation["pendingUiTag"])
            service.finish(state, "applied", "core_updated", "done")
            return 0

        with patch.object(service, "printer_is_idle", return_value=True), \
                patch.object(service, "loading_screen"), \
                patch.object(service, "run_core", side_effect=successful_apply) as apply:
            self.assertEqual(service.run_current(), 0)
        apply.assert_called_once()
        self.assertFalse(apply.call_args.kwargs.get("recovery", False))
        final = service.read_state()
        self.assertEqual((final["targetId"], final["targetTag"], final["status"]),
                         ("printer-ui", "ui-main-123-1", "queued"))

    def test_recover_does_not_treat_installing_without_journal_as_fresh_retry(self):
        operation = {"operationId": "22222222-2222-4222-8222-222222222222",
                     "attemptGeneration": 3, "targetId": "printer-core", "targetTag": "v0.2.0",
                     "status": "installing", "phase": "installing", "busy": True,
                     "resultCode": "core_download_failed",
                     "retry": {"attempts": 1, "scheduledAt": "2000-01-01T00:00:00Z",
                               "nextAttemptAt": None, "exhausted": False,
                               "lastResultCode": "core_download_failed"}}
        service.write_state(operation)
        with patch.dict(service.os.environ, {"TREED_CORE_STATE_DIR": str(self.root / "core")}), \
                patch.object(service.os, "geteuid", return_value=0, create=True), \
                patch.object(service, "acquire", return_value=FakeLock()), \
                patch.object(service, "loading_screen"), \
                patch.object(service, "run_core", return_value=1) as recover_core:
            self.assertEqual(service.recover(), 1)
        recover_core.assert_called_once_with(operation, recovery=True)
        self.assertEqual(service.read_state()["status"], "installing")

    def test_core_not_ready_with_journal_queues_existing_recovery_path(self):
        operation = {"operationId": "55555555-5555-4555-8555-555555555555",
                     "attemptGeneration": 2, "targetId": "printer-core", "targetTag": "v0.2.0",
                     "status": "error", "busy": False, "resultCode": "core_not_ready",
                     "retry": {"attempts": 1, "scheduledAt": "2000-01-01T00:00:00Z",
                               "nextAttemptAt": "2000-01-01T00:00:30Z", "exhausted": False,
                               "lastResultCode": "core_not_ready"}}
        core_state = self.root / "core"
        journal = core_state / operation["operationId"] / "journal.json"
        journal.parent.mkdir(parents=True)
        journal.write_text("{}", encoding="utf-8")
        service.write_state(operation)
        with patch.dict(service.os.environ, {"TREED_CORE_STATE_DIR": str(core_state)}), \
                patch.object(service.os, "geteuid", return_value=0, create=True), \
                patch.object(service, "acquire", return_value=FakeLock()), \
                patch.object(service, "printer_job_state", return_value="standby"), \
                patch.object(service.subprocess, "run"):
            self.assertEqual(service.retry_due(), 0)
        queued = service.read_state()
        self.assertTrue(queued["resumeCore"])
        with patch.object(service, "printer_is_idle") as idle, \
                patch.object(service, "run_core", return_value=1) as recover_core:
            self.assertEqual(service.run_current(), 1)
        recover_core.assert_called_once_with(queued, recovery=True)
        idle.assert_not_called()

    def test_retry_budget_survives_state_reload_and_exhausts_after_three_attempts(self):
        operation = {"operationId": "budget-op", "targetId": "printer-core", "targetTag": "v0.2.0",
                     "status": "error", "busy": False, "resultCode": "core_download_failed"}
        for attempt in range(4):
            if attempt:
                operation = service.read_state()
                operation.update(status="error", busy=False, resultCode="core_download_failed")
                operation["retry"]["nextAttemptAt"] = None
            service.finish(operation, "error", "core_download_failed", "failed")
            saved = service.read_state()
            self.assertEqual(saved["retry"]["attempts"], min(attempt + 1, 3))
            self.assertEqual(bool(saved["retry"]["exhausted"]), attempt == 3)
        state = service.read_state()
        with patch.object(service.os, "geteuid", return_value=0, create=True), \
                patch.object(service, "acquire", return_value=FakeLock()), \
                patch.object(service.subprocess, "run") as start:
            self.assertEqual(service.retry_due(), 0)
            start.assert_not_called()
        self.assertEqual(service.read_state()["retry"], state["retry"])

    def test_download_retry_without_journal_uses_fresh_core_apply(self):
        operation = {"operationId": "44444444-4444-4444-8444-444444444444",
                     "targetId": "printer-core", "targetTag": "v0.2.0",
                     "resetConfigs": [{"path": "config/printer.cfg", "sha256": "b" * 64}],
                     "pendingUiTag": "ui-main-123-1",
                     "status": "error", "busy": False, "resultCode": "core_download_failed"}
        service.finish(operation, "error", "core_download_failed", "download failed")
        queued = service.read_state()
        queued["retry"]["nextAttemptAt"] = "2000-01-01T00:00:00Z"
        service.write_state(queued)
        with patch.object(service.os, "geteuid", return_value=0, create=True), \
                patch.object(service, "acquire", return_value=FakeLock()), \
                patch.object(service, "printer_job_state", return_value=None), \
                patch.object(service.subprocess, "run"):
            service.retry_due()
        attempt = service.read_state()
        self.assertEqual(attempt["status"], "queued")
        self.assertEqual(attempt["retry"]["attempts"], 1)
        self.assertFalse(attempt.get("resumeCore", False))
        with patch.object(service, "printer_is_idle", return_value=False), \
                patch.object(service, "loading_screen"), \
                patch.object(service, "run_core") as apply:
            self.assertEqual(service.run_current(), 0)
            apply.assert_not_called()
        deferred = service.read_state()
        self.assertEqual((deferred["operationId"], deferred["targetTag"], deferred["resetConfigs"],
                          deferred["pendingUiTag"]),
                         (attempt["operationId"], attempt["targetTag"], attempt["resetConfigs"],
                          attempt["pendingUiTag"]))
        self.assertEqual(deferred["status"], "error")
        self.assertFalse(deferred["busy"])
        self.assertEqual(deferred["retry"]["attempts"], attempt["retry"]["attempts"])
        self.assertEqual(deferred["attemptGeneration"], attempt["attemptGeneration"])
        self.assertFalse(service.retry_is_due(deferred["retry"]))
        deferred["retry"]["scheduledAt"] = "2000-01-01T00:00:00Z"
        deferred["retry"]["nextAttemptAt"] = "2000-01-01T00:00:30Z"
        service.write_state(deferred)
        with patch.object(service.os, "geteuid", return_value=0, create=True), \
                patch.object(service, "acquire", return_value=FakeLock()), \
                patch.object(service, "printer_job_state", return_value="standby"), \
                patch.object(service.subprocess, "run"):
            self.assertEqual(service.retry_due(), 0)

        def successful_apply(operation_state, recovery=False):
            self.assertFalse(recovery)
            self.assertEqual(operation_state["resetConfigs"], operation["resetConfigs"])
            self.assertEqual(operation_state["pendingUiTag"], operation["pendingUiTag"])
            service.finish(operation_state, "applied", "core_updated", "done")
            return 0

        with patch.object(service, "printer_is_idle", return_value=True), \
                patch.object(service, "loading_screen"), \
                patch.object(service, "run_core", side_effect=successful_apply) as apply:
            self.assertEqual(service.run_current(), 0)
        apply.assert_called_once()
        self.assertFalse(apply.call_args.kwargs.get("recovery", False))
        final = service.read_state()
        self.assertEqual(final["operationId"], operation["operationId"])
        self.assertEqual((final["targetId"], final["targetTag"], final["status"]),
                         ("printer-ui", "ui-main-123-1", "queued"))

    def test_core_not_ready_after_download_defers_same_fresh_retry_without_spending_budget(self):
        operation = {"operationId": "33333333-3333-4333-8333-333333333333",
                     "attemptGeneration": 0, "targetId": "printer-core", "targetTag": "v0.2.0",
                     "resetConfigs": [{"path": "config/printer.cfg", "sha256": "d" * 64}],
                     "pendingUiTag": "ui-main-123-1", "status": "queued", "busy": True}
        core_state = self.root / "core"
        service.write_state(operation)
        not_ready = subprocess.CompletedProcess(
            [service.CORE_APPLY, "apply", "v0.2.0", operation["operationId"]], 1,
            stdout=json.dumps({"status": "error", "resultCode": "core_not_ready", "message": "not ready"}))
        applied = subprocess.CompletedProcess(
            [service.CORE_APPLY, "apply", "v0.2.0", operation["operationId"]], 0,
            stdout=json.dumps({"status": "applied", "resultCode": "core_updated", "message": "updated"}))

        def make_due_and_queue():
            current = service.read_state()
            current["retry"]["scheduledAt"] = "2000-01-01T00:00:00Z"
            current["retry"]["nextAttemptAt"] = "2000-01-01T00:00:30Z"
            service.write_state(current)
            with patch.dict(service.os.environ, {"TREED_CORE_STATE_DIR": str(core_state)}), \
                    patch.object(service.os, "geteuid", return_value=0, create=True), \
                    patch.object(service, "acquire", return_value=FakeLock()), \
                    patch.object(service, "printer_job_state", return_value="standby"), \
                    patch.object(service.subprocess, "run"):
                self.assertEqual(service.retry_due(), 0)

        with patch.dict(service.os.environ, {"TREED_CORE_STATE_DIR": str(core_state)}), \
                patch.object(service, "printer_is_idle", return_value=True), \
                patch.object(service, "loading_screen"), \
                patch.object(service.subprocess, "run", return_value=not_ready) as core:
            self.assertEqual(service.run_current(), 1)
        self.assertEqual(core.call_args.args[0],
                         [service.CORE_APPLY, "apply", "v0.2.0", operation["operationId"]])
        first_error = service.read_state()
        self.assertEqual(first_error["resultCode"], "core_not_ready")
        self.assertEqual(first_error["retry"]["attempts"], 1)
        self.assertFalse((core_state / operation["operationId"] / "journal.json").exists())

        make_due_and_queue()
        with patch.dict(service.os.environ, {"TREED_CORE_STATE_DIR": str(core_state)}), \
                patch.object(service, "printer_is_idle", return_value=True), \
                patch.object(service, "loading_screen"), \
                patch.object(service.subprocess, "run", return_value=not_ready) as core:
            self.assertEqual(service.run_current(), 1)
        self.assertEqual(core.call_args.args[0],
                         [service.CORE_APPLY, "apply", "v0.2.0", operation["operationId"]])
        deferred = service.read_state()
        self.assertEqual((deferred["operationId"], deferred["targetTag"], deferred["resetConfigs"],
                          deferred["pendingUiTag"]),
                         (operation["operationId"], operation["targetTag"], operation["resetConfigs"],
                          operation["pendingUiTag"]))
        self.assertEqual(deferred["status"], "error")
        self.assertFalse(deferred["busy"])
        self.assertEqual(deferred["resultCode"], "core_not_ready")
        self.assertEqual(deferred["retry"]["attempts"], first_error["retry"]["attempts"])
        self.assertEqual(deferred["retry"]["lastResultCode"], "core_not_ready")
        self.assertFalse(service.retry_is_due(deferred["retry"]))

        make_due_and_queue()
        with patch.dict(service.os.environ, {"TREED_CORE_STATE_DIR": str(core_state)}), \
                patch.object(service, "printer_is_idle", return_value=True), \
                patch.object(service, "loading_screen"), \
                patch.object(service.subprocess, "run", return_value=applied) as core:
            self.assertEqual(service.run_current(), 0)
        self.assertEqual(core.call_args.args[0],
                         [service.CORE_APPLY, "apply", "v0.2.0", operation["operationId"]])
        final = service.read_state()
        self.assertEqual(final["operationId"], operation["operationId"])
        self.assertEqual((final["targetId"], final["targetTag"], final["status"]),
                         ("printer-ui", "ui-main-123-1", "queued"))
        self.assertNotIn("retry", final)

    def test_ui_apply_phase_uses_operation_and_attempt_context(self):
        operation = {"operationId": "ui-phase-op", "attemptGeneration": 8, "targetId": "printer-ui",
                     "targetTag": "ui-main-123-1", "status": "queued", "busy": True}
        service.write_state(operation)

        def apply_ui(_command, env, **_kwargs):
            self.assertEqual(env["TREED_UPDATE_OPERATION_ID"], operation["operationId"])
            self.assertEqual(env["TREED_UPDATE_ATTEMPT_GENERATION"], str(operation["attemptGeneration"]))
            with patch.dict(service.os.environ, env):
                service.transition("verifying", "phase from UI updater")
            self.assertEqual(service.read_state()["phase"], "verifying")
            return types.SimpleNamespace(returncode=0)

        with patch.object(service, "printer_is_idle", return_value=True), \
                patch.object(service, "loading_screen"), \
                patch.object(service, "shell_runtime_dir", return_value=self.root / "shell"), \
                patch.object(service.subprocess, "run", side_effect=apply_ui):
            self.assertEqual(service.run_current(), 0)
        self.assertEqual(service.read_state()["status"], "applied")

    def test_phase_generation_rejects_old_context_without_changing_state(self):
        operation = {"operationId": "phase-op", "attemptGeneration": 2, "status": "installing",
                     "busy": True, "message": "current"}
        service.write_state(operation)
        before = service.STATE.read_bytes()
        with patch.dict(service.os.environ, {"TREED_UPDATE_OPERATION_ID": "phase-op",
                                             "TREED_UPDATE_ATTEMPT_GENERATION": "1"}):
            service.transition("verifying", "stale")
        self.assertEqual(service.STATE.read_bytes(), before)
        with patch.dict(service.os.environ, {}, clear=True):
            service.transition("verifying", "legacy caller")
        self.assertEqual(service.read_state()["phase"], "verifying")

    def test_stale_core_result_cannot_finish_a_new_attempt_generation(self):
        operation = {"operationId": "phase-op", "attemptGeneration": 1, "targetId": "printer-core",
                     "targetTag": "v0.2.0", "status": "installing", "busy": True}
        service.write_state(operation)

        def stale_apply(_command, **_kwargs):
            current = service.read_state()
            current.update(attemptGeneration=2, status="queued", phase="queued", busy=True)
            service.write_state(current)
            return types.SimpleNamespace(returncode=0, stdout='{"status":"applied","resultCode":"core_updated"}')

        with patch.object(service.subprocess, "run", side_effect=stale_apply):
            self.assertEqual(service.run_core(operation), 1)
        state = service.read_state()
        self.assertEqual((state["attemptGeneration"], state["status"], state["busy"]), (2, "queued", True))

    def test_validation_error_without_journal_does_not_require_recovery(self):
        operation = {"operationId": "11111111-1111-4111-8111-111111111111",
                     "targetId": "printer-core", "targetTag": "v0.2.0", "status": "installing", "busy": True}
        service.write_state(operation)
        result = subprocess.CompletedProcess([], 1, stdout=json.dumps({
            "status": "error", "resultCode": "core_update_failed", "message": "invalid package"}))
        with patch.object(service.subprocess, "run", return_value=result), \
                patch.dict(service.os.environ, {"TREED_CORE_STATE_DIR": str(self.root / "core")}):
            self.assertEqual(1, service.run_core(operation))
        self.assertEqual("core_validation_failed", service.read_state()["resultCode"])

    def test_lock_contention_returns_existing_operation_without_rewrite(self):
        existing = {
            "operationId": "operation-1", "requestId": "11111111-1111-4111-8111-111111111111",
            "targetId": "printer-ui", "targetTag": "ui-main-123-1", "status": "installing",
            "phase": "installing", "busy": True, "history": [],
        }
        service.write_state(existing)
        output = io.StringIO()
        with patch.object(service.os, "geteuid", return_value=0, create=True), \
             patch.object(service, "acquire", return_value=None), \
             contextlib.redirect_stdout(output):
            result = service.submit("22222222-2222-4222-8222-222222222222", "printer-ui", "ui-main-124-1")
        response = json.loads(output.getvalue())
        persisted = json.loads(service.STATE.read_text(encoding="utf-8"))
        self.assertEqual(result, 4)
        self.assertFalse(response["accepted"])
        self.assertEqual(response["operationId"], "operation-1")
        self.assertEqual(persisted["operationId"], "operation-1")

    def test_terminal_state_is_durable_and_keeps_bounded_history(self):
        operation = {
            "operationId": "operation-2", "requestId": "11111111-1111-4111-8111-111111111111",
            "targetId": "printer-ui", "targetTag": "ui-main-123-1", "status": "installing",
            "phase": "installing", "busy": True, "progress": None, "history": [],
        }
        service.write_state(operation)
        service.finish(operation, "applied", "ui_updated", "Обновление TreeD UI завершено.")
        state = json.loads(service.STATE.read_text(encoding="utf-8"))
        self.assertFalse(state["busy"])
        self.assertEqual(state["status"], "applied")
        self.assertEqual(state["phase"], "complete")
        self.assertEqual(state["progress"], 100)
        self.assertEqual(len(state["history"]), 1)

    def test_invalid_identifiers_tags_and_shell_metacharacters_are_rejected(self):
        invalid = [
            ("not-a-uuid", "printer-ui", "ui-main-123-1"),
            ("11111111-1111-4111-8111-111111111111", "printer-ui;touch /tmp/x", "ui-main-123-1"),
            ("11111111-1111-4111-8111-111111111111", "printer-ui", "ui-main-123-1;touch /tmp/x"),
        ]
        with patch.object(service.os, "geteuid", return_value=0, create=True), \
             patch.object(service, "acquire", return_value=FakeLock()), \
             patch.object(service.subprocess, "run") as start_service:
            for arguments in invalid:
                with self.assertRaises(ValueError):
                    service.submit(*arguments)
        start_service.assert_not_called()

    def test_systemd_start_failure_clears_busy_state(self):
        output = io.StringIO()
        with patch.object(service.os, "geteuid", return_value=0, create=True), \
             patch.object(service, "acquire", return_value=FakeLock()), \
             patch.object(service.subprocess, "run", side_effect=TimeoutError("systemd timeout")), \
             contextlib.redirect_stdout(output):
            result = service.submit(
                "11111111-1111-4111-8111-111111111111", "printer-ui", "ui-main-123-1")
        state = json.loads(service.STATE.read_text(encoding="utf-8"))
        self.assertEqual(result, 1)
        self.assertEqual(state["status"], "error")
        self.assertEqual(state["resultCode"], "service_start_failed")
        self.assertFalse(state["busy"])

    def test_core_worker_uses_managed_runtime_package_and_persists_result(self):
        operation = {
            "operationId": "operation-3", "requestId": "11111111-1111-4111-8111-111111111111",
            "targetId": "printer-core", "targetTag": "v1.2.3", "status": "queued",
            "phase": "queued", "busy": True, "progress": 0, "history": [],
        }
        service.write_state(operation)
        with patch.object(service.os, "geteuid", return_value=0, create=True), \
             patch.object(service, "acquire", return_value=FakeLock()), \
             patch.object(service, "printer_is_idle", return_value=True), \
             patch.object(service.subprocess, "run", return_value=types.SimpleNamespace(
                 returncode=0, stdout=json.dumps({"status": "applied", "resultCode": "core_updated",
                                                 "message": "Core установлен."}))) as apply:
            self.assertEqual(service.run_worker(), 0)
        state = json.loads(service.STATE.read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "applied")
        self.assertEqual(state["resultCode"], "core_updated")
        self.assertFalse(state["busy"])
        self.assertEqual(apply.call_args.args[0], [service.CORE_APPLY, "apply", "v1.2.3", "operation-3"])

    def test_core_recovery_records_rollback_from_independent_installer(self):
        service.write_state({"operationId": "operation-3", "targetId": "printer-core",
                             "targetTag": "v1.2.3", "status": "installing", "busy": True})
        with patch.object(service.os, "geteuid", return_value=0, create=True), \
             patch.object(service, "acquire", return_value=FakeLock()), \
             patch.object(service.subprocess, "run", return_value=types.SimpleNamespace(
                 returncode=1, stdout=json.dumps({"status": "rolled_back", "resultCode": "rollback_succeeded",
                                                 "message": "Core восстановлен."}))) as command:
            self.assertEqual(service.recover(), 0)
        self.assertEqual(command.call_args.args[0], [service.CORE_APPLY, "recover", "operation-3"])
        self.assertEqual(service.read_state()["status"], "rolled_back")

    def test_core_resume_recovers_instead_of_overwriting_interrupted_transaction(self):
        service.write_state({"operationId": "operation-3", "targetId": "printer-core",
                             "targetTag": "v1.2.3", "status": "installing", "busy": True})
        with patch.object(service.os, "geteuid", return_value=0, create=True), \
             patch.object(service, "acquire", return_value=FakeLock()), \
             patch.object(service, "run_core", return_value=1) as command, \
             patch.object(service, "printer_is_idle") as idle:
            self.assertEqual(service.run_worker(), 1)
        self.assertTrue(command.call_args.kwargs["recovery"])
        idle.assert_not_called()

    def test_reboot_recovery_marks_interrupted_operation_without_claiming_rollback(self):
        operation = {
            "operationId": "operation-4", "requestId": "11111111-1111-4111-8111-111111111111",
            "targetId": "printer-ui", "targetTag": "ui-main-123-1", "status": "installing",
            "phase": "installing", "busy": True, "history": [],
        }
        service.write_state(operation)
        with patch.object(service.os, "geteuid", return_value=0, create=True), \
             patch.object(service, "acquire", return_value=FakeLock()):
            self.assertEqual(service.recover(), 0)
        state = json.loads(service.STATE.read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "error")
        self.assertEqual(state["resultCode"], "operation_interrupted")
        self.assertFalse(state["busy"])
        self.assertNotIn("rollbackSucceeded", state)

    def test_interrupted_ui_publication_restores_previous_files_and_checks_readiness(self):
        operation_id = "11111111-1111-4111-8111-111111111111"
        runtime = self.root / "treed" / "treed-shell-runtime"
        current = runtime / "ui"
        rollback = runtime / f"ui.rollback.{operation_id}"
        current.mkdir(parents=True)
        rollback.mkdir()
        (current / "version.txt").write_text("new", encoding="utf-8")
        (rollback / "version.txt").write_text("previous", encoding="utf-8")
        operation = {
            "operationId": operation_id, "requestId": "22222222-2222-4222-8222-222222222222",
            "targetId": "printer-ui", "targetTag": "ui-main-123-1", "status": "installing",
            "phase": "installing", "busy": True, "history": [],
        }
        service.write_state(operation)
        with patch.object(service.os, "geteuid", return_value=0, create=True), \
             patch.object(service, "acquire", return_value=FakeLock()), \
             patch.object(service, "shell_runtime_dir", return_value=runtime), \
             patch.object(service, "wait_shell_ready", return_value=True), \
             patch.object(service.subprocess, "run"):
            original_copy = shutil.copytree
            def interrupted_copy(*args, **kwargs):
                original_copy(*args, **kwargs)
                raise KeyboardInterrupt("power loss after restoring files")
            with patch.object(service.shutil, "copytree", side_effect=interrupted_copy), \
                    self.assertRaises(KeyboardInterrupt):
                service.recover()
            self.assertTrue(rollback.is_dir())
            self.assertEqual(service.recover(), 0)
        state = json.loads(service.STATE.read_text(encoding="utf-8"))
        self.assertEqual((current / "version.txt").read_text(encoding="utf-8"), "previous")
        self.assertEqual((rollback / "version.txt").read_text(encoding="utf-8"), "previous")
        self.assertEqual(state["status"], "rolled_back")
        self.assertEqual(state["resultCode"], "rollback_succeeded")

    def test_ui_apply_error_recovers_before_finishing_the_operation(self):
        operation_id = "11111111-1111-4111-8111-111111111111"
        runtime = self.root / "runtime"
        rollback = runtime / f"ui.rollback.{operation_id}"
        rollback.mkdir(parents=True)
        (rollback / "index.html").write_text("previous UI", encoding="utf-8")
        service.write_state({"operationId": operation_id, "targetId": "printer-ui",
                             "targetTag": "ui-main-123-1", "status": "queued", "busy": True})
        with patch.object(service, "printer_is_idle", return_value=True), \
                patch.object(service, "shell_runtime_dir", return_value=runtime), \
                patch.object(service, "wait_shell_ready", return_value=True), \
                patch.object(service.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)):
            self.assertEqual(1, service.run_current())
        self.assertEqual("rolled_back", service.read_state()["status"])
        self.assertEqual("previous UI", (runtime / "ui/index.html").read_text())

    def test_recovery_confirms_a_fully_published_and_ready_ui_without_rollback_directory(self):
        operation_id = "33333333-3333-4333-8333-333333333333"
        runtime = self.root / "treed" / "treed-shell-runtime"
        current = runtime / "ui"
        current.mkdir(parents=True)
        manifest = {"runNumber": 123, "runAttempt": 1, "mode": "live"}
        (current / "treed-shell-ui-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        operation = {
            "operationId": operation_id, "requestId": "22222222-2222-4222-8222-222222222222",
            "targetId": "printer-ui", "targetTag": "ui-main-123-1", "status": "verifying",
            "phase": "verifying", "busy": True, "history": [],
        }
        service.write_state(operation)
        with patch.object(service.os, "geteuid", return_value=0, create=True), \
             patch.object(service, "acquire", return_value=FakeLock()), \
             patch.object(service, "shell_runtime_dir", return_value=runtime), \
             patch.object(service, "wait_shell_ready", return_value=True):
            self.assertEqual(service.recover(), 0)
        state = json.loads(service.STATE.read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "applied")
        self.assertEqual(state["resultCode"], "ui_updated_recovered")


    def test_combined_update_runs_core_then_ui_and_stops_on_core_failure(self):
        for outcome in ("applied", "error", "rolled_back"):
            with self.subTest(core_result=outcome):
                service.write_state({"status": "idle"})
                calls = []

                def run(command, **_kwargs):
                    calls.append(command)
                    if command[0] == service.CORE_APPLY:
                        return types.SimpleNamespace(returncode=0 if outcome == "applied" else 1,
                            stdout=json.dumps({"status": outcome, "message": "core result"}))
                    if command[0] == service.APPLY:
                        self.assertTrue(service.read_state()["busy"])
                        self.assertTrue(service.read_state()["coreUpdated"])
                    return types.SimpleNamespace(returncode=0)

                with patch.object(service.os, "geteuid", return_value=0, create=True), \
                     patch.object(service, "acquire", return_value=FakeLock()), \
                     patch.object(service, "printer_is_idle", return_value=True), \
                     patch.object(service.subprocess, "run", side_effect=run), \
                     contextlib.redirect_stdout(io.StringIO()):
                    service.submit("11111111-1111-4111-8111-111111111111", "printer-core", "v0.2.0", "ui-main-123-1")
                    queued = service.read_state()
                    self.assertEqual(queued["pendingUiTag"], "ui-main-123-1")
                    service.run_worker()
                final = service.read_state()
                self.assertEqual(final["operationId"], queued["operationId"])
                self.assertFalse(final["busy"])
                self.assertEqual(final["status"], outcome)
                self.assertEqual([call[0] for call in calls],
                                 ["systemctl", service.CORE_APPLY] + ([service.APPLY] if outcome == "applied" else []))

    def test_combined_update_preserves_next_step_across_recovery(self):
        state = {"operationId": "batch", "requestId": "request", "targetId": "printer-core",
                 "targetTag": "v0.2.0", "pendingUiTag": "ui-main-123-1", "busy": True, "status": "verifying"}
        service.finish(state, "applied", "core_updated", "core ready")
        with patch.object(service.os, "geteuid", return_value=0, create=True), \
             patch.object(service, "acquire", return_value=FakeLock()), \
             patch.object(service.subprocess, "run") as restart:
            service.recover()
            restart.assert_called_once_with(["systemctl", "start", "--no-block", service.UNIT], check=True, timeout=3)
        resumed = service.read_state()
        self.assertEqual((resumed["targetId"], resumed["targetTag"], resumed["status"]),
                         ("printer-ui", "ui-main-123-1", "queued"))
        self.assertTrue(resumed["busy"])
        service.finish(resumed, "error", "ui_failed", "Интерфейс не обновлён.")
        self.assertEqual(service.read_state()["message"], "Система обновлена. Интерфейс не обновлён.")

    def test_combined_submission_validates_both_targets_before_acquiring_lock(self):
        with patch.object(service.os, "geteuid", return_value=0, create=True), \
             patch.object(service, "acquire") as acquire:
            for target, tag, ui_tag in (("printer-ui", "ui-main-1-1", "ui-main-2-1"),
                                        ("printer-core", "v0.2.0", "../../bad")):
                with self.assertRaises(ValueError):
                    service.submit("11111111-1111-4111-8111-111111111111", target, tag, ui_tag)
            acquire.assert_not_called()

    def test_config_reset_selection_is_validated_and_persisted_with_combined_update(self):
        selection = [{"path": key, "sha256": "a" * 64} for key in (
            "config/profiles/treed_v2_corexy_v1/service_fans.cfg", "config/moonraker/base/00-core.conf",
            "config/moonraker.conf", "klipper/treed_driver_mode.py", "moonraker/treed_update.py",
            "camera/stream_detect.py", "vision/service.py", "vision/models.json", "sbin/treed-core-update",
            "sbin/treed-loading-screen")]
        request = "11111111-1111-4111-8111-111111111111"
        with patch.object(service.os, "geteuid", return_value=0, create=True), \
             patch.object(service, "acquire", return_value=FakeLock()), \
             patch.object(service.fcntl, "flock"), patch.object(service.subprocess, "run"):
            self.assertEqual(service.submit(request, "printer-core", "v0.2.0", "ui-main-123-1", json.dumps(selection)), 0)
        self.assertEqual(service.read_state()["resetConfigs"], selection)
        self.assertEqual(service.read_state()["pendingUiTag"], "ui-main-123-1")
        with patch.object(service.os, "geteuid", return_value=0, create=True), \
             patch.object(service, "acquire") as acquire:
            for invalid in ([{"path": "config/../printer.cfg", "sha256": "a" * 64}],
                            [{"path": "camera/unknown.py", "sha256": "a" * 64}],
                            [{"path": "vision/unknown.py", "sha256": "a" * 64}],
                            [{"path": "/etc/shadow", "sha256": "a" * 64}], selection * 2, False):
                with self.assertRaises(ValueError):
                    service.submit(request, "printer-core", "v0.2.0", "", json.dumps(invalid))
            with self.assertRaises(ValueError):
                service.submit(request, "printer-ui", "ui-main-123-1", "", json.dumps(selection))
            acquire.assert_not_called()

    def test_worker_starts_splash_after_idle_check_and_stops_before_unlock(self):
        service.write_state({"operationId": "op", "requestId": "request", "targetId": "printer-ui",
                             "targetTag": "ui-main-123-1", "status": "queued", "busy": True})
        events = []
        class OrderedLock(FakeLock):
            def close(self):
                events.append("unlock")
        service.fcntl.flock = lambda *_args: None
        self.loading_screen.stop()

        def mocked_run(command, **_kwargs):
            if len(command) == 3 and Path(command[1]) == service.LOADING_SCREEN:
                events.append("splash:" + command[2])
            else:
                events.append("apply")
            return types.SimpleNamespace(returncode=0)

        with (patch.object(service.os, "geteuid", return_value=0, create=True),
              patch.object(service, "acquire", return_value=OrderedLock()),
              patch.object(service.os, "fchmod", lambda *_args: None, create=True),
              patch.object(service, "printer_is_idle", side_effect=lambda: events.append("idle") or True),
              patch.object(service, "loading_screen", wraps=service.loading_screen),
              patch.object(service.subprocess, "run", side_effect=mocked_run)):
            self.assertEqual(service.run_worker(), 0)
        self.assertLess(events.index("idle"), events.index("splash:start"))
        self.assertLess(events.index("splash:start"), events.index("apply"))
        self.assertLess(events.index("apply"), events.index("splash:stop"))
        self.assertLess(events.index("splash:stop"), events.index("unlock"))


class PackageExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        previous = service.STATE, service.LOG
        self.addCleanup(lambda: (setattr(service, 'STATE', previous[0]), setattr(service, 'LOG', previous[1])))
        service.STATE, service.LOG = self.root / 'state.json', self.root / 'worker.log'
        self.state = {'operationId': str(uuid.uuid4()), 'targetId': 'printer-core', 'targetTag': 'v0.2.0',
                      'status': 'queued', 'phase': 'queued', 'busy': True, 'history': [], 'attemptGeneration': 0}
        service.write_state(self.state)
        self.code = (b'import json, os, sys\nfrom pathlib import Path\n'
                     b'Path(os.environ["TEST_UPDATER_RECEIPT"]).write_text(json.dumps(sys.argv[1:]))\n'
                     b'print(json.dumps({"status":"applied","resultCode":"fixture_updated"}))\n')
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {
            'TREED_CORE_RELEASE_API_URL': 'https://api.github.com/repos/test/printer-core/releases',
            'TREED_SHELL_RELEASE_API_URL': 'https://api.github.com/repos/test/printer-ui/releases',
            'TEST_UPDATER_RECEIPT': str(self.root / 'receipt.json')}))
        self.network = self.stack.enter_context(patch.object(service.urllib.request, 'urlopen', side_effect=self.open_url))
        self.make_package()

    def make_package(self, metadata=None, declared=True):
        target = self.state['targetId']
        self.asset = 'treed-core-runtime.zip' if target == 'printer-core' else 'treed-shell-ui.zip'
        name = 'manifest.json' if target == 'printer-core' else 'treed-shell-ui-manifest.json'
        # Bootstrap не интерпретирует будущий формат данных самого installer.
        manifest = {'schema': 999, 'futureComponent': {'enabled': True}}
        if declared:
            manifest['updater'] = metadata or {'protocol': 1, 'entrypoint': 'private/engine.py',
                                               'sha256': hashlib.sha256(self.code).hexdigest()}
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as bundle:
            bundle.writestr(name, json.dumps(manifest))
            bundle.writestr('private/engine.py', self.code)
        self.archive = buffer.getvalue()
        self.url = f"https://github.com/test/{target}/releases/download/{self.state['targetTag']}/{self.asset}"
        self.release = {'tag_name': self.state['targetTag'], 'draft': False, 'prerelease': False,
                        'assets': [{'name': self.asset, 'digest': 'sha256:' + hashlib.sha256(self.archive).hexdigest(),
                                    'browser_download_url': self.url}]}

    def open_url(self, request, **kwargs):
        url = getattr(request, 'full_url', request)
        return io.BytesIO(json.dumps(self.release).encode() if url.startswith('https://api.github.com/') else self.archive)

    def test_core_and_ui_execute_only_their_verified_package_runner(self):
        for target, tag in (('printer-core', 'v0.2.0'), ('printer-ui', 'ui-main-2-1')):
            self.state.update(targetId=target, targetTag=tag)
            service.write_state(self.state)
            self.make_package()
            execution = service.package_execution(self.state)
            self.assertNotEqual(execution[0][0], service.CORE_APPLY)
            self.assertEqual(service.execute_updater(self.state, execution)['status'], 'applied')
            self.assertEqual(json.loads((self.root / 'receipt.json').read_text()), ['apply', tag, self.state['operationId']])

    def test_recovery_reuses_pinned_bytes_without_network_or_installed_runner(self):
        execution = service.package_execution(self.state)
        self.network.side_effect = AssertionError('Recovery must not download another runner')
        self.state = service.read_state()
        self.state.update(status='installing', phase='installing')
        service.write_state(self.state)
        recovered = service.package_execution(self.state, recovery=True)
        self.assertEqual(execution[0][1], recovered[0][1])
        service.execute_updater(self.state, recovered)
        self.assertEqual(json.loads((self.root / 'receipt.json').read_text()), ['recover', self.state['operationId']])

    def test_power_loss_before_pin_never_executes_incomplete_cache(self):
        write = service.write_executor_file

        def power_loss(path, data):
            if path.name == 'executor.json':
                raise KeyboardInterrupt('power loss before pin')
            write(path, data)

        with patch.object(service, 'write_executor_file', side_effect=power_loss), self.assertRaises(KeyboardInterrupt):
            service.package_execution(self.state)
        self.assertFalse((self.root / 'receipt.json').exists())
        execution = service.package_execution(service.read_state())
        self.assertEqual(service.execute_updater(self.state, execution)['status'], 'applied')

    def test_corrupt_cache_and_lost_pin_never_fall_back_to_installed_code(self):
        execution = service.package_execution(self.state)
        runner = Path(execution[0][1])
        original = runner.read_bytes()
        runner.write_bytes(b'raise SystemExit("corrupt")')
        with self.assertRaises(ValueError):
            service.package_execution(service.read_state(), recovery=True)
        runner.write_bytes(original)
        runner.with_name('package.zip').write_bytes(b'corrupt archive')
        with self.assertRaises(ValueError):
            service.package_execution(service.read_state(), recovery=True)
        runner.with_name('executor.json').unlink()
        with self.assertRaisesRegex(ValueError, 'утрачен'):
            service.package_execution(service.read_state(), recovery=True)
        self.assertFalse((self.root / 'receipt.json').exists())

    def test_invalid_protocol_hash_path_source_and_tag_do_not_execute(self):
        for metadata in ({'protocol': 2}, {'protocol': True},
                         {'protocol': 1, 'entrypoint': '../engine.py', 'sha256': 'a' * 64},
                         {'protocol': 1, 'entrypoint': 'private/engine.py', 'sha256': 'a' * 64}):
            self.make_package(metadata)
            with self.subTest(metadata=metadata), self.assertRaises(ValueError):
                service.package_execution(self.state)
        self.make_package()
        self.release['assets'][0]['digest'] = 'sha256:' + '0' * 64
        with self.assertRaises(ValueError):
            service.package_execution(self.state)
        self.make_package()
        self.release['assets'][0]['browser_download_url'] = 'https://github.com/other/repo/releases/download/v0.2.0/a.zip'
        with self.assertRaises(ValueError):
            service.package_execution(self.state)
        self.make_package()
        self.release['tag_name'] = 'v0.3.0'
        with self.assertRaises(ValueError):
            service.package_execution(self.state)
        self.assertFalse((self.root / 'receipt.json').exists())

    def test_old_package_uses_legacy_path_but_malformed_new_metadata_does_not(self):
        self.make_package(declared=False)
        self.assertIsNone(service.package_execution(self.state))
        self.make_package({'protocol': 1})
        with self.assertRaises(ValueError):
            service.package_execution(self.state)

    def test_pin_identity_and_active_operation_cannot_be_replaced(self):
        execution = service.package_execution(self.state)
        pin_path = Path(execution[0][1]).with_name('executor.json')
        pin = json.loads(pin_path.read_text())
        pin['targetTag'] = 'v0.3.0'
        pin_path.write_text(json.dumps(pin))
        with self.assertRaises(ValueError):
            service.package_execution(service.read_state(), recovery=True)


if __name__ == "__main__":
    unittest.main()
