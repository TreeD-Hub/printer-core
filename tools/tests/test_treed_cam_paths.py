"""Проверка путей камеры в изолированной установке; без сервисов и устройства."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


# Блок 1: Изолированная раскладка loader и подмена только внешнего снимка.
class CameraPathsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "non_pi_user"
        self.bin = self.home / "treed/cam/bin"
        self.bin.mkdir(parents=True)
        source = Path(__file__).resolve().parents[2] / "runtime-scripts/treed-cam"
        self.marker = self.root / "session_marker"
        for name in ("cam_env.sh", "session_start.sh", "snapshot.sh"):
            content = (source / name).read_text(encoding="utf-8")
            content = content.replace("/tmp/treed_cam_session_dir", str(self.marker))
            (self.bin / name).write_text(content, encoding="utf-8")
        self.mock_bin = self.root / "mock_bin"
        self.mock_bin.mkdir()
        curl = self.mock_bin / "curl"
        curl.write_text('#!/bin/bash\nwhile (( $# )); do\n  if [[ "$1" == "-o" ]]; then printf mock > "$2"; exit 0; fi\n  shift\ndone\nexit 1\n', encoding="utf-8")
        curl.chmod(0o755)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("PI_", "TREED_CAM_"))}
        self.env["PATH"] = str(self.mock_bin) + os.pathsep + self.env["PATH"]

    def run_script(self, name, *args):
        subprocess.run(["bash", str(self.bin / name), *args], env=self.env, check=True,
                       capture_output=True, text=True)

    # Блок 2: Новый пользователь и существующий explicit override используют один корень.
    def test_default_home_and_snapshot(self):
        self.run_script("session_start.sh", "sample.gcode")
        session = Path(self.marker.read_text().strip())
        self.assertEqual(session.parent, self.home / "treed/cam/prints")
        self.assertEqual(len(list(session.glob("*.jpg"))), 1)
        self.run_script("snapshot.sh")
        self.assertEqual(len(list(session.glob("*.jpg"))), 2)

    def test_explicit_home(self):
        override = self.root / "custom_home"
        self.env["PI_HOME"] = str(override)
        self.run_script("session_start.sh", "sample.gcode")
        self.assertEqual(Path(self.marker.read_text().strip()).parent, override / "treed/cam/prints")

    def test_runtime_env_uses_install_home(self):
        config = self.home / "treed/cam/config"
        config.mkdir()
        (config / "runtime.env").write_text("TREED_CAM_SNAPSHOT_URL=http://fixture/snapshot\n", encoding="utf-8")
        command = 'source "$1"; treed_cam_load_runtime_env; treed_cam_snapshot_url'
        result = subprocess.run(["bash", "-c", command, "bash", str(self.bin / "cam_env.sh")],
                                env=self.env, capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), "http://fixture/snapshot")


# Блок 3: Только адресная проверка, без запуска loader и перезапуска сервисов.
if __name__ == "__main__":
    unittest.main()
