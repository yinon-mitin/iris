"""The hourly clean-up of leftover download folders. No network or Telegram needed."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import utils


class CleanTempfileTest(unittest.TestCase):
    def test_removes_leftover_download_folders_older_than_three_hours(self):
        with tempfile.TemporaryDirectory() as root:
            leftover, unrelated = Path(root, "ytdl-leftover"), Path(root, "unrelated")
            leftover.mkdir()
            unrelated.mkdir()
            # ctime cannot be moved back on disk, so the clock jumps forward instead
            later = time.time() + 4 * 3600
            with mock.patch.object(tempfile, "tempdir", root), mock.patch.object(utils.time, "time", return_value=later):
                utils.clean_tempfile()
            self.assertFalse(leftover.exists())
            self.assertTrue(unrelated.exists())


if __name__ == "__main__":
    unittest.main()
