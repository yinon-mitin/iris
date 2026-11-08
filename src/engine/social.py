#!/usr/bin/env python3
# coding: utf-8

# iris - social.py: photos and videos from X (Twitter) and Threads posts.
# yt-dlp saves X videos but not photos, and has no Threads extractor, so these two are read here.

import re
from html.parser import HTMLParser
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlparse

import filetype
import requests

FXTWITTER_API = "https://api.fxtwitter.com/i/status/{}"
THREADS_EMBED = "https://www.threads.com/t/{}/embed"
# a full browser User-Agent gets the JavaScript app shell from Threads, without the media; a short one gets the embed
USER_AGENT = "Mozilla/5.0"

_X_STATUS = re.compile(r"/status(?:es)?/(\d+)")
_THREADS_POST = re.compile(r"/(?:post|video|t)/([\w-]+)")


class Media(NamedTuple):
    kind: str  # "photo" or "video"
    url: str


def fxtwitter_url(url: str) -> str | None:
    """API link of a tweet, by its status id (the user name in the link does not matter)."""
    match = _X_STATUS.search(urlparse(url).path)
    return FXTWITTER_API.format(match.group(1)) if match else None


def parse_fxtwitter_photos(payload: dict) -> list[Media]:
    """Tweet photos in original size. Videos are left to yt-dlp, which picks the best H.264."""
    media = ((payload or {}).get("tweet") or {}).get("media") or {}
    photos = []
    for item in media.get("all") or []:
        url = item.get("url") or ""
        if item.get("type") != "photo" or not url.startswith("https://"):
            continue
        photos.append(Media("photo", url if "?" in url else url + "?name=orig"))
    return photos


def threads_embed_url(url: str) -> str | None:
    """Link of the public embed page of a Threads post: the only page that has the media without a login."""
    match = _THREADS_POST.search(urlparse(url).path)
    return THREADS_EMBED.format(match.group(1)) if match else None


class _EmbedMedia(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.media: list[Media] = []
        self._depth = 0
        self._avatar_depth = None  # nesting level of the avatar block while inside it

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag not in {"img", "source", "br", "input", "meta", "link"}:
            self._depth += 1
            if self._avatar_depth is None and "AvatarContainer" in (attrs.get("class") or ""):
                self._avatar_depth = self._depth
        if self._avatar_depth is not None:
            return
        src = attrs.get("src") or ""
        if tag in {"img", "source"} and src.startswith("https://"):
            self.media.append(Media("video" if tag == "source" else "photo", src))

    def handle_endtag(self, tag):
        if tag in {"img", "source", "br", "input", "meta", "link"}:
            return
        if self._avatar_depth == self._depth:
            self._avatar_depth = None
        self._depth -= 1


def parse_threads_embed(page: str) -> list[Media]:
    parser = _EmbedMedia()
    parser.feed(page)
    return parser.media


def fetch_threads_media(url: str) -> list[Media]:
    embed = threads_embed_url(url)
    if not embed:
        return []
    resp = requests.get(embed, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    return parse_threads_embed(resp.text)


def fetch_x_photos(url: str) -> list[Media]:
    api = fxtwitter_url(url)
    if not api:
        return []
    # a helper service: if it is down or does not know the tweet, the caller reports yt-dlp's own error
    try:
        resp = requests.get(api, headers={"User-Agent": USER_AGENT}, timeout=30)
        resp.raise_for_status()
        return parse_fxtwitter_photos(resp.json())
    except (requests.RequestException, ValueError):
        return []


def download_media(media: list[Media], directory: str | Path, hook=None) -> list[Path]:
    """Save the files into the directory as 01.ext, 02.ext, ... so an album keeps the order of the post.
    The extension comes from the content; hook(downloaded, total) reports progress."""
    saved = []
    for number, item in enumerate(media, 1):
        path = Path(directory, f"{number:02d}")
        with requests.get(item.url, headers={"User-Agent": USER_AGENT}, stream=True, timeout=60) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length") or 0)
            done = 0
            with open(path, "wb") as out:
                for chunk in resp.iter_content(64 * 1024):
                    out.write(chunk)
                    done += len(chunk)
                    if hook:
                        hook(done, total)
        ext = filetype.guess_extension(path) or ("mp4" if item.kind == "video" else "jpg")
        saved.append(path.rename(path.with_suffix(f".{ext}")))
    return saved
