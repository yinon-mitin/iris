"""File-type detection, link checks and download helpers. No network or Telegram needed."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from engine.direct import response_filename
from engine.generic import instagram_match_filter
from utils import FriendlyError, detect_format, guess_mime, is_instagram, is_internal_url

SAMPLES = {
    "photo.jpg": b"\xff\xd8\xff\xe0" + b"\x00" * 64,
    "photo.png": b"\x89PNG\r\n\x1a\n" + b"\x00" * 64,
    "song.mp3": b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 64,
    "doc.pdf": b"%PDF-1.4\n" + b"\x00" * 64,
    "archive.zip": b"PK\x03\x04" + b"\x00" * 64,
    "video.mp4": b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 64,
    # a brand filetype does not know: the extension decides
    "clip.mp4": b"\x00\x00\x00\x18ftypzzzz\x00\x00\x00\x00" + b"\x00" * 64,
}


class DetectFormatTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        cls.f = {}
        for name, data in SAMPLES.items():
            path = Path(cls.dir.name, name)
            path.write_bytes(data)
            cls.f[name] = path

    @classmethod
    def tearDownClass(cls):
        cls.dir.cleanup()

    def files(self, *names):
        return [self.f[n] for n in names]

    def test_images_become_photo(self):
        self.assertEqual(detect_format(self.files("photo.jpg", "photo.png"), "video"), "photo")

    def test_audio_becomes_audio(self):
        self.assertEqual(detect_format(self.files("song.mp3"), "video"), "audio")

    def test_other_files_become_document(self):
        self.assertEqual(detect_format(self.files("doc.pdf"), "video"), "document")
        self.assertEqual(detect_format(self.files("archive.zip", "photo.jpg"), "video"), "document")

    def test_video_keeps_user_choice(self):
        self.assertEqual(detect_format(self.files("video.mp4"), "video"), "video")
        self.assertEqual(detect_format(self.files("video.mp4", "photo.jpg"), "video"), "video")

    def test_document_setting_wins(self):
        self.assertEqual(detect_format(self.files("photo.jpg"), "document"), "document")

    def test_mime_falls_back_to_extension(self):
        self.assertEqual(guess_mime(self.f["clip.mp4"]), "video/mp4")


class InternalUrlTest(unittest.TestCase):
    def test_private_and_local_addresses_are_internal(self):
        for url in (
            "http://10.1.2.3/x",
            "http://192.168.1.10:8096/",
            "http://172.20.0.5/",
            "http://100.100.100.100/",  # CGNAT / Tailscale
            "http://127.0.0.1:8096/",
            "http://localhost/",
            "http://[::1]/",
            "http://169.254.169.254/latest/meta-data/",
        ):
            with self.subTest(url=url):
                self.assertTrue(is_internal_url(url))

    def test_public_and_unresolvable_are_not_internal(self):
        for url in ("https://93.184.215.14/", "http://does-not-exist.invalid/", "not a url"):
            with self.subTest(url=url):
                self.assertFalse(is_internal_url(url))


class InstagramTest(unittest.TestCase):
    def test_hosts(self):
        self.assertTrue(is_instagram("https://www.instagram.com/p/abc/"))
        self.assertTrue(is_instagram("https://instagram.com/reel/abc/"))
        self.assertFalse(is_instagram("https://notinstagram.com/p/abc/"))
        self.assertFalse(is_instagram("https://instagram.com.example.org/p/abc/"))

    def test_match_filter_skips_photos_only(self):
        self.assertEqual(instagram_match_filter({"formats": []}), "photo item")
        self.assertIsNone(instagram_match_filter({"formats": [{"url": "x"}]}))
        with self.assertRaises(FriendlyError):
            instagram_match_filter({"formats": [{"url": "x"}], "is_live": True})


class ResponseFilenameTest(unittest.TestCase):
    @staticmethod
    def response(url, disposition=""):
        return SimpleNamespace(url=url, headers={"content-disposition": disposition})

    def test_content_disposition_utf8(self):
        r = self.response("https://example.org/get", "attachment; filename*=UTF-8''%D0%BE%D1%82%D1%87%D1%91%D1%82.pdf")
        self.assertEqual(response_filename(r), "отчёт.pdf")

    def test_falls_back_to_url(self):
        self.assertEqual(response_filename(self.response("https://example.org/files/My%20Doc.zip")), "My Doc.zip")

    def test_no_path_traversal(self):
        name = response_filename(self.response("https://example.org/x", 'attachment; filename="../../etc/passwd"'))
        self.assertNotIn("/", name)

    def test_long_names_fit_filesystem(self):
        name = response_filename(self.response("https://example.org/" + "я" * 300 + ".bin"))
        self.assertLessEqual(len(name.encode()), 200)


if __name__ == "__main__":
    unittest.main()
