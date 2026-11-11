"""Проверяет формат и выбор зафиксированных Python-зависимостей loader."""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from importlib import metadata


ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "loader/lib/python-runtime-lock.sh"


def bash_path():
    if os.name == "nt":
        git = shutil.which("git")
        if git:
            candidate = Path(git).parent.parent / "bin/bash.exe"
            if candidate.is_file():
                return str(candidate)
    return shutil.which("bash")


class RuntimeLockTest(unittest.TestCase):
    def lock(self, component):
        bash = bash_path()
        self.assertIsNotNone(bash, "bash is required to inspect the runtime lock")
        path = HELPER.as_posix()
        if os.name == "nt":
            path = "/" + path[0].lower() + path[2:]
        return subprocess.run([bash, "-c", '. "$1"; python_runtime_lock "$2"',
                               "lock-test", path, component], capture_output=True, text=True)

    def test_locks_are_exact_and_contain_required_packages(self):
        for component, required in {
            "klipper": {"numpy": "1.25.2", "matplotlib": "3.10.9", "cffi": "1.14.6"},
            "moonraker": {"tornado": "6.5.6", "dbus-fast": "3.1.2", "apprise": "1.9.8"},
            "vision": {"torch": "2.11.0", "torchvision": "0.26.0", "rfdetr": "1.11.0", "pillow": "12.3.0"},
        }.items():
            with self.subTest(component=component):
                result = self.lock(component)
                self.assertEqual(result.returncode, 0, result.stderr)
                rows = result.stdout.splitlines()
                self.assertTrue(all(re.fullmatch(r"[A-Za-z0-9_.-]+==[A-Za-z0-9_.+-]+", row)
                                    for row in rows))
                values = {re.sub(r"[-_.]+", "-", name.lower()): version
                          for name, version in (row.split("==") for row in rows)}
                self.assertEqual(len(values), len(rows), "duplicate package")
                required.update({"pip": "26.3", "setuptools": "80.10.2", "wheel": "0.48.0"})
                for name, expected in required.items():
                    self.assertEqual(values.get(name), expected)

    def test_unknown_runtime_is_rejected(self):
        self.assertNotEqual(self.lock("unknown").returncode, 0)

    def test_vision_validation_rejects_transitive_drift_and_accepts_cpu_wheels(self):
        source = (ROOT / "loader/steps/treed-cam.sh").read_text(encoding="utf-8")
        start = source.index("vision_python_valid() {")
        function = source[start:source.index("\n}\n", start) + 3]
        helper, python = HELPER.as_posix(), Path(sys.executable).as_posix()
        if os.name == "nt":
            helper, python = ("/" + path[0].lower() + path[2:] for path in (helper, python))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for row in self.lock("vision").stdout.splitlines():
                name, version = row.split("==")
                if name in ("torch", "torchvision"):
                    version += "+cpu"
                info = root / (name.replace("-", "_") + "-" + version + ".dist-info")
                info.mkdir()
                (info / "METADATA").write_text(f"Name: {name}\nVersion: {version}\n", encoding="utf-8")
            command = [bash_path(), "-c", '. "$1"; ' + function + '\nvision_python_valid "$2"',
                       "vision-test", helper, python]
            env = dict(os.environ, PYTHONPATH=str(root))
            self.assertEqual(subprocess.run(command, env=env, capture_output=True).returncode, 0)
            numpy = next(root.glob("numpy-*/METADATA"))
            numpy.write_text("Name: numpy\nVersion: 0.0.0\n", encoding="utf-8")
            self.assertNotEqual(subprocess.run(command, env=env, capture_output=True).returncode, 0)

    def test_installed_version_check_rejects_mismatch(self):
        bootstrap = (ROOT / "loader/steps/runtime-bootstrap.sh").read_text(encoding="utf-8")
        check = bootstrap.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / "lock.txt"
            lock.write_text("pip==" + metadata.version("pip") + "\n", encoding="utf-8")
            self.assertEqual(subprocess.run([sys.executable, "-c", check, str(lock)],
                                            capture_output=True).returncode, 0)
            lock.write_text("pip==0.0.0\n", encoding="utf-8")
            mismatch = subprocess.run([sys.executable, "-c", check, str(lock)],
                                      capture_output=True, text=True)
            self.assertNotEqual(mismatch.returncode, 0)
            self.assertIn("expected 0.0.0", mismatch.stderr)


if __name__ == "__main__":
    unittest.main()
