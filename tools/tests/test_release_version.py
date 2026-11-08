"""Версия выпуска: реальный Git и resolver, GitHub API заменён без публикации."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


# Блок 1: Проверяем публичные правила названий и расчёта версии.
ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("release_version", ROOT / "tools/release_version.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class VersionTests(unittest.TestCase):
    def test_short_prefixes_ignore_case_colon_and_spaces(self):
        for prefix, bump in (("фикс", 2), ("патч", 1), ("релиз", 0)):
            for title in (prefix, prefix.upper(), prefix + ":", prefix + " обновления",
                          prefix.upper() + ": обновления", "  " + prefix + " : обновления  "):
                with self.subTest(title=title):
                    self.assertEqual(release.title_bump(title), bump)

    def test_unknown_or_partial_prefix_is_rejected(self):
        for title in ("", "dev", "Фиксы", "фиксировать", "новый фикс", "фича", "мажор"):
            with self.subTest(title=title), self.assertRaises(ValueError):
                release.title_bump(title)

    def test_transition_from_existing_ci_versions(self):
        for prefix, expected in (("фикс", "0.49.2"), ("патч", "0.50.0"), ("релиз", "1.0.0")):
            with self.subTest(prefix=prefix):
                version, reserve, _ = release.select_version(
                    release.title_bump(prefix), "new", [], ["v0.9.9", "v0.49.1"], [], "0.2.0")
                self.assertEqual(version, expected)
                self.assertTrue(reserve)

    def test_drafts_reserve_numbers_and_prereleases_do_not_set_base(self):
        releases = [{"tag_name": "v0.49.2", "draft": True, "target_commitish": "other"},
                    {"tag_name": "v99.0.0", "prerelease": True}, {"tag_name": "nightly"}]
        version, _, _ = release.select_version(2, "new", releases, ["v0.49.1"], [], "0.2.0")
        self.assertEqual(version, "0.49.3")

    def test_rerun_reuses_draft_or_published_release_even_after_title_change(self):
        for draft in (True, False):
            for target, tags in (("same", []), ("treed-v2", ["v0.49.2"])):
                with self.subTest(draft=draft, target=target):
                    releases = [{"tag_name": "v0.49.2", "draft": draft, "target_commitish": target}]
                    version, reserve, _ = release.select_version(0, "same", releases, [], tags, "0.2.0")
                    self.assertEqual(version, "0.49.2")
                    self.assertFalse(reserve)

    def test_old_draft_cannot_be_published_after_newer_release(self):
        releases = [{"tag_name": "v0.49.2", "draft": True, "target_commitish": "old"},
                    {"tag_name": "v0.49.3", "draft": False, "target_commitish": "new"}]
        with self.assertRaisesRegex(ValueError, "более новый"):
            release.select_version(2, "old", releases, [], [], "0.2.0")

    def test_bootstrap_is_used_only_without_release_versions(self):
        self.assertEqual(release.select_version(2, "new", [], [], [], "0.2.0")[0], "0.2.1")
        with self.assertRaises(ValueError):
            release.select_version(2, "new", [], [], [], "broken")


# Блок 2: Запускаем entrypoint с настоящими refs; подменяем только границу GitHub.
class WorkflowTests(unittest.TestCase):
    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args],
                                       text=True, encoding="utf-8", stderr=subprocess.PIPE).strip()

    def commit(self, message):
        self.git("add", ".")
        self.git("-c", "user.name=Release Test", "-c", "user.email=release@example.invalid",
                 "-c", "commit.gpgsign=false", "commit", "-qm", message)
        return self.git("rev-parse", "HEAD")

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="release-version-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.git("init", "-q")
        (self.root / "VERSION").write_text("0.2.0\n", encoding="utf-8")
        old_sha = self.commit("baseline")
        self.git("tag", "v0.49.1")
        (self.root / "change.txt").write_text("new\n", encoding="utf-8")
        self.sha = self.commit("merged PR")
        self.pr = {"title": "Фикс: обновления", "number": 13, "merged_at": "2026-11-08T00:00:00Z",
                   "base": {"ref": "treed-v2"}, "merge_commit_sha": self.sha}
        self.releases = [{"tag_name": "v0.49.1", "draft": False, "target_commitish": old_sha}]
        self.created = []
        self.env_file = self.root / "github-env"
        self.event_file = self.root / "event.json"
        self.env = {"GITHUB_REF": "refs/heads/treed-v2", "GITHUB_SHA": self.sha,
                    "GITHUB_REPOSITORY": "example/printer-core", "GITHUB_ENV": str(self.env_file),
                    "GITHUB_EVENT_PATH": str(self.event_file)}

    def github(self, *args):
        if args[0] == "api":
            # Несколько страниц обязаны участвовать в расчёте версии.
            rows = [self.pr] if "/commits/" in args[-1] else self.releases
            return json.dumps([[], rows])
        self.assertEqual(args[:2], ("release", "create"))
        self.created.append(args)
        self.releases.append({"tag_name": args[2], "draft": True,
                              "target_commitish": args[args.index("--target") + 1]})
        return ""

    def run_resolver(self, *args):
        with contextlib.chdir(self.root), patch.dict(os.environ, self.env), \
                patch.object(release, "gh", side_effect=self.github), contextlib.redirect_stdout(io.StringIO()):
            release.main(list(args))

    def test_merged_pr_pins_draft_and_stamps_same_version_on_retry(self):
        self.run_resolver()
        self.pr["title"] = "РЕЛИЗ"
        self.run_resolver()
        self.assertEqual(len(self.created), 1)
        self.assertEqual(self.created[0][2], "v0.49.2")
        self.assertIn("--draft", self.created[0])
        self.assertEqual((self.root / "VERSION").read_text(), "0.49.2\n")
        self.assertEqual(self.env_file.read_text(),
                         "MAIN_SHELL_VERSION=0.49.2\nEXPECTED_TAG=v0.49.2\n" * 2)

    def test_title_never_enters_shell_or_release_arguments(self):
        self.pr["title"] = "ФИКС: $(touch pwned); `anything`"
        self.run_resolver()
        self.assertFalse((self.root / "pwned").exists())
        self.assertNotIn(self.pr["title"], self.created[0])

    def test_direct_push_wrong_target_and_unmerged_pr_fail_before_reservation(self):
        for change in ({"merged_at": None}, {"base": {"ref": "dev"}},
                       {"merge_commit_sha": "0" * 40}, {"title": "dev"}):
            with self.subTest(change=change), patch.dict(self.pr, change), self.assertRaises(ValueError):
                self.run_resolver()
        self.assertEqual(self.created, [])
        self.assertFalse(self.env_file.exists())
        self.assertEqual((self.root / "VERSION").read_text(), "0.2.0\n")

    def test_wrong_checkout_or_dispatch_branch_is_rejected(self):
        for change in ({"GITHUB_REF": "refs/heads/dev"}, {"GITHUB_SHA": "0" * 40}):
            with self.subTest(change=change), patch.dict(self.env, change), self.assertRaises(ValueError):
                self.run_resolver()
        self.assertEqual(self.created, [])

    def test_github_failure_does_not_stamp_an_unreserved_version(self):
        for command in ("api", "release"):
            github = self.github
            def fail(*args):
                if args[0] == command:
                    raise subprocess.CalledProcessError(1, ["gh", *args])
                return github(*args)
            with self.subTest(command=command), contextlib.chdir(self.root), \
                    patch.dict(os.environ, self.env), patch.object(release, "gh", side_effect=fail), \
                    self.assertRaises(subprocess.CalledProcessError):
                release.main([])
        self.assertEqual(self.created, [])
        self.assertFalse(self.env_file.exists())
        self.assertEqual((self.root / "VERSION").read_text(), "0.2.0\n")

    def test_stale_unreserved_run_cannot_release_old_code_with_new_number(self):
        (self.root / "later.txt").write_text("later\n", encoding="utf-8")
        newer = self.commit("later release")
        self.git("tag", "v0.50.0")
        self.releases.append({"tag_name": "v0.50.0", "draft": False, "target_commitish": newer})
        self.git("checkout", "--detach", self.sha)
        with self.assertRaisesRegex(ValueError, "не содержит"):
            self.run_resolver()
        self.assertEqual(self.created, [])

    def test_published_rerun_keeps_version_even_after_newer_release(self):
        self.git("tag", "v0.49.2")
        self.releases.extend([{"tag_name": "v0.49.2", "draft": False, "target_commitish": "treed-v2"},
                              {"tag_name": "v0.50.0", "draft": False, "target_commitish": "newer"}])
        self.run_resolver()
        self.assertEqual(self.created, [])
        self.assertEqual((self.root / "VERSION").read_text(), "0.49.2\n")

    def test_pr_event_validates_without_github_or_publication(self):
        for title, valid in (("ПаТч обновления", True), ("dev", False)):
            self.event_file.write_text(json.dumps({"pull_request": {"title": title}}), encoding="utf-8")
            with self.subTest(title=title):
                if valid:
                    self.run_resolver("--check-title")
                else:
                    with self.assertRaises(ValueError):
                        self.run_resolver("--check-title")
        self.assertEqual(self.created, [])
        self.assertFalse(self.env_file.exists())


if __name__ == "__main__":
    unittest.main()
