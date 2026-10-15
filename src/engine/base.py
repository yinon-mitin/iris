#!/usr/bin/env python3
# coding: utf-8

# ytdlbot - types.py

import hashlib
import html
import json
import logging
import re
import tempfile
import time
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from types import SimpleNamespace
from typing import final

import ffmpeg
import filetype
from pyrogram import enums, errors, types

from config import TG_NORMAL_MAX_SIZE, BotText, Types
from database import Redis
from database.model import (
    check_quota,
    get_format_settings,
    get_free_quota,
    get_paid_quota,
    get_quality_settings,
    use_quota,
)
from engine.helper import debounce, sizeof_fmt
from utils import FriendlyError, duration_fmt, guess_mime, is_internal_url


def generate_input_media(file_paths: list, cap: str) -> list:
    input_media = []
    for path in file_paths:
        mime = filetype.guess_mime(path)
        if "video" in mime:
            input_media.append(types.InputMediaVideo(media=path))
        elif "image" in mime:
            input_media.append(types.InputMediaPhoto(media=path))
        elif "audio" in mime:
            input_media.append(types.InputMediaAudio(media=path))
        else:
            input_media.append(types.InputMediaDocument(media=path))

    input_media[0].caption = cap
    return input_media


class BaseDownloader(ABC):
    def __init__(self, client: Types.Client, bot_msg: Types.Message, url: str):
        self._client = client
        self._url = url
        # chat id is the same for private chat
        self._chat_id = self._from_user = bot_msg.chat.id
        if bot_msg.chat.type == enums.ChatType.GROUP or bot_msg.chat.type == enums.ChatType.SUPERGROUP:
            # if in group, we need to find out who send the message
            self._from_user = bot_msg.reply_to_message.from_user.id
        self._id = bot_msg.id
        self._tempdir = tempfile.TemporaryDirectory(prefix="ytdl-")
        self._album_sent, self._album_first = 0, None
        self._bot_msg: Types.Message = bot_msg
        self._redis = Redis()
        self._quality = get_quality_settings(self._chat_id)
        self._format = get_format_settings(self._chat_id)

    def __del__(self):
        self._tempdir.cleanup()

    def _record_usage(self):
        free, paid = get_free_quota(self._from_user), get_paid_quota(self._from_user)
        logging.info("User %s has %s free and %s paid quota", self._from_user, free, paid)
        if free + paid < 0:
            raise Exception("Usage limit exceeded")

        use_quota(self._from_user)

    @staticmethod
    def __remove_bash_color(text):
        return re.sub(r"\u001b|\[0;94m|\u001b\[0m|\[0;32m|\[0m|\[0;33m", "", text)

    def __progress_text(self, desc, total, finished, speed="", eta=""):
        lines = [desc]
        if title := getattr(self, "_title", None):
            lines.append(f"<b>{html.escape(title)}</b>")
        if total:
            percent = max(0, min(100, int(finished * 100 / total)))
            filled = percent // 10
            lines.append(f"<code>{'▓' * filled}{'░' * (10 - filled)}</code> {percent}%")
            lines.append(f"{sizeof_fmt(finished)} из {sizeof_fmt(total)}")
        if speed and speed != "N/A":
            lines.append(f"{BotText.speed}: {speed}")
        if eta and eta not in ("N/A", "None", "Unknown"):
            lines.append(f"{BotText.eta}: {eta}")
        return "\n".join(lines)

    def download_hook(self, d: dict):
        if d["status"] == "downloading":
            downloaded = d.get("downloaded_bytes", 0)
            total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)

            if total > TG_NORMAL_MAX_SIZE:
                raise FriendlyError(BotText.too_large.format(size=sizeof_fmt(total)))

            # percent = remove_bash_color(d.get("_percent_str", "N/A"))
            if not getattr(self, "_title", None):
                self._title = (d.get("info_dict") or {}).get("title")
            speed = self.__remove_bash_color(str(d.get("_speed_str", "N/A"))).strip()
            speed = speed.replace("KiB/s", " КБ/с").replace("MiB/s", " МБ/с").replace("GiB/s", " ГБ/с")
            eta = self.__remove_bash_color(str(d.get("_eta_str", d.get("eta")))).strip()
            text = self.__progress_text(BotText.downloading, total, downloaded, speed, eta)
            self.edit_text(text)

    def upload_hook(self, current, total):
        text = self.__progress_text(BotText.uploading, total, current)
        self.edit_text(text)

    @debounce(1.5)
    def edit_text(self, text: str):
        # progress is cosmetic: a Telegram error here must never break the download/upload itself
        if time.time() < getattr(self, "_edit_paused_until", 0):
            return
        try:
            self._bot_msg.edit_text(text, parse_mode=enums.ParseMode.HTML)
        except errors.Flood as e:
            logging.warning("Flood wait %ss on progress edit, pausing updates", e.value)
            self._edit_paused_until = time.time() + e.value
        except Exception as e:
            logging.debug("Progress edit skipped: %s", e)

    @abstractmethod
    def _setup_formats(self) -> list | None:
        pass

    @abstractmethod
    def _download(self, formats) -> list:
        # responsible for get format and download it
        pass

    @property
    def _methods(self):
        return {
            "document": self._client.send_document,
            "audio": self._client.send_audio,
            "video": self._client.send_video,
            "animation": self._client.send_animation,
            "photo": self._client.send_photo,
        }

    def send_something(self, *, chat_id, files, _type, caption=None, thumb=None, **kwargs):
        self._client.send_chat_action(chat_id, enums.ChatAction.UPLOAD_DOCUMENT)
        is_cache = kwargs.pop("cache", False)
        if len(files) > 1 and is_cache == False:
            # Telegram albums hold at most 10 items; on a retry skip the parts already sent
            for i in range(self._album_sent, len(files), 10):
                inputs = generate_input_media(files[i : i + 10], caption if i == 0 else None)
                sent = self._client.send_media_group(chat_id, inputs)[0]
                self._album_first = self._album_first or sent
                self._album_sent = i + 10
            return self._album_first
        else:
            file_arg_name = None
            if _type == "photo":
                file_arg_name = "photo"
            elif _type == "video":
                file_arg_name = "video"
            elif _type == "animation":
                file_arg_name = "animation"
            elif _type == "document":
                file_arg_name = "document"
            elif _type == "audio":
                file_arg_name = "audio"
            else:
                logging.error("Unknown _type encountered: %s", _type)
                return None

            send_args = {
                "chat_id": chat_id,
                file_arg_name: files[0],
                "caption": caption,
                "parse_mode": enums.ParseMode.HTML,
                "progress": self.upload_hook,
                **kwargs,
            }

            if _type in ["video", "animation", "document", "audio"] and thumb is not None:
                send_args["thumb"] = thumb

            return self._methods[_type](**send_args)

    def get_metadata(self):
        video_path = sorted(Path(self._tempdir.name).glob("*"))[0]
        filename = Path(video_path).name
        width = height = duration = 0
        try:
            video_streams = ffmpeg.probe(video_path, select_streams="v")
            for item in video_streams.get("streams", []):
                height = item["height"]
                width = item["width"]
            duration = int(float(video_streams["format"]["duration"]))
        except Exception as e:
            logging.error("Error while getting metadata: %s", e)
        try:
            thumb = Path(video_path).parent.joinpath(f"{uuid.uuid4().hex}-thunmnail.png").as_posix()
            # A thumbnail's width and height should not exceed 320 pixels.
            ffmpeg.input(video_path, ss=duration / 2).filter(
                "scale",
                "if(gt(iw,ih),300,-1)",  # If width > height, scale width to 320 and height auto
                "if(gt(iw,ih),-1,300)",
            ).output(thumb, vframes=1).run()
        except ffmpeg._run.Error:
            thumb = None

        title = getattr(self, "_title", None) or Path(video_path).stem
        mime = guess_mime(video_path)
        if mime.startswith("image/"):
            caption = BotText.caption_photo.format(title=html.escape(title), url=html.escape(self._url))
        elif mime.startswith("audio/"):
            caption = BotText.caption_audio.format(
                title=html.escape(title), duration=duration_fmt(duration), url=html.escape(self._url)
            )
        elif mime.startswith("video/"):
            caption = BotText.caption.format(
                title=html.escape(title), duration=duration_fmt(duration), width=width, height=height, url=html.escape(self._url)
            )
        else:
            caption = BotText.caption_file.format(title=html.escape(Path(video_path).name), url=html.escape(self._url))
        return dict(height=height, width=width, duration=duration, thumb=thumb, caption=caption)

    def _upload(self, files=None, meta=None):
        if files is None:
            files = sorted(Path(self._tempdir.name).glob("*"))
        if meta is None:
            meta = self.get_metadata()

        success = SimpleNamespace(document=None, video=None, audio=None, animation=None, photo=None)
        if self._format == "document":
            logging.info("Sending as document for %s", self._url)
            success = self.send_something(
                chat_id=self._chat_id,
                files=files,
                _type="document",
                thumb=meta.get("thumb"),
                force_document=True,
                caption=meta.get("caption"),
            )
        elif self._format == "photo":
            logging.info("Sending as photo for %s", self._url)
            success = self.send_something(
                chat_id=self._chat_id,
                files=files,
                _type="photo",
                caption=meta.get("caption"),
            )
        elif self._format == "audio":
            logging.info("Sending as audio for %s", self._url)
            success = self.send_something(
                chat_id=self._chat_id,
                files=files,
                _type="audio",
                caption=meta.get("caption"),
            )
        elif self._format == "video":
            logging.info("Sending as video for %s", self._url)
            attempt_methods = ["video", "animation", "audio", "photo"]
            video_meta = meta.copy()

            upload_successful = False  # Flag to track if any method succeeded
            for method in attempt_methods:
                current_meta = video_meta.copy()

                if method == "photo":
                    current_meta.pop("thumb", None)
                    current_meta.pop("duration", None)
                    current_meta.pop("height", None)
                    current_meta.pop("width", None)
                elif method == "audio":
                    current_meta.pop("height", None)
                    current_meta.pop("width", None)

                try:
                    success_obj = self.send_something(
                        chat_id=self._chat_id,
                        files=files,
                        _type=method,
                        **current_meta
                    )

                    if method == "video":
                        success = success_obj
                    elif method == "animation":
                        success = success_obj
                    elif method == "photo":
                        success = success_obj
                    elif method == "audio":
                        success = success_obj

                    upload_successful = True # Set flag to True on success
                    break
                except Exception as e:
                    logging.error("Retry to send as %s, error: %s", method, e)

            # Check the flag after the loop
            if not upload_successful:
                raise FriendlyError(BotText.upload_failed)

        else:
            logging.error("Unknown upload format settings for %s", self._format)
            return

        video_key = self._calc_video_key()
        obj = success.document or success.video or success.audio or success.animation or success.photo
        mapping = {
            "file_id": json.dumps([getattr(obj, "file_id", None)]),
            "meta": json.dumps({k: v for k, v in meta.items() if k != "thumb"}, ensure_ascii=False),
        }

        # an album is several messages: caching only the first file_id would resend a single item
        if len(files) == 1:
            self._redis.add_cache(video_key, mapping)
        # keep the chat clean: the file itself is the result, drop the progress message
        try:
            self._bot_msg.delete()
        except Exception as e:
            logging.warning("Can't delete progress message: %s", e)
        return success

    def _upload_with_retry(self, files=None, meta=None, attempts=3):
        # fix the list now: the first attempt leaves a generated thumbnail in the same directory
        if files is None:
            files = sorted(Path(self._tempdir.name).glob("*"))
        for attempt in range(1, attempts + 1):
            try:
                return self._upload(files, meta)
            except errors.Flood as e:
                if attempt == attempts:
                    raise
                logging.warning("Flood wait %ss before upload retry for %s", e.value, self._url)
                time.sleep(e.value)
            except Exception as e:
                # too large etc. won't get better on a retry
                if attempt == attempts or (isinstance(e, FriendlyError) and str(e) != BotText.upload_failed):
                    raise
                logging.warning("Upload attempt %s/%s failed for %s: %s", attempt, attempts, self._url, e)
                try:  # direct call: the debounced edit_text may swallow it right after a progress update
                    self._bot_msg.edit_text(BotText.upload_retry.format(attempt + 1, attempts))
                except Exception:
                    pass
                time.sleep(10 * attempt)

    def _get_video_cache(self):
        return self._redis.get_cache(self._calc_video_key())

    def _calc_video_key(self):
        h = hashlib.md5()
        h.update((self._url + self._quality + self._format).encode())
        key = h.hexdigest()
        return key

    @final
    def start(self):
        check_quota(self._from_user)
        try:
            if is_internal_url(self._url):
                raise FriendlyError(BotText.error_internal)
            if cache := self._get_video_cache():
                logging.info("Cache hit for %s", self._url)
                meta, file_id = json.loads(cache["meta"]), json.loads(cache["file_id"])
                meta["cache"] = True
                self._upload_with_retry(file_id, meta)
            else:
                self._start()
        finally:
            # files are not kept on the server once the job is over, sent or not
            self._tempdir.cleanup()
        self._record_usage()

    @abstractmethod
    def _start(self):
        pass
