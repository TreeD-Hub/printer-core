"""Адресная офлайн-проверка оценки и метаданных; без сервисов и устройства."""

import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[2]


# Блок 1: Настоящие runtime-модули; подменены только Moonraker и Klipper API.
def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


feedback = load_module("treed_print_feedback", "moonraker/components/treed_print_feedback.py")
metadata = load_module("session_metadata", "runtime-scripts/treed-cam/session_metadata.py")


class Request:
    def __init__(self, action="GET", **args):
        self.action = action
        self.args = args

    def get(self, key, default=None):
        return self.args.get(key, default)

    def get_action(self):
        return self.action


class FeedbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.frames = self.root / "prints"
        self.frames.mkdir()
        self.marker = self.root / "session_marker"
        self.filename = "папка с пробелами/part test.gcode"
        self.objects = {
            "print_stats": {"state": "complete", "filename": self.filename},
            "gcode_macro _TREED_CAM_STATE": {"enabled": 0, "session_token": 101},
        }
        self.session = self.create_session("part__20261011_120000_AAAAAA", self.filename, token=101)
        self.api = NS(query_objects=AsyncMock(return_value=self.objects))
        self.endpoints = {}
        server = NS(
            lookup_component=lambda _: self.api,
            error=lambda message, code=400: ValueError((code, message)),
            register_endpoint=lambda path, methods, handler, **kwargs:
            self.endpoints.update({path: (methods, kwargs)}),
        )
        values = {"frames_root": str(self.frames), "session_file": str(self.marker)}
        config = NS(get_server=lambda: server, get=lambda key, default=None: values.get(key, default))
        self.config = config
        self.component = feedback.load_component(config)

    def create_session(self, session_id, filename, closed=True, token=None):
        directory = self.frames / session_id
        directory.mkdir()
        if token is None:
            token = self.objects["gcode_macro _TREED_CAM_STATE"]["session_token"] + 1
        self.objects["gcode_macro _TREED_CAM_STATE"]["session_token"] = token
        metadata.start_session(self.frames, directory, filename, token)
        (directory / "img_start.jpg").write_bytes(b"original JPEG bytes")
        if closed:
            metadata.stop_session(self.frames, directory, token)
        return directory

    async def get(self, **overrides):
        args = {"filename": self.filename}
        args.update(overrides)
        return await self.component._handle_feedback(Request(**args))

    async def post(self, **overrides):
        args = {"filename": self.filename, "sessionId": self.session.name, "rating": "good"}
        args.update(overrides)
        return await self.component._handle_feedback(Request("POST", **args))

    async def assert_error(self, operation, code):
        with self.assertRaises(ValueError) as caught:
            await operation
        self.assertEqual(caught.exception.args[0][0], code)

    # Блок 2: Raw endpoint, точное имя файла и атомарная оценка выбранного ID.
    async def test_get_post_and_reopen_keep_original_frames(self):
        self.assertEqual(self.endpoints["/server/treed/print-feedback"],
                         (["GET", "POST"], {"wrap_result": False}))
        expected = {"sessionId": self.session.name, "filename": self.filename, "rating": None}
        self.assertEqual(await self.get(), expected)
        expected["rating"] = "good"
        self.assertEqual(await self.post(), expected)
        original = self.session
        self.session = self.frames / "successful" / original.name
        self.assertFalse(original.exists())
        self.assertEqual(json.loads((self.session / "rating.json").read_text(encoding="utf-8")), expected)
        self.assertEqual(await self.get(), expected)
        self.assertEqual((self.session / "img_start.jpg").read_bytes(), b"original JPEG bytes")
        self.assertEqual((await self.post(rating="bad"))["rating"], "bad")
        failed = self.frames / "failed" / self.session.name
        self.assertFalse(self.session.exists())
        self.assertEqual((failed / "img_start.jpg").read_bytes(), b"original JPEG bytes")
        self.assertEqual((await self.get())["rating"], "bad")
        self.assertEqual((await self.post(rating="bad"))["rating"], "bad")

    async def test_bad_first_and_rating_changes_move_one_complete_folder(self):
        original = self.session
        (original / "notes.json").write_text('{"frame": "img_start.jpg"}', encoding="utf-8")
        await self.post(rating="bad")
        failed = self.frames / "failed" / original.name
        self.assertFalse(original.exists())
        session_bytes = (failed / "session.json").read_bytes()
        await self.post(rating="good")
        successful = self.frames / "successful" / original.name
        self.assertFalse(failed.exists())
        self.assertEqual((successful / "session.json").read_bytes(), session_bytes)
        self.assertEqual((successful / "notes.json").read_text(), '{"frame": "img_start.jpg"}')
        self.assertEqual((successful / "img_start.jpg").read_bytes(), b"original JPEG bytes")
        self.assertEqual((await self.get())["rating"], "good")

    async def test_failed_move_is_recovered_after_component_restart(self):
        with patch.object(feedback.Path, "rename", side_effect=OSError("disk failure")):
            await self.assert_error(self.post(), 409)
        self.assertTrue(self.session.is_dir())
        self.assertEqual((self.session / "img_start.jpg").read_bytes(), b"original JPEG bytes")
        self.assertEqual(json.loads((self.session / "rating.json").read_text())["rating"], "good")
        self.component = feedback.load_component(self.config)
        self.assertEqual((await self.get())["rating"], "good")
        self.assertFalse(self.session.exists())
        self.assertTrue((self.frames / "successful" / self.session.name / "img_start.jpg").is_file())
        await self.post()

    async def test_failed_reclassification_can_be_retried_or_changed(self):
        await self.post()
        successful = self.frames / "successful" / self.session.name
        with patch.object(feedback.Path, "rename", side_effect=OSError("disk failure")):
            await self.assert_error(self.post(rating="bad"), 409)
        self.assertTrue(successful.is_dir())
        self.assertEqual((await self.post())["rating"], "good")
        self.assertTrue(successful.is_dir())
        await self.post(rating="bad")
        self.assertFalse(successful.exists())
        self.assertEqual((await self.get())["rating"], "bad")

    async def test_failed_final_move_does_not_mix_opposite_labels(self):
        await self.post()
        original_rename = feedback.Path.rename

        def fail_classification(source, target):
            if target.parent == self.frames:
                return original_rename(source, target)
            raise OSError("disk failure")

        with patch.object(feedback.Path, "rename", autospec=True, side_effect=fail_classification):
            await self.assert_error(self.post(rating="bad"), 409)
        self.assertFalse((self.frames / "successful" / self.session.name).exists())
        self.assertFalse((self.frames / "failed" / self.session.name).exists())
        self.assertTrue(self.session.is_dir())
        self.assertEqual(json.loads((self.session / "rating.json").read_text())["rating"], "bad")
        self.assertEqual((await self.get())["rating"], "bad")
        self.assertFalse(self.session.exists())
        self.assertTrue((self.frames / "failed" / self.session.name / "img_start.jpg").is_file())

    async def test_destination_collision_preserves_both_directories(self):
        parent = self.frames / "successful"
        parent.mkdir()
        target = parent / self.session.name
        target.mkdir()
        (target / "keep.txt").write_text("untouched")
        await self.assert_error(self.post(), 409)
        self.assertEqual((target / "keep.txt").read_text(), "untouched")
        self.assertEqual((self.session / "img_start.jpg").read_bytes(), b"original JPEG bytes")
        self.assertFalse((self.session / "rating.json").exists())

    async def test_legacy_rating_is_classified_on_reopen(self):
        metadata.write_json(self.session / "rating.json", {
            "sessionId": self.session.name, "filename": self.filename, "rating": "bad",
        })
        self.assertEqual((await self.get())["rating"], "bad")
        self.assertFalse(self.session.exists())
        self.assertTrue((self.frames / "failed" / self.session.name / "img_start.jpg").is_file())

    async def test_unrated_and_still_owned_sessions_are_not_moved(self):
        self.assertIsNone((await self.get())["rating"])
        self.assertTrue(self.session.is_dir())
        self.assertFalse((self.frames / "successful").exists())
        self.marker.write_text(str(self.session), encoding="utf-8")
        await self.assert_error(self.post(), 409)
        self.assertTrue(self.session.is_dir())
        self.marker.unlink()
        await self.post()

    async def test_classification_rejects_symlinked_bucket(self):
        outside = self.root / "outside"
        outside.mkdir()
        try:
            (self.frames / "successful").symlink_to(outside, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"Создание ссылок недоступно: {error}")
        await self.assert_error(self.post(), 409)
        self.assertTrue(self.session.is_dir())
        self.assertEqual(list(outside.iterdir()), [])

    async def test_selected_old_session_can_be_saved_after_new_print_starts(self):
        selected = await self.get()
        second = self.create_session("part__20261011_120000_BBBBBB", self.filename, closed=False)
        self.marker.write_text(str(second), encoding="utf-8")
        self.objects["print_stats"]["state"] = "printing"
        self.objects["gcode_macro _TREED_CAM_STATE"]["enabled"] = 1
        saved = await self.post(sessionId=selected["sessionId"])
        self.assertEqual(saved["sessionId"], self.session.name)
        self.assertFalse((second / "rating.json").exists())
        await self.assert_error(self.get(), 409)

    async def test_new_closed_same_filename_replaces_latest(self):
        second = self.create_session("part__20261011_120000_CCCCCC", self.filename)
        self.assertEqual((await self.get())["sessionId"], second.name)
        self.assertFalse((self.session / "rating.json").exists())

    async def test_old_latest_is_rejected_when_new_print_token_is_active(self):
        latest = self.frames / ".latest_session"
        self.assertEqual(json.loads(latest.read_text())["sessionId"], self.session.name)
        self.objects["gcode_macro _TREED_CAM_STATE"]["session_token"] += 1
        await self.assert_error(self.get(), 409)
        self.assertEqual(json.loads(latest.read_text())["sessionId"], self.session.name)

    async def test_same_basename_different_directory_does_not_match(self):
        self.objects["print_stats"]["filename"] = "другая/part test.gcode"
        await self.assert_error(self.get(filename="другая/part test.gcode"), 409)

    async def test_get_rejects_noncomplete_camera_marker_and_tombstone(self):
        for state in ("printing", "paused", "cancelled", "error", "standby"):
            self.objects["print_stats"]["state"] = state
            await self.assert_error(self.get(), 409)
        self.objects["print_stats"]["state"] = "complete"
        self.objects["gcode_macro _TREED_CAM_STATE"]["enabled"] = 1
        await self.assert_error(self.get(), 409)
        self.objects["gcode_macro _TREED_CAM_STATE"]["enabled"] = 0
        self.marker.write_text(str(self.session))
        await self.assert_error(self.get(), 409)
        self.marker.unlink()
        self.component.unavailable_file.write_text("pending")
        await self.assert_error(self.get(), 409)

    async def test_get_rejects_missing_stale_open_or_invalid_metadata(self):
        latest = self.frames / ".latest_session"
        latest.unlink()
        await self.assert_error(self.get(), 409)
        metadata.write_json(latest, {"sessionId": self.session.name})
        path = self.session / "session.json"
        saved = json.loads(path.read_text(encoding="utf-8"))
        for changes in ({"closed": False}, {"filename": "other.gcode"}, {"finishedAt": None}):
            metadata.write_json(path, saved | changes)
            await self.assert_error(self.get(), 409)
        path.write_text("bad json")
        await self.assert_error(self.get(), 409)

    async def test_missing_frames_disables_rating(self):
        (self.session / "img_start.jpg").unlink()
        await self.assert_error(self.get(), 409)
        await self.assert_error(self.post(), 409)

    async def test_post_rejects_invalid_inputs_and_open_sessions(self):
        for session_id in ("../outside", "..", ".", "a/b", "a\\b", "/tmp/x", "C:x", None, [], "x" * 241):
            await self.assert_error(self.post(sessionId=session_id), 400)
        for filename in ("", "../part.gcode", "a/../part.gcode", "/part.gcode", "a\\b.gcode", "a\n.gcode", None, []):
            await self.assert_error(self.post(filename=filename), 400)
        for rating in (None, "okay", True, [], {}):
            await self.assert_error(self.post(rating=rating), 400)
        await self.assert_error(self.post(filename="other.gcode"), 409)
        second = self.create_session("active", self.filename, closed=False)
        await self.assert_error(self.post(sessionId=second.name), 409)

    async def test_atomic_failure_preserves_prior_rating(self):
        await self.post()
        self.session = self.frames / "successful" / self.session.name
        prior = (self.session / "rating.json").read_bytes()
        with patch.object(feedback.os, "replace", side_effect=OSError("disk failure")):
            await self.assert_error(self.post(rating="bad"), 409)
        self.assertFalse(self.session.exists())
        self.session = self.frames / self.session.name
        self.assertEqual((self.session / "rating.json").read_bytes(), prior)
        self.assertEqual(list(self.session.glob(".rating-*")), [])
        self.assertEqual((await self.get())["rating"], "good")

    async def test_corrupt_saved_rating_is_visible(self):
        (self.session / "rating.json").write_text('{"rating": "bad"}')
        await self.assert_error(self.get(), 409)
        await self.assert_error(self.post(), 409)

    async def test_symlinks_cannot_escape_or_replace_rating(self):
        outside = self.root / "outside"
        outside.mkdir()
        link = self.frames / "linked"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"Создание ссылок недоступно: {error}")
        await self.assert_error(self.post(sessionId="linked"), 409)
        rating = self.session / "rating.json"
        target = outside / "untouched.json"
        target.write_text("untouched")
        rating.symlink_to(target)
        await self.assert_error(self.post(), 409)
        self.assertEqual(target.read_text(), "untouched")
        latest = self.frames / ".latest_session"
        latest.unlink()
        latest.symlink_to(target)
        await self.assert_error(self.get(), 409)

    # Блок 3: Закрытие повторяемо и не переключает указатель на прежнюю сессию.
    async def test_stop_is_idempotent_and_does_not_change_latest(self):
        path = self.session / "session.json"
        original = path.read_bytes()
        second = self.create_session("next_session", "another.gcode", closed=False)
        self.assertFalse(metadata.stop_session(self.frames, self.session, 999))
        self.assertEqual(path.read_bytes(), original)
        self.assertTrue(metadata.stop_session(self.frames, self.session, 101))
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(json.loads((self.frames / ".latest_session").read_text())["sessionId"], second.name)

    async def test_metadata_rejects_outside_directory_and_invalid_filename(self):
        outside = self.root / "outside"
        outside.mkdir()
        with self.assertRaises(ValueError):
            metadata.start_session(self.frames, outside, self.filename)
        with self.assertRaises(ValueError):
            metadata.start_session(self.frames, self.session, "../file.gcode")
        self.assertFalse((outside / "session.json").exists())


# Блок 4: Адресный тест с временными каталогами, без сетевых операций.
if __name__ == "__main__":
    unittest.main()
