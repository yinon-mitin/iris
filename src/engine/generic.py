#!/usr/bin/env python3
# coding: utf-8

# ytdlbot - generic.py

import logging
import os
from pathlib import Path

import filetype
import requests
import yt_dlp

from config import AUDIO_FORMAT, BotText
from utils import FriendlyError, detect_format, is_instagram, is_youtube
from database.model import get_format_settings, get_quality_settings
from engine.base import BaseDownloader


def match_filter(info_dict):
    if info_dict.get("is_live"):
        raise FriendlyError(BotText.error_live)
    return None  # Allow download for non-live videos


def instagram_match_filter(info_dict):
    # carousel photos have no video formats: skip them here, they are fetched in _download_photos
    if not info_dict.get("formats"):
        return "photo item"
    return match_filter(info_dict)


# Telegram streams H.264 with a smooth preview; VP9/AV1 often lag or show no preview at all.
# Files are never re-encoded: at most a separate video and audio track are remuxed into one mp4.
# sites name the same codecs differently: YouTube/Instagram "avc1"/"mp4a", TikTok "h264"/"aac"
H264 = "[vcodec~='^(avc|h264)']"
AAC = "[acodec~='^(mp4a|aac)']"
# headroom under Telegram's 2 GB for the audio track and the container
LIMIT = "1900M"


def _video_format(limit: str = LIMIT) -> str:
    known = (f"[filesize<{limit}]", f"[filesize_approx<{limit}]")  # size reported and it fits
    fits = f"[filesize<?{limit}][filesize_approx<?{limit}]"  # fits, or size not reported
    return "/".join([
        # 1. H.264 whose size is known to fit: a long film drops to a lower resolution instead of failing
        *(f"bv*{H264}{k}+ba{AAC}" for k in known),
        # 2. H.264 of unknown size
        f"bv*{H264}{fits}+ba{AAC}",
        f"bv*{H264}{fits}+ba",
        # 3. progressive mp4 whose codec the site does not report (Instagram, VK): in practice H.264 with audio
        f"b[vcodec~=?'^(avc|h264)'][ext=mp4]{fits}",
        # 4. the site has no H.264 at all: best of whatever it has
        f"bv*{fits}+ba",
        f"b{fits}",
        # 5. nothing fits into 2 GB: the size check in download_hook stops it with a clear message
        "bv*+ba/b",
    ])


VIDEO_FORMAT = _video_format()
QUALITY_CAP = {"high": 1080, "medium": 720, "low": 480}


class YoutubeDownload(BaseDownloader):
    def _setup_formats(self) -> list | None:
        quality, format_ = get_quality_settings(self._chat_id), get_format_settings(self._chat_id)
        if format_ == "audio" or quality == "audio":
            self._format_sort = None
            return [f"bestaudio[ext={AUDIO_FORMAT or 'm4a'}]/bestaudio/best"]
        # "res" is the short side, so a 720x1280 portrait clip counts as 720p, not 1280p.
        # It is a preference, not a filter: within the cap the largest wins; above it, the smallest.
        self._format_sort = [f"res:{QUALITY_CAP.get(quality, 1080)}", "ext:mp4:m4a"]
        return [VIDEO_FORMAT]

    def _download(self, formats) -> list:
        # limit by bytes, not chars: Cyrillic titles are 2 bytes/char and file names max out at 255 bytes
        output = Path(self._tempdir.name, "%(title).200B.%(ext)s").as_posix()
        ydl_opts = {
            "progress_hooks": [lambda d: self.download_hook(d)],
            "outtmpl": output,
            "restrictfilenames": False,
            "quiet": True,
            "match_filter": match_filter,
            "noplaylist": True,
            "concurrent_fragments": 16,
            "buffersize": 4194304,
            "retries": 6,
            "fragment_retries": 6,
            "skip_unavailable_fragments": True,
            "embed_metadata": True,
            "embed_thumbnail": True,
            "writethumbnail": False,
        }
        if format_sort := getattr(self, "_format_sort", None):
            ydl_opts["format_sort"] = format_sort
            ydl_opts["merge_output_format"] = "mp4"
        instagram = is_instagram(self._url)
        if instagram:
            # carousel items share one title: name files by position so they don't overwrite each other
            ydl_opts["outtmpl"] = Path(self._tempdir.name, "%(playlist_index)02d_%(id)s.%(ext)s").as_posix()
            # photo items have no video formats; they are fetched separately below
            ydl_opts["ignore_no_formats_error"] = True
            ydl_opts["match_filter"] = instagram_match_filter
        # setup cookies for youtube only
        if is_youtube(self._url):
            # use cookies from browser firstly
            if browsers := os.getenv("BROWSERS"):
                ydl_opts["cookiesfrombrowser"] = browsers.split(",")
            if os.path.isfile("youtube-cookies.txt") and os.path.getsize("youtube-cookies.txt") > 100:
                ydl_opts["cookiefile"] = "youtube-cookies.txt"
            # try add extract_args if present
            if potoken := os.getenv("POTOKEN"):
                ydl_opts["extractor_args"] = {"youtube": ["player-client=web,default", f"po_token=web+{potoken}"]}
                # for new version? https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide
                # ydl_opts["extractor_args"] = {
                #     "youtube": [f"po_token=web.player+{potoken}", f"po_token=web.gvs+{potoken}"]
                # }

        if self._url.startswith("https://drive.google.com"):
            # Always use the `source` format for Google Drive URLs (only the first format string is used).
            formats = [f"source/{formats[0]}"]

        files = None
        for f in formats:
            ydl_opts["format"] = f
            logging.info("yt-dlp options: %s", ydl_opts)
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(self._url, download=True)
            except yt_dlp.utils.DownloadError as e:
                # file hosts behind Cloudflare: retry once pretending to be a real browser
                if "generic:impersonate" not in str(e):
                    raise
                ydl_opts.setdefault("extractor_args", {})["generic"] = {"impersonate": [""]}
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(self._url, download=True)
            self._title = (info or {}).get("title") or getattr(self, "_title", None)
            if instagram:
                self._download_photos(info)
            files = sorted(Path(self._tempdir.name).glob("*"))
            break

        if not files:
            raise FriendlyError(BotText.error_unavailable)
        # yt-dlp saves direct links to non-media files (zip, pdf, ...) as .unknown_video
        for i, f in enumerate(files):
            if f.suffix == ".unknown_video" and (ext := filetype.guess_extension(f)):
                files[i] = f.rename(f.with_suffix(f".{ext}"))
        self._format = detect_format(files, self._format)
        return files

    def _download_photos(self, info):
        # yt-dlp extracts only videos; Instagram photos are exposed as the entry thumbnail
        for entry in (info or {}).get("entries") or [info or {}]:
            if not entry or entry.get("formats") or not entry.get("thumbnail"):
                continue
            resp = requests.get(entry["thumbnail"], headers=entry.get("http_headers"), timeout=60)
            resp.raise_for_status()
            ext = filetype.guess_extension(resp.content) or "jpg"
            name = f"{entry.get('playlist_index') or 0:02d}_{entry['id']}.{ext}"
            Path(self._tempdir.name, name).write_bytes(resp.content)

    def _start(self, formats=None):
        # start download and upload, no cache hit
        # user can choose format by clicking on the button(custom config)
        default_formats = self._setup_formats()
        if formats is not None:
            # formats according to user choice
            default_formats = formats + self._setup_formats()
        self._download(default_formats)
        self._upload_with_retry()
