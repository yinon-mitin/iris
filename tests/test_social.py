"""X and Threads links: host checks and media extraction. No network or Telegram needed."""
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

from engine.social import (
    Media,
    download_media,
    fxtwitter_url,
    parse_fxtwitter_photos,
    parse_threads_embed,
    resolve_threads_share,
    threads_embed_url,
)
from utils import is_threads, is_x

THREADS_PHOTO = """
<div class="OuterContainer"><div class="AvatarContainer"><img class="img" src="https://scontent.cdninstagram.com/v/t51.2885-19/avatar.jpg?stp=dst-jpg_s150x150&amp;_nc_cat=1" /></div>
<span>text</span>
<div class="SoloMediaContainer" id="u_0_4_yc"><div class="SingleInnerMediaContainer"><img class="img" src="https://scontent.cdninstagram.com/v/t51.82787-15/photo.jpg?stp=dst-jpg&amp;_nc_gid=abc" draggable="false" alt="" /></div></div></div>
"""

THREADS_VIDEO = """
<div class="OuterContainer"><div class="AvatarContainer"><img class="img" src="https://scontent.cdninstagram.com/v/t51.2885-19/avatar.jpg" /></div>
<div class="SoloMediaContainer"><div class="SingleInnerMediaContainerVideo SingleInnerMediaContainer"><video controls="1" loop="1" class=""><source src="https://instagram.ftlv20-1.fna.fbcdn.net/o1/v/clip.mp4?_nc_cat=110&amp;oe=6AF2E1AB" /></video></div></div></div>
"""

THREADS_ALBUM = """
<div class="AvatarContainer"><img class="img" src="https://scontent.cdninstagram.com/avatar.jpg" /></div>
<div class="CarouselContainer">
<div class="MediaContainer"><img class="img" src="https://scontent.cdninstagram.com/one.jpg" /></div>
<div class="MediaContainer"><video><source src="https://scontent.cdninstagram.com/two.mp4" /></video></div>
<div class="MediaContainer"><img class="img" src="https://scontent.cdninstagram.com/three.jpg" /></div>
</div>
"""

THREADS_TEXT_ONLY = '<div class="AvatarContainer"><img class="img" src="https://scontent.cdninstagram.com/avatar.jpg" /></div><span>just words</span>'


class HostTest(unittest.TestCase):
    def test_x_hosts(self):
        for url in (
            "https://x.com/NASA/status/1",
            "https://www.x.com/NASA/status/1",
            "https://twitter.com/NASA/status/1",
            "https://mobile.twitter.com/NASA/status/1",
        ):
            with self.subTest(url=url):
                self.assertTrue(is_x(url))

    def test_x_lookalikes_are_not_x(self):
        for url in ("https://notx.com/a/status/1", "https://x.com.example.org/a/status/1", "https://fakex.com/", "", None):
            with self.subTest(url=url):
                self.assertFalse(is_x(url))

    def test_threads_hosts(self):
        for url in (
            "https://www.threads.com/@nasa/post/DYU50T4kXpX",
            "https://threads.com/@nasa/post/DYU50T4kXpX",
            "https://www.threads.net/@nasa/post/DYU50T4kXpX",
        ):
            with self.subTest(url=url):
                self.assertTrue(is_threads(url))

    def test_threads_lookalikes_are_not_threads(self):
        for url in ("https://notthreads.com/a", "https://threads.com.example.org/a", "", None):
            with self.subTest(url=url):
                self.assertFalse(is_threads(url))


class ThreadsEmbedUrlTest(unittest.TestCase):
    def test_post_link_becomes_embed_link(self):
        want = "https://www.threads.com/t/DYU50T4kXpX/embed"
        for url in (
            "https://www.threads.com/@nasa/post/DYU50T4kXpX",
            "https://www.threads.net/@nasa/post/DYU50T4kXpX/its-not-turquoise?hl=en",
            "https://www.threads.com/@nasa/post/DYU50T4kXpX/?xmt=AQ",
            "https://www.threads.com/t/DYU50T4kXpX",
            "https://www.threads.com/@thvllamelo/video/DeS0VRHEkZt",
        ):
            with self.subTest(url=url):
                self.assertTrue(threads_embed_url(url).endswith("/embed"))
        self.assertEqual(threads_embed_url("https://www.threads.net/@nasa/post/DYU50T4kXpX?hl=en"), want)

    def test_profile_link_is_not_a_post(self):
        self.assertIsNone(threads_embed_url("https://www.threads.com/@nasa"))


class ThreadsShareTest(unittest.TestCase):
    """The Share button gives threads.com/share/<id>, which redirects to the post."""

    SHARE = "https://www.threads.com/share/_wSEYlmQ-"
    POST = "https://www.threads.com/@makajonak/post/DfeSS4fiN3z?xmt=AQG&slof=1"

    @staticmethod
    def get_returning(status, location=None):
        def get(url, **kwargs):
            assert kwargs.get("allow_redirects") is False  # never follow a redirect off Threads
            return SimpleNamespace(status_code=status, headers={"location": location} if location else {})

        return get

    def test_share_link_resolves_to_the_post(self):
        self.assertEqual(resolve_threads_share(self.SHARE, self.get_returning(302, self.POST)), self.POST)

    def test_share_link_is_not_a_post_before_resolving(self):
        self.assertIsNone(threads_embed_url(self.SHARE))
        self.assertEqual(threads_embed_url(self.POST), "https://www.threads.com/t/DfeSS4fiN3z/embed")

    def test_redirect_to_another_site_is_not_followed(self):
        self.assertEqual(resolve_threads_share(self.SHARE, self.get_returning(302, "https://evil.example.org/x")), self.SHARE)

    def test_no_redirect_keeps_the_link(self):
        self.assertEqual(resolve_threads_share(self.SHARE, self.get_returning(200)), self.SHARE)

    def test_post_links_are_not_requested(self):
        def get(*args, **kwargs):
            raise AssertionError("no request needed")

        self.assertEqual(resolve_threads_share(self.POST, get), self.POST)


class ParseThreadsEmbedTest(unittest.TestCase):
    def test_photo_without_avatar_and_with_unescaped_url(self):
        self.assertEqual(
            parse_threads_embed(THREADS_PHOTO),
            [Media("photo", "https://scontent.cdninstagram.com/v/t51.82787-15/photo.jpg?stp=dst-jpg&_nc_gid=abc")],
        )

    def test_video(self):
        self.assertEqual(
            parse_threads_embed(THREADS_VIDEO),
            [Media("video", "https://instagram.ftlv20-1.fna.fbcdn.net/o1/v/clip.mp4?_nc_cat=110&oe=6AF2E1AB")],
        )

    def test_album_keeps_post_order(self):
        self.assertEqual(
            parse_threads_embed(THREADS_ALBUM),
            [
                Media("photo", "https://scontent.cdninstagram.com/one.jpg"),
                Media("video", "https://scontent.cdninstagram.com/two.mp4"),
                Media("photo", "https://scontent.cdninstagram.com/three.jpg"),
            ],
        )

    def test_text_only_post_has_no_media(self):
        self.assertEqual(parse_threads_embed(THREADS_TEXT_ONLY), [])

    def test_only_https_media(self):
        page = '<div class="SoloMediaContainer"><img src="http://scontent.cdninstagram.com/a.jpg" /><img src="data:image/png;base64,AAAA" /></div>'
        self.assertEqual(parse_threads_embed(page), [])


class FxTwitterTest(unittest.TestCase):
    def test_status_links_become_api_links(self):
        want = "https://api.fxtwitter.com/i/status/2042756933686337713"
        for url in (
            "https://x.com/NASA/status/2042756933686337713",
            "https://twitter.com/NASA/status/2042756933686337713?s=20",
            "https://mobile.twitter.com/NASA/status/2042756933686337713/photo/1",
            "https://x.com/i/status/2042756933686337713",
        ):
            with self.subTest(url=url):
                self.assertEqual(fxtwitter_url(url), want)

    def test_non_status_link_has_no_api_link(self):
        self.assertIsNone(fxtwitter_url("https://x.com/NASA"))

    def test_photos_in_original_size_and_videos_ignored(self):
        payload = {
            "code": 200,
            "tweet": {
                "media": {
                    "all": [
                        {"type": "photo", "url": "https://pbs.twimg.com/media/AAA.jpg"},
                        {"type": "video", "url": "https://video.twimg.com/x.mp4"},
                        {"type": "photo", "url": "https://pbs.twimg.com/media/BBB.jpg"},
                    ]
                }
            },
        }
        self.assertEqual(
            parse_fxtwitter_photos(payload),
            [
                Media("photo", "https://pbs.twimg.com/media/AAA.jpg?name=orig"),
                Media("photo", "https://pbs.twimg.com/media/BBB.jpg?name=orig"),
            ],
        )

    def test_tweet_without_media(self):
        self.assertEqual(parse_fxtwitter_photos({"code": 200, "tweet": {"text": "hi"}}), [])
        self.assertEqual(parse_fxtwitter_photos({"code": 404, "message": "NOT_FOUND"}), [])


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 64
FILES = {"/a": PNG, "/b": MP4, "/c": JPG}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = FILES.get(self.path.split("?")[0])
        self.send_response(200 if body else 404)
        self.send_header("Content-Length", str(len(body or b"")))
        self.end_headers()
        self.wfile.write(body or b"")

    def log_message(self, *args):
        pass


class DownloadMediaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_files_are_numbered_by_position_with_real_extensions(self):
        media = [Media("photo", self.base + "/a?x=1"), Media("video", self.base + "/b"), Media("photo", self.base + "/c")]
        with tempfile.TemporaryDirectory() as tmp:
            files = download_media(media, tmp)
            self.assertEqual([f.name for f in files], ["01.png", "02.mp4", "03.jpg"])
            self.assertEqual(files[1].read_bytes(), MP4)
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["01.png", "02.mp4", "03.jpg"])

    def test_failed_download_raises(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(Exception):
            download_media([Media("photo", self.base + "/missing")], tmp)


if __name__ == "__main__":
    unittest.main()
