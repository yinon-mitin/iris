"""Upload retries, album splitting, progress cleanup and file deletion, against a fake Telegram client."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from pyrogram import errors

import engine.base as base

SENT = SimpleNamespace(photo=SimpleNamespace(file_id="x"), document=None, video=None, audio=None, animation=None)


class FakeMessage:
    def __init__(self):
        self.chat, self.id = SimpleNamespace(id=1), id(self)
        self.deleted, self.texts = False, []

    def delete(self):
        self.deleted = True

    def edit_text(self, text, **kwargs):
        self.texts.append(text)


class FakeClient:
    """Records sends; fails the calls whose 1-based numbers are in `fail_calls`."""

    def __init__(self, fail_calls=()):
        self.calls, self.fail_calls = [], set(fail_calls)

    def send_chat_action(self, *args, **kwargs):
        pass

    def _record(self, entry):
        self.calls.append(entry)
        if len(self.calls) in self.fail_calls:
            raise ConnectionError("network down")

    def send_media_group(self, chat_id, inputs):
        self._record(("album", [Path(i.media).name for i in inputs]))
        return [SENT]

    def send_photo(self, **kwargs):
        self._record(("photo", Path(kwargs["photo"]).name))
        return SENT

    def __getattr__(self, name):
        if name.startswith("send_"):
            def unexpected(**kwargs):
                raise AssertionError(f"{name} should not be used here")
            return unexpected
        raise AttributeError(name)


class LocalJob(base.BaseDownloader):
    """A job whose 'download' renders n small JPEGs, so the real upload path runs offline."""

    def __init__(self, client, n_files):
        self._client, self._n = client, n_files
        self._url = "https://example.org/post"
        self._chat_id = self._from_user = 1
        self._tempdir = tempfile.TemporaryDirectory(prefix="ytdl-")
        self._album_sent, self._album_first = 0, None
        self._bot_msg = FakeMessage()
        self._redis = SimpleNamespace(add_cache=lambda *a: None, get_cache=lambda k: None)
        self._quality, self._format = "high", "photo"
        self._record_usage = lambda: None

    def _setup_formats(self):
        return None

    def _download(self, formats=None):
        for i in range(self._n):
            subprocess.run(
                ["ffmpeg", "-loglevel", "quiet", "-f", "lavfi", "-i", "color=c=red:s=32x32", "-frames:v", "1",
                 f"{self._tempdir.name}/{i:02d}.jpg"],
                check=True,
            )

    def _start(self):
        self._download()
        self._upload_with_retry()


@mock.patch.object(base, "check_quota", lambda uid: None)
@mock.patch.object(base.time, "sleep", lambda s: None)
class UploadTest(unittest.TestCase):
    def run_job(self, client, n_files):
        job = LocalJob(client, n_files)
        directory = Path(job._tempdir.name)
        error = None
        try:
            job.start()
        except Exception as e:  # noqa: BLE001 - the test inspects it
            error = e
        return job, directory, error

    def test_album_over_ten_is_split_and_cleaned(self):
        client = FakeClient()
        job, directory, error = self.run_job(client, 12)
        self.assertIsNone(error)
        self.assertEqual([len(c[1]) for c in client.calls], [10, 2])
        self.assertTrue(job._bot_msg.deleted, "progress message should be removed")
        self.assertFalse(directory.exists(), "downloads should be deleted")

    def test_retry_does_not_resend_delivered_parts(self):
        client = FakeClient(fail_calls={2})
        job, directory, error = self.run_job(client, 12)
        self.assertIsNone(error)
        sizes = [len(c[1]) for c in client.calls]
        self.assertEqual(sizes, [10, 2, 2], "only the failed second part is sent again")
        self.assertTrue(all(name.endswith(".jpg") for c in client.calls for name in c[1]), "no thumbnails leak in")
        self.assertFalse(directory.exists())

    def test_single_file_retried_then_sent(self):
        client = FakeClient(fail_calls={1})
        job, directory, error = self.run_job(client, 1)
        self.assertIsNone(error)
        self.assertEqual(client.calls, [("photo", "00.jpg"), ("photo", "00.jpg")])
        self.assertTrue(any("2/3" in t for t in job._bot_msg.texts))

    def test_gives_up_after_three_attempts_and_still_cleans(self):
        client = FakeClient(fail_calls={1, 2, 3})
        job, directory, error = self.run_job(client, 1)
        self.assertIsInstance(error, ConnectionError)
        self.assertEqual(len(client.calls), 3)
        self.assertFalse(directory.exists(), "files are deleted even when sending failed")

    def test_flood_wait_on_progress_pauses_updates(self):
        class FloodMessage(FakeMessage):
            def edit_text(self, text, **kwargs):
                self.texts.append(text)
                raise errors.FloodWait(value=30)

        job = LocalJob(FakeClient(), 0)
        job._bot_msg = FloodMessage()
        job.edit_text("first")  # must not raise
        job.edit_text.__wrapped__(job, "second")  # bypass the debounce: skipped while paused
        self.assertEqual(job._bot_msg.texts, ["first"])
        job._tempdir.cleanup()


if __name__ == "__main__":
    unittest.main()
