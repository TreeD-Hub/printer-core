"""Офлайн-проверка read-only API резонансов; без Moonraker и устройства."""

# Блок 1: Загрузка реального компонента и временные CSV.
import asyncio
import importlib.util
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "treed_resonance", ROOT / "moonraker/components/treed_resonance.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
CSV = "freq,psd_x,psd_y,psd_z,psd_xyz,shapers:,ei(51.4)\n0,1,2,3,6,,1\n1,2,3,4,9,,0.5\n"


# Блок 2: Стабильный файл, отсутствие данных, формат и границы чтения.
class FileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "resonance.csv"
        self.path.write_bytes(CSV.encode("utf-8"))

    def test_source_and_revision(self):
        first = module._read_axis(self.path)
        self.assertEqual(first["csv"], CSV)
        self.assertEqual(first, module._read_axis(self.path))
        timestamp = self.path.stat().st_mtime_ns
        os.utime(self.path, ns=(timestamp + 10_000_000, timestamp + 10_000_000))
        self.assertNotEqual(first["revision"], module._read_axis(self.path)["revision"])
        before = module._read_axis(self.path)
        self.path.write_bytes(CSV.replace(",6,", ",7,").encode("utf-8"))
        os.utime(self.path, ns=(timestamp + 10_000_000, timestamp + 10_000_000))
        self.assertNotEqual(before["revision"], module._read_axis(self.path)["revision"])

    def test_missing_and_nonregular(self):
        self.assertIsNone(module._read_axis(self.path.parent / "missing.csv"))
        with self.assertRaises(ValueError):
            module._read_axis(self.path.parent)

    def test_symlink_rejected_before_open(self):
        with patch.object(Path, "lstat", return_value=SimpleNamespace(st_mode=0o120777)):
            with patch.object(module.os, "open") as opened:
                with self.assertRaises(ValueError):
                    module._read_axis(self.path)
                opened.assert_not_called()

    def test_size_limit(self):
        with patch.object(module, "MAX_CSV_BYTES", 10):
            with self.assertRaises(ValueError):
                module._read_axis(self.path)

    @unittest.skipUnless(os.name == "posix", "Граница O_NOFOLLOW проверяется на Linux")
    def test_symlink_replacement_before_open(self):
        target = self.path.parent / "target.csv"
        target.write_bytes(CSV.encode("utf-8"))
        original_open = module.os.open

        def replaced_open(path, flags):
            self.path.unlink()
            self.path.symlink_to(target)
            return original_open(path, flags)

        with patch.object(module.os, "open", side_effect=replaced_open):
            with self.assertRaises(OSError):
                module._read_axis(self.path)

    @unittest.skipUnless(os.name == "posix", "FIFO проверяется на Linux")
    def test_fifo_replacement_does_not_block(self):
        original_open = module.os.open

        def replaced_open(path, flags):
            self.path.unlink()
            os.mkfifo(self.path)
            return original_open(path, flags)

        with patch.object(module.os, "open", side_effect=replaced_open):
            with self.assertRaises(ValueError):
                module._read_axis(self.path)

    def test_malformed_csv(self):
        for text in ("", "secret\nvalue\n", CSV.replace("0,1", "nan,1"),
                     CSV.replace("1,2,3,4,9", "0,2,3,4,9"), CSV.replace("0,1,2,3,6", "0,1,2,3")):
            with self.subTest(text=text):
                self.path.write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError):
                    module._read_axis(self.path)

    def test_rewrite_while_reading_rejected(self):
        original = module.os.fstat
        calls = 0

        def changing_stat(fd):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.path.write_text(CSV + "2,3,4,5,12,,0.4\n", encoding="utf-8")
            return original(fd)

        with patch.object(module.os, "fstat", side_effect=changing_stat):
            with self.assertRaises(ValueError):
                module._read_axis(self.path)


# Блок 3: Enum запроса, отсутствующая ось и явная ошибка API.
class EndpointTests(unittest.TestCase):
    def setUp(self):
        self.server = Mock()
        self.server.error.side_effect = lambda message, status: ValueError(f"{status}:{message}")
        self.component = module.TreeDResonance(SimpleNamespace(get_server=lambda: self.server))

    def request(self, mode):
        return SimpleNamespace(get_str=lambda name, default: mode)

    def test_fixed_endpoint_and_mode(self):
        self.server.register_endpoint.assert_called_once_with(
            "/server/treed/resonances", ["GET"], self.component._handle_resonances
        )
        with patch.object(module, "_read_axis", return_value=None) as read:
            result = asyncio.run(self.component._handle_resonances(self.request("light")))
            self.assertEqual(result, {"mode": "light", "axes": {"x": None, "y": None}})
            self.assertEqual([call.args[0] for call in read.call_args_list], list(module.RESULT_FILES["light"].values()))
        with patch.object(module, "_read_axis") as read:
            with self.assertRaisesRegex(ValueError, "400:"):
                asyncio.run(self.component._handle_resonances(self.request("../../etc/passwd")))
            read.assert_not_called()

    def test_read_error_is_visible(self):
        with patch.object(module, "_read_axis", side_effect=ValueError("invalid CSV")):
            with self.assertRaisesRegex(ValueError, "409:.*invalid CSV"):
                asyncio.run(self.component._handle_resonances(self.request("full")))


if __name__ == "__main__":
    unittest.main()
