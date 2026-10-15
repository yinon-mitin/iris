"""Which format the bot picks: H.264 first, short-side quality cap, 2 GB limit, never re-encode.
Runs yt-dlp's real selector on synthetic format lists, so no network is needed."""
import unittest

import yt_dlp

from engine.generic import QUALITY_CAP, VIDEO_FORMAT

GB = 1024**3


def fmt(format_id, vcodec="none", acodec="none", width=None, height=None, filesize=None, ext="mp4"):
    return {
        "format_id": format_id, "url": f"https://example.org/{format_id}", "ext": ext,
        "vcodec": vcodec, "acodec": acodec, "width": width, "height": height, "filesize": filesize,
    }


def unknown(format_id):
    # a progressive mp4 the site reports nothing about (Instagram does this for its H.264 files)
    return {"format_id": format_id, "url": f"https://example.org/{format_id}", "ext": "mp4"}


AUDIO = fmt("a", acodec="mp4a.40.5")


def pick(formats, quality="high"):
    opts = {"quiet": True, "no_warnings": True, "simulate": True,
            "format": VIDEO_FORMAT, "format_sort": [f"res:{QUALITY_CAP[quality]}", "ext:mp4:m4a"]}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.process_ie_result({
            "id": "x", "title": "x", "extractor": "test", "extractor_key": "Test",
            "webpage_url": "https://example.org/x", "formats": formats,
        }, download=False)
    return [f["format_id"] for f in info.get("requested_formats") or [info]]


class FormatSelectionTest(unittest.TestCase):
    def test_instagram_prefers_progressive_h264_over_vp9_dash(self):
        formats = [AUDIO, unknown("1"),
                   fmt("vp9-536", "vp09.00.30.08", width=536, height=952),
                   fmt("vp9-1076", "vp09.00.40.08", width=1076, height=1912)]
        self.assertEqual(pick(formats), ["1"])

    def test_portrait_cap_uses_short_side(self):
        formats = [AUDIO,
                   fmt("720p", "avc1.64001f", width=720, height=1280),
                   fmt("1080p", "avc1.640028", width=1080, height=1920)]
        self.assertEqual(pick(formats, "high"), ["1080p", "a"])
        self.assertEqual(pick(formats, "medium"), ["720p", "a"])

    def test_h264_beats_hevc_named_like_tiktok(self):
        formats = [fmt("hevc", "h265", "aac", 576, 1024), fmt("h264", "h264", "aac", 576, 1024)]
        self.assertEqual(pick(formats), ["h264"])

    def test_h264_beats_higher_resolution_vp9(self):
        formats = [AUDIO, fmt("avc-720", "avc1.4d401f", width=1280, height=720),
                   fmt("vp9-1080", "vp09.00.40.08", width=1920, height=1080)]
        self.assertEqual(pick(formats), ["avc-720", "a"])

    def test_drops_resolution_to_fit_2gb(self):
        formats = [AUDIO,
                   fmt("1080p", "avc1.640028", width=1920, height=1080, filesize=3 * GB),
                   fmt("720p", "avc1.4d401f", width=1280, height=720, filesize=1 * GB)]
        self.assertEqual(pick(formats), ["720p", "a"])

    def test_falls_back_to_other_codecs_when_no_h264(self):
        formats = [AUDIO, fmt("vp9-480", "vp9", width=854, height=480), fmt("vp9-1080", "vp9", width=1920, height=1080)]
        self.assertEqual(pick(formats), ["vp9-1080", "a"])


if __name__ == "__main__":
    unittest.main()
