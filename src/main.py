#!/usr/local/bin/python3
# coding: utf-8

# ytdlbot - new.py
# 8/14/21 14:37
#

__author__ = "Benny <benny.think@gmail.com>"

import html
import logging
import os
import re
import threading
import time
import typing
from io import BytesIO
from typing import Any

import psutil
import pyrogram.errors
import yt_dlp
from apscheduler.schedulers.background import BackgroundScheduler
from pyrogram import Client, enums, filters, idle, types

from config import (
    APP_HASH,
    APP_ID,
    AUTHORIZED_USER,
    BOT_TOKEN,
    ENABLE_ARIA2,
    ENABLE_FFMPEG,
    M3U8_SUPPORT,
    ENABLE_VIP,
    OWNER,
    PROVIDER_TOKEN,
    TOKEN_PRICE,
    BotText,
)
from database.model import (
    credit_account,
    get_format_settings,
    get_free_quota,
    get_paid_quota,
    get_quality_settings,
    init_user,
    reset_free,
    set_user_settings,
)
from engine import direct_entrance, youtube_entrance, special_download_entrance
from utils import FriendlyError, extract_url_and_name, is_youtube, sizeof_fmt, timeof_fmt

logging.info("Authorized users are %s", AUTHORIZED_USER)
logging.getLogger("apscheduler.executors.default").propagate = False


def create_app(name: str, workers: int = 64) -> Client:
    return Client(
        name,
        APP_ID,
        APP_HASH,
        bot_token=BOT_TOKEN,
        workers=workers,
        parse_mode=enums.ParseMode.HTML,
        # max_concurrent_transmissions=max(1, WORKERS // 2),
        # https://github.com/pyrogram/pyrogram/issues/1225#issuecomment-1446595489
    )


app = create_app("main")

# private mode: only OWNER and AUTHORIZED_USER can talk to the bot, for every handler
ALLOWED_IDS = set(OWNER) | {int(i) for i in str(AUTHORIZED_USER or "").split(",") if i.strip()}


@app.on_message(group=-1)
def auth_gate(client: Client, message: types.Message):
    uid = getattr(message.from_user, "id", None)
    if uid in ALLOWED_IDS:
        return
    logging.warning("Rejected message from %s: %s", uid, getattr(message, "text", ""))
    if message.chat.type == enums.ChatType.PRIVATE and (message.text or "").startswith("/start"):
        message.reply_text(BotText.private.format(uid), quote=True)
    message.stop_propagation()


@app.on_callback_query(group=-1)
def auth_gate_callback(client: Client, callback_query: types.CallbackQuery):
    if callback_query.from_user.id not in ALLOWED_IDS:
        callback_query.stop_propagation()


def private_use(func):
    def wrapper(client: Client, message: types.Message):
        chat_id = getattr(message.from_user, "id", None)

        # message type check
        if message.chat.type != enums.ChatType.PRIVATE and not getattr(message, "text", "").lower().startswith("/ytdl"):
            logging.debug("%s, it's annoying me...🙄️ ", message.text)
            return

        # authorized users check: OWNER + AUTHORIZED_USER, same list as auth_gate
        if chat_id not in ALLOWED_IDS:
            return

        return func(client, message)

    return wrapper


@app.on_message(filters.command(["start"]))
def start_handler(client: Client, message: types.Message):
    from_id = message.chat.id
    init_user(from_id)
    logging.info("%s welcome to youtube-dl bot!", message.from_user.id)
    client.send_chat_action(from_id, enums.ChatAction.TYPING)
    text = BotText.start
    if ENABLE_VIP:
        free, paid = get_free_quota(from_id), get_paid_quota(from_id)
        text += f"You have {free} free and {paid} paid quota."
    client.send_message(from_id, text, disable_web_page_preview=True)


@app.on_message(filters.command(["help"]))
def help_handler(client: Client, message: types.Message):
    chat_id = message.chat.id
    init_user(chat_id)
    client.send_chat_action(chat_id, enums.ChatAction.TYPING)
    client.send_message(chat_id, BotText.help, disable_web_page_preview=True)


@app.on_message(filters.command(["about"]))
def about_handler(client: Client, message: types.Message):
    chat_id = message.chat.id
    init_user(chat_id)
    client.send_chat_action(chat_id, enums.ChatAction.TYPING)
    client.send_message(chat_id, BotText.about)


@app.on_message(filters.command(["ping"]))
def ping_handler(client: Client, message: types.Message):
    chat_id = message.chat.id
    init_user(chat_id)
    client.send_chat_action(chat_id, enums.ChatAction.TYPING)

    def send_message_and_measure_ping():
        start_time = int(round(time.time() * 1000))
        reply: types.Message | typing.Any = client.send_message(chat_id, BotText.ping_start)

        end_time = int(round(time.time() * 1000))
        ping_time = int(round(end_time - start_time))
        message_sent = True
        if message_sent:
            message.reply_text(BotText.ping_result.format(ping_time), quote=True)
        time.sleep(0.5)
        client.edit_message_text(chat_id=reply.chat.id, message_id=reply.id, text=BotText.ping_done)
        time.sleep(1)
        client.delete_messages(chat_id=reply.chat.id, message_ids=reply.id)

    thread = threading.Thread(target=send_message_and_measure_ping)
    thread.start()


@app.on_message(filters.command(["buy"]))
def buy(client: Client, message: types.Message):
    if not ENABLE_VIP:
        message.reply_text(BotText.buy_disabled, quote=True)
        return
    markup = types.InlineKeyboardMarkup(
        [
            [  # First row
                types.InlineKeyboardButton("10-$1", callback_data="buy-10-1"),
                types.InlineKeyboardButton("20-$2", callback_data="buy-20-2"),
                types.InlineKeyboardButton("40-$3.5", callback_data="buy-40-3.5"),
            ],
            [  # second row
                types.InlineKeyboardButton("50-$4", callback_data="buy-50-4"),
                types.InlineKeyboardButton("75-$6", callback_data="buy-75-6"),
                types.InlineKeyboardButton("100-$8", callback_data="buy-100-8"),
            ],
        ]
    )
    message.reply_text("Please choose the amount you want to buy.", reply_markup=markup)


@app.on_callback_query(filters.regex(r"buy.*"))
def send_invoice(client: Client, callback_query: types.CallbackQuery):
    chat_id = callback_query.message.chat.id
    data = callback_query.data
    _, count, price = data.split("-")
    price = int(float(price) * 100)
    client.send_invoice(
        chat_id,
        f"{count} permanent download quota",
        "Please make a payment via Stripe",
        f"{count}",
        "USD",
        [types.LabeledPrice(label="VIP", amount=price)],
        provider_token=os.getenv("PROVIDER_TOKEN"),
        protect_content=True,
        start_parameter="no-forward-placeholder",
    )


@app.on_pre_checkout_query()
def pre_checkout(client: Client, query: types.PreCheckoutQuery):
    client.answer_pre_checkout_query(query.id, ok=True)


@app.on_message(filters.successful_payment)
def successful_payment(client: Client, message: types.Message):
    who = message.chat.id
    amount = message.successful_payment.total_amount  # in cents
    quota = int(message.successful_payment.invoice_payload)
    ch = message.successful_payment.provider_payment_charge_id
    free, paid = credit_account(who, amount, quota, ch)
    if paid > 0:
        message.reply_text(f"Payment successful! You now have {free} free and {paid} paid quota.")
    else:
        message.reply_text("Something went wrong. Please contact the admin.")
    message.delete()


@app.on_message(filters.command(["stats"]))
def stats_handler(client: Client, message: types.Message):
    chat_id = message.chat.id
    init_user(chat_id)
    client.send_chat_action(chat_id, enums.ChatAction.TYPING)
    cpu_usage = psutil.cpu_percent()
    total, used, free, disk = psutil.disk_usage("/")
    swap = psutil.swap_memory()
    memory = psutil.virtual_memory()
    boot_time = psutil.boot_time()

    net = psutil.net_io_counters()
    user_stats = (
        "📊 <b>Состояние сервера</b>\n\n"
        f"🖥 Процессор: {cpu_usage}%\n"
        f"💾 Память: {memory.percent}% (свободно {sizeof_fmt(memory.available)} из {sizeof_fmt(memory.total)})\n"
        f"🗃 Диск: {disk}% (свободно {sizeof_fmt(free)} из {sizeof_fmt(total)})\n\n"
        f"📤 Отправлено: {sizeof_fmt(net.bytes_sent)}\n"
        f"📥 Получено: {sizeof_fmt(net.bytes_recv)}\n\n"
        f"🤖 Бот работает: {timeof_fmt(time.time() - botStartTime)}\n"
    )
    owner_stats = user_stats + (
        f"⏲ Сервер работает: {timeof_fmt(time.time() - boot_time)}\n"
        f"🧠 Ядер: {psutil.cpu_count(logical=False)} / {psutil.cpu_count(logical=True)}\n"
        f"🔄 Подкачка: {sizeof_fmt(swap.total)}, занято {swap.percent}%\n"
        f"🎞 yt-dlp: {yt_dlp.version.__version__}\n"
    )

    if message.from_user.id in OWNER:
        message.reply_text(owner_stats, quote=True)
    else:
        message.reply_text(user_stats, quote=True)


def settings_view(chat_id: int) -> tuple[str, types.InlineKeyboardMarkup]:
    quality, send_type = get_quality_settings(chat_id), get_format_settings(chat_id)

    def button(text: str, data: str, current: str) -> types.InlineKeyboardButton:
        return types.InlineKeyboardButton(("✅ " if data == current else "") + text, callback_data=data)

    markup = types.InlineKeyboardMarkup(
        [
            [button("Высокое 1080p", "high", quality)],
            [button("Среднее 720p", "medium", quality)],
            [button("Экономное 480p", "low", quality)],
            [
                button("🎬 Видео", "video", send_type),
                button("📄 Файлом", "document", send_type),
                button("🎵 Звук", "audio", send_type),
            ],
        ]
    )
    text = BotText.settings.format(
        quality=BotText.quality_names.get(quality, quality), send_type=BotText.format_names.get(send_type, send_type)
    )
    return text, markup


@app.on_message(filters.command(["settings"]))
def settings_handler(client: Client, message: types.Message):
    chat_id = message.chat.id
    init_user(chat_id)
    client.send_chat_action(chat_id, enums.ChatAction.TYPING)
    text, markup = settings_view(chat_id)
    client.send_message(chat_id, text, reply_markup=markup)


@app.on_message(filters.command(["direct"]))
def direct_download(client: Client, message: types.Message):
    chat_id = message.chat.id
    init_user(chat_id)
    client.send_chat_action(chat_id, enums.ChatAction.TYPING)
    message_text = message.text
    url, new_name = extract_url_and_name(message_text)
    logging.info("Direct download using aria2/requests start %s", url)
    if url is None or not re.findall(r"^https?://", url.lower()):
        message.reply_text(BotText.not_a_link, quote=True)
        return
    bot_msg = message.reply_text(BotText.received, quote=True)
    try:
        direct_entrance(client, bot_msg, url)
    except Exception as e:
        logging.error("Download failed", exc_info=True)
        bot_msg.delete()
        message.reply_text(friendly_error(e), quote=True)


@app.on_message(filters.command(["spdl"]))
def spdl_handler(client: Client, message: types.Message):
    chat_id = message.chat.id
    init_user(chat_id)
    client.send_chat_action(chat_id, enums.ChatAction.TYPING)
    message_text = message.text
    url, new_name = extract_url_and_name(message_text)
    logging.info("spdl start %s", url)
    if url is None or not re.findall(r"^https?://", url.lower()):
        message.reply_text(BotText.not_a_link, quote=True)
        return
    bot_msg = message.reply_text(BotText.received, quote=True)
    try:
        special_download_entrance(client, bot_msg, url)
    except Exception as e:
        logging.error("Download failed", exc_info=True)
        bot_msg.delete()
        message.reply_text(friendly_error(e), quote=True)


@app.on_message(filters.command(["ytdl"]) & filters.group)
def ytdl_handler(client: Client, message: types.Message):
    # for group only
    init_user(message.from_user.id)
    client.send_chat_action(message.chat.id, enums.ChatAction.TYPING)
    message_text = message.text
    url, new_name = extract_url_and_name(message_text)
    logging.info("ytdl start %s", url)
    if url is None or not re.findall(r"^https?://", url.lower()):
        message.reply_text(BotText.not_a_link, quote=True)
        return

    bot_msg = message.reply_text(BotText.received, quote=True)
    try:
        youtube_entrance(client, bot_msg, url)
    except Exception as e:
        logging.error("Download failed", exc_info=True)
        bot_msg.delete()
        message.reply_text(friendly_error(e), quote=True)


def check_link(url: str):
    ytdl = yt_dlp.YoutubeDL()
    # only YouTube playlists are rejected; vk.com/video...?list=... is a single video
    if re.findall(r"^https://www\.youtube\.com/channel/", url) or ("list" in url and is_youtube(url)):
        # TODO maybe using ytdl.extract_info
        raise FriendlyError(BotText.playlist)

    if not M3U8_SUPPORT and (re.findall(r"m3u8|\.m3u8|\.m3u$", url.lower())):
        return "m3u8 links are disabled."


def friendly_error(e: Exception) -> str:
    # walk the exception chain: yt-dlp may wrap errors raised in our hooks
    err = e
    while err is not None:
        if isinstance(err, FriendlyError):
            return str(err)
        err = err.__cause__ or err.__context__

    raw = re.sub(r"\x1b\[[0-9;]*m", "", str(e))
    low = raw.lower()
    if "unsupported url" in low:
        text = BotText.error_unsupported
    elif any(k in low for k in ("private", "followers", "registered users", "login", "sign in", "cookies")):
        text = BotText.error_private
    elif any(k in low for k in ("geo", "your country", "region")):
        text = BotText.error_geo
    elif any(k in low for k in ("unavailable", "not available", "removed", "deleted", "404", "not found")):
        text = BotText.error_unavailable
    else:
        text = BotText.error_generic
    return text + BotText.error_details.format(html.escape(raw[:300]))


URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


@app.on_message(filters.incoming & filters.text)
@private_use
def download_handler(client: Client, message: types.Message):
    chat_id = message.from_user.id
    init_user(chat_id)
    client.send_chat_action(chat_id, enums.ChatAction.TYPING)
    # the link may come inside other text ("look at this https://vk.com/...")
    match = URL_RE.search(message.text)
    if not match:
        message.reply_text(BotText.not_a_link, quote=True)
        return
    url = match.group(0)
    logging.info("start %s", url)

    bot_msg = None
    try:
        check_link(url)
        bot_msg: types.Message | Any = message.reply_text(BotText.received, quote=True)
        client.send_chat_action(chat_id, enums.ChatAction.UPLOAD_VIDEO)
        youtube_entrance(client, bot_msg, url)
    except pyrogram.errors.Flood as e:
        message.reply_text(BotText.flood_wait.format(e.value), quote=True)
        client.send_message(OWNER[0], f"Flood wait! 🙁 {e} seconds....")
        time.sleep(e.value)
    except Exception as e:
        logging.error("Download failed for %s", url, exc_info=True)
        text = friendly_error(e)
        try:
            bot_msg.edit_text(text) if bot_msg else message.reply_text(text, quote=True)
        except Exception:
            message.reply_text(text, quote=True)


@app.on_callback_query(filters.regex(r"^(document|video|audio)$"))
def format_callback(client: Client, callback_query: types.CallbackQuery):
    chat_id = callback_query.message.chat.id
    logging.info("Setting %s file type to %s", chat_id, callback_query.data)
    set_user_settings(chat_id, "format", callback_query.data)
    callback_query.answer(BotText.setting_saved)
    text, markup = settings_view(chat_id)
    callback_query.message.edit_text(text, reply_markup=markup)


@app.on_callback_query(filters.regex(r"^(high|medium|low)$"))
def quality_callback(client: Client, callback_query: types.CallbackQuery):
    chat_id = callback_query.message.chat.id
    logging.info("Setting %s download quality to %s", chat_id, callback_query.data)
    set_user_settings(chat_id, "quality", callback_query.data)
    callback_query.answer(BotText.setting_saved)
    text, markup = settings_view(chat_id)
    callback_query.message.edit_text(text, reply_markup=markup)


def setup_commands():
    # the "/" menu in Telegram: short list for everyone, a few extra for the owner
    commands = [types.BotCommand(c, d) for c, d in BotText.commands]
    owner_commands = commands + [types.BotCommand(c, d) for c, d in BotText.owner_commands]
    app.set_bot_commands(commands)
    for owner in OWNER:
        try:
            app.set_bot_commands(owner_commands, scope=types.BotCommandScopeChat(owner))
        except Exception as e:
            logging.warning("Can't set owner commands for %s: %s", owner, e)


if __name__ == "__main__":
    botStartTime = time.time()
    scheduler = BackgroundScheduler()
    scheduler.add_job(reset_free, "cron", hour=0, minute=0)
    scheduler.start()
    banner = f"""
▌ ▌         ▀▛▘     ▌       ▛▀▖              ▜            ▌
▝▞  ▞▀▖ ▌ ▌  ▌  ▌ ▌ ▛▀▖ ▞▀▖ ▌ ▌ ▞▀▖ ▌  ▌ ▛▀▖ ▐  ▞▀▖ ▝▀▖ ▞▀▌
 ▌  ▌ ▌ ▌ ▌  ▌  ▌ ▌ ▌ ▌ ▛▀  ▌ ▌ ▌ ▌ ▐▐▐  ▌ ▌ ▐  ▌ ▌ ▞▀▌ ▌ ▌
 ▘  ▝▀  ▝▀▘  ▘  ▝▀▘ ▀▀  ▝▀▘ ▀▀  ▝▀   ▘▘  ▘ ▘  ▘ ▝▀  ▝▀▘ ▝▀▘

By @BennyThink, VIP Mode: {ENABLE_VIP} 
    """
    print(banner)
    app.start()
    setup_commands()
    idle()
    app.stop()
