"""Read-only: настоящий GitHub API resolver loader; сеть и ожидания заменены."""
from contextlib import redirect_stderr, redirect_stdout
from io import BytesIO, StringIO
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError


# Блок 1: Выполняем Python-блок installer без provisioning и сетевых запросов.
ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "loader/steps/treed-shell-install.sh"
RESOLVER = INSTALLER.read_text(encoding="utf-8").split(
    "resolve_ui_archive_url() {", 1)[1].split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
API_URL = "https://api.github.com/repos/TreeD-Hub/printer-ui/releases"
ASSET_URL = "https://github.com/TreeD-Hub/printer-ui/releases/download/ui-main-1-1/treed-shell-ui.zip"
RELEASE = {"tag_name": "ui-main-1-1", "assets": [
    {"name": "treed-shell-ui.zip", "browser_download_url": ASSET_URL}]}


class ShellReleaseResolverTests(unittest.TestCase):
    def run_resolver(self, outcomes):
        responses = [value if isinstance(value, BaseException) else
                     BytesIO(json.dumps(value).encode("utf-8")) for value in outcomes]
        self.stdout, self.stderr = StringIO(), StringIO()
        with patch.object(sys, "argv", ["-", API_URL, "ui-main-", "treed-shell-ui.zip"]), \
                patch("urllib.request.urlopen", side_effect=responses) as self.urlopen, \
                patch("time.sleep") as self.sleep, \
                redirect_stdout(self.stdout), redirect_stderr(self.stderr):
            exec(compile(RESOLVER, str(INSTALLER), "exec"), {})

    def assert_success(self, outcomes):
        with self.assertRaises(SystemExit) as stopped:
            self.run_resolver(outcomes)
        self.assertEqual(stopped.exception.code, 0)
        self.assertEqual(self.stdout.getvalue(), ASSET_URL + "\n")

    # Блок 2: Успех и повторы временных сбоев сохраняют чистый URL в stdout.
    def test_success_filters_releases_without_retry(self):
        self.assert_success([[{**RELEASE, "draft": True},
                              {**RELEASE, "prerelease": True},
                              {**RELEASE, "tag_name": "development"}, RELEASE]])
        self.assertEqual(self.urlopen.call_count, 1)
        self.sleep.assert_not_called()
        self.assertEqual(self.stderr.getvalue(), "")
        self.assertEqual(self.urlopen.call_args.kwargs["timeout"], 30)

    def test_503_then_success(self):
        self.assert_success([HTTPError(API_URL, 503, "Service Unavailable", {}, None), [RELEASE]])
        self.assertEqual(self.urlopen.call_count, 2)
        self.sleep.assert_called_once_with(2)
        self.assertIn("503", self.stderr.getvalue())

    def test_timeouts_then_success(self):
        self.assert_success([TimeoutError("timed out"), URLError(TimeoutError("timed out")), [RELEASE]])
        self.assertEqual(self.urlopen.call_count, 3)
        self.assertEqual(self.sleep.call_count, 2)

    # Блок 3: Исчерпание попыток и постоянные ошибки не дают ложного успеха.
    def test_503_exhaustion_raises_last_error(self):
        errors = [HTTPError(API_URL, 503, "Service Unavailable", {}, None) for _ in range(3)]
        with self.assertRaises(HTTPError) as stopped:
            self.run_resolver(errors)
        self.assertIs(stopped.exception, errors[-1])
        self.assertEqual(self.urlopen.call_count, 3)
        self.assertEqual(self.sleep.call_count, 2)
        self.assertEqual(self.stdout.getvalue(), "")

    def test_timeout_exhaustion_raises_last_error(self):
        errors = [TimeoutError("timed out") for _ in range(3)]
        with self.assertRaises(TimeoutError) as stopped:
            self.run_resolver(errors)
        self.assertIs(stopped.exception, errors[-1])
        self.assertEqual(self.urlopen.call_count, 3)
        self.assertEqual(self.sleep.call_count, 2)
        self.assertEqual(self.stdout.getvalue(), "")

    def test_permanent_http_error_fails_without_retry(self):
        for code in (401, 403, 404):
            with self.subTest(code=code), self.assertRaises(HTTPError):
                self.run_resolver([HTTPError(API_URL, code, "Rejected", {}, None)])
            self.assertEqual(self.urlopen.call_count, 1)
            self.sleep.assert_not_called()

    def test_non_timeout_url_error_fails_without_retry(self):
        with self.assertRaises(URLError):
            self.run_resolver([URLError("certificate verify failed")])
        self.assertEqual(self.urlopen.call_count, 1)
        self.sleep.assert_not_called()

    def test_invalid_response_fails_without_retry(self):
        with patch("json.load", side_effect=json.JSONDecodeError("invalid", "", 0)), \
                self.assertRaises(json.JSONDecodeError):
            self.run_resolver([[RELEASE]])
        self.assertEqual(self.urlopen.call_count, 1)
        self.sleep.assert_not_called()

    def test_missing_release_asset_fails_without_retry(self):
        with self.assertRaises(SystemExit) as stopped:
            self.run_resolver([[]])
        self.assertIn("no release asset", str(stopped.exception.code))
        self.assertEqual(self.urlopen.call_count, 1)
        self.sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
