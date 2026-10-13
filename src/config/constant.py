#!/usr/local/bin/python3
# coding: utf-8

# ytdlbot - constant.py
# 8/16/21 16:59
#

__author__ = "Benny <benny.think@gmail.com>"

import typing

from pyrogram import Client, types


class BotText:

    start = "Пришлите ссылку на видео (VK, OK, YouTube и др.) — я скачаю его и отправлю сюда.\nКачество: /settings\n\n"

    help = """
1. Просто пришлите ссылку на видео — VK, OK.ru, YouTube и любые сайты, которые поддерживает yt-dlp.

2. Качество и формат отправки — /settings. Для длинных видео выбирайте Medium или Low: Telegram принимает файлы до 2 ГБ.

3. Для Instagram, Pixeldrain и KrakenFiles: `/spdl ссылка`.
    """

    about = "Приватный бот на базе ytdlbot и yt-dlp: https://github.com/tgbot-collection/ytdlbot"

    settings = """
Выберите качество и способ отправки видео.
High: до 1080p
Medium: до 720p
Low: до 480p

Если отправлять видео документом, Telegram не сможет проигрывать его без скачивания.

Сейчас:
Качество: {}
Отправка: {}
"""


class Types:
    Message = typing.Union[types.Message, typing.Coroutine]
    Client = Client
