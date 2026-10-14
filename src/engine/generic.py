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


class YoutubeDownload(BaseDownloader):
    @staticmethod
    def get_format(m):
        return [
            f"bestvideo[ext=mp4][height={m}]+bestaudio[ext=m4a]",
            f"bestvideo[vcodec^=avc][height={m}]+bestaudio[acodec^=mp4a]/best[vcodec^=avc]/best",
        ]

    def _setup_formats(self) -> list | None:
        quality, format_ = get_quality_settings(self._chat_id), get_format_settings(self._chat_id)
        if not is_youtube(self._url):
            # VK, OK.ru and other sites: respect quality setting so long videos fit into Telegram's 2GB
            if format_ == "audio" or quality == "audio":
                return ["bestaudio/best"]
            height = {"medium": 720, "low": 480}.get(quality, 1080)
            return [f"bestvideo*[height<={height}]+bestaudio/best[height<={height}]/best"]

        # quality: high, medium, low, custom
        # format: audio, video, document
        formats = []
        defaults = [
            # webm , vp9 and av01 are not streamable on telegram, so we'll extract only mp4
            "bestvideo[ext=mp4][vcodec!*=av01][vcodec!*=vp09]+bestaudio[ext=m4a]/bestvideo+bestaudio",
            "bestvideo[vcodec^=avc]+bestaudio[acodec^=mp4a]/best[vcodec^=avc]/best",
            None,
        ]
        audio = AUDIO_FORMAT or "m4a"
        maps = {
            "high-audio": [f"bestaudio[ext={audio}]"],
            "high-video": defaults,
            "high-document": defaults,
            "medium-audio": [f"bestaudio[ext={audio}]"],  # no mediumaudio :-(
            "medium-video": self.get_format(720),
            "medium-document": self.get_format(720),
            "low-audio": [f"bestaudio[ext={audio}]"],
            "low-video": self.get_format(480),
            "low-document": self.get_format(480),
            "custom-audio": "",
            "custom-video": "",
            "custom-document": "",
        }

        if quality == "custom":
            pass
            # TODO not supported yet
            # get format from ytdlp, send inlinekeyboard button to user so they can choose
            # another callback will be triggered to download the video
            # available_options = {
            #     "480P": "best[height<=480]",
            #     "720P": "best[height<=720]",
            #     "1080P": "best[height<=1080]",
            # }
            # markup, temp_row = [], []
            # for quality, data in available_options.items():
            #     temp_row.append(types.InlineKeyboardButton(quality, callback_data=data))
            #     if len(temp_row) == 3:  # Add a row every 3 buttons
            #         markup.append(temp_row)
            #         temp_row = []
            # # Add any remaining buttons as the last row
            # if temp_row:
            #     markup.append(temp_row)
            # self._bot_msg.edit_text("Choose the format", reply_markup=types.InlineKeyboardMarkup(markup))
            # return None

        formats.extend(maps[f"{quality}-{format_}"])
        # extend default formats if not high*
        if quality != "high":
            formats.extend(defaults)
        return formats

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
            # Always use the `source` format for Google Drive URLs.
            formats = ["source"] + formats

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
