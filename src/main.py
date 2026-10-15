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
    add_allowed_user,
    create_invite,
    credit_account,
    get_allowed_users,
    get_bot_setting,
    get_format_settings,
    get_free_quota,
    get_paid_quota,
    get_quality_settings,
    init_user,
    remove_allowed_user,
    reset_free,
    set_bot_setting,
    set_user_settings,
    use_invite,
)
from engine import direct_entrance, youtube_entrance, special_download_entrance
from utils import FriendlyError, clean_tempfile, extract_url_and_name, is_youtube, sizeof_fmt, timeof_fmt

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

# private mode: only OWNER, AUTHORIZED_USER and users added from the bot can talk to it, for every handler
ENV_ALLOWED_IDS = set(OWNER) | {int(i) for i in str(AUTHORIZED_USER or "").split(",") if i.strip()}
ALLOWED_IDS = ENV_ALLOWED_IDS | {uid for uid, _ in get_allowed_users()}


def short_name(user: types.User) -> str:
    name = " ".join(filter(None, [user.first_name, user.last_name])) or "Без имени"
    return f"{name} (@{user.username})" if user.username else name


def user_label(user: types.User) -> str:
    return f"{html.escape(short_name(user))} <code>{user.id}</code>"


def grant_access(uid: int, name: str | None, added_by: int):
    add_allowed_user(uid, name, added_by)
    ALLOWED_IDS.add(uid)


@app.on_message(group=-1)
def auth_gate(client: Client, message: types.Message):
    uid = getattr(message.from_user, "id", None)
    if uid in ALLOWED_IDS:
        return
    logging.warning("Rejected message from %s: %s", uid, getattr(message, "text", ""))
    if message.chat.type == enums.ChatType.PRIVATE and (message.text or "").startswith("/start"):
        # invite link: t.me/<bot>?start=inv_xxx arrives as "/start inv_xxx"
        parts = message.text.split(maxsplit=1)
        token = parts[1].strip() if len(parts) > 1 else ""
        inviter = use_invite(token, uid) if token.startswith("inv_") else None
        if inviter is not None:
            grant_access(uid, short_name(message.from_user), inviter)
            logging.info("User %s joined via invite from %s", uid, inviter)
            message.reply_text(BotText.invite_welcome, quote=True)
            try:
                client.send_message(inviter, BotText.invite_used_notice.format(who=user_label(message.from_user)))
            except Exception as e:
                logging.warning("Can't notify %s about used invite: %s", inviter, e)
            return  # let start_handler greet the new user
        text = BotText.invite_invalid if token.startswith("inv_") else BotText.private
        message.reply_text(text.format(uid), quote=True)
        notify_access_request(client, message.from_user)
    message.stop_propagation()


# uid -> time of the last request sent to the owner, so repeated /start doesn't spam
last_requests: dict[int, float] = {}
REQUEST_COOLDOWN = 3600


def notifications_enabled() -> bool:
    return get_bot_setting("notify_requests", "on") == "on"


def notify_access_request(client: Client, user: types.User):
    if not notifications_enabled() or time.time() - last_requests.get(user.id, 0) < REQUEST_COOLDOWN:
        return
    last_requests[user.id] = time.time()
    markup = types.InlineKeyboardMarkup(
        [
            [
                types.InlineKeyboardButton(BotText.access_allow, callback_data=f"allow-{user.id}"),
                types.InlineKeyboardButton(BotText.access_deny, callback_data=f"deny-{user.id}"),
            ]
        ]
    )
    for owner in OWNER:
        try:
            client.send_message(owner, BotText.access_request.format(who=user_label(user)), reply_markup=markup)
        except Exception as e:
            logging.warning("Can't send access request to %s: %s", owner, e)


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


@app.on_message(filters.command(["invite"]) & filters.user(OWNER))
def invite_handler(client: Client, message: types.Message):
    token = create_invite(message.from_user.id)
    link = f"https://t.me/{client.me.username}?start={token}"
    message.reply_text(BotText.invite_created.format(link=link), quote=True, disable_web_page_preview=True)


@app.on_message(filters.command(["adduser"]) & filters.user(OWNER))
def adduser_handler(client: Client, message: types.Message):
    user, uid = None, None
    reply = message.reply_to_message
    origin = getattr(reply, "forward_origin", None) if reply else None
    if len(message.command) > 1 and message.command[1].isdigit():
        uid = int(message.command[1])
        try:
            user = client.get_users(uid)
        except Exception:
            pass  # the bot may not know this user yet, the id is enough
    elif getattr(origin, "sender_user", None):
        user = origin.sender_user
        uid = user.id
    elif origin is not None:
        message.reply_text(BotText.adduser_hidden, quote=True)
        return
    else:
        message.reply_text(BotText.adduser_usage, quote=True)
        return

    grant_access(uid, short_name(user) if user else None, message.from_user.id)
    logging.info("User %s added by %s", uid, message.from_user.id)
    message.reply_text(BotText.adduser_done.format(who=user_label(user) if user else f"<code>{uid}</code>"), quote=True)
    try:
        client.send_message(uid, BotText.adduser_notice)
    except Exception:
        pass  # the user hasn't started the bot yet, they'll get access when they do


@app.on_message(filters.command(["deluser"]) & filters.user(OWNER))
def deluser_handler(client: Client, message: types.Message):
    if len(message.command) < 2 or not message.command[1].isdigit():
        message.reply_text(BotText.deluser_usage, quote=True)
        return
    uid = int(message.command[1])
    if uid in ENV_ALLOWED_IDS:
        message.reply_text(BotText.deluser_env.format(uid), quote=True)
        return
    if remove_allowed_user(uid):
        ALLOWED_IDS.discard(uid)
        logging.info("User %s removed by %s", uid, message.from_user.id)
        message.reply_text(BotText.deluser_done.format(uid), quote=True)
    else:
        message.reply_text(BotText.deluser_missing.format(uid), quote=True)


@app.on_message(filters.command(["users"]) & filters.user(OWNER))
def users_handler(client: Client, message: types.Message):
    lines = [BotText.users_header]
    env_users = sorted(ENV_ALLOWED_IDS - set(OWNER))
    if env_users:
        lines.append("<b>Из .env:</b>")
        lines += [f"• <code>{uid}</code>" for uid in env_users]
    db_users = get_allowed_users()
    if db_users:
        lines.append("<b>Добавлены через бота:</b>")
        lines += [f"• {html.escape(name or '—')} <code>{uid}</code>" for uid, name in db_users]
        lines.append("\nУбрать доступ: <code>/deluser номер</code>")
    if not env_users and not db_users:
        lines.append(BotText.users_empty)
    message.reply_text("\n".join(lines), quote=True)


@app.on_callback_query(filters.regex(r"^(allow|deny)-\d+$") & filters.user(OWNER))
def access_request_callback(client: Client, callback_query: types.CallbackQuery):
    action, uid = callback_query.data.split("-")
    uid = int(uid)
    try:
        user = client.get_users(uid)
        who = user_label(user)
    except Exception:
        user, who = None, f"<code>{uid}</code>"

    if action == "deny":
        last_requests.pop(uid, None)
        callback_query.message.edit_text(BotText.access_denied.format(who=who))
        return
    if uid in ALLOWED_IDS:
        callback_query.answer(BotText.access_already)
        callback_query.message.edit_text(BotText.access_allowed.format(who=who))
        return
    grant_access(uid, short_name(user) if user else None, callback_query.from_user.id)
    logging.info("User %s allowed by %s from access request", uid, callback_query.from_user.id)
    callback_query.message.edit_text(BotText.access_allowed.format(who=who))
    try:
        client.send_message(uid, BotText.adduser_notice)
    except Exception as e:
        logging.warning("Can't notify %s about access: %s", uid, e)


def notify_view() -> tuple[str, types.InlineKeyboardMarkup]:
    on = notifications_enabled()
    text = BotText.notify_state.format(state=BotText.notify_on if on else BotText.notify_off)
    button = types.InlineKeyboardButton(
        BotText.notify_turn_off if on else BotText.notify_turn_on, callback_data="notify-off" if on else "notify-on"
    )
    return text, types.InlineKeyboardMarkup([[button]])


@app.on_message(filters.command(["notify"]) & filters.user(OWNER))
def notify_handler(client: Client, message: types.Message):
    text, markup = notify_view()
    message.reply_text(text, quote=True, reply_markup=markup)


@app.on_callback_query(filters.regex(r"^notify-(on|off)$") & filters.user(OWNER))
def notify_callback(client: Client, callback_query: types.CallbackQuery):
    set_bot_setting("notify_requests", callback_query.data.split("-")[1])
    callback_query.answer(BotText.setting_saved)
    text, markup = notify_view()
    callback_query.message.edit_text(text, reply_markup=markup)


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
    scheduler.add_job(clean_tempfile, "interval", hours=1)
    scheduler.start()
    banner = f"""
  Iris · private Telegram downloader
  https://github.com/yinon-mitin/iris · based on ytdlbot by @BennyThink
  VIP mode: {ENABLE_VIP}
    """
    print(banner)
    app.start()
    setup_commands()
    idle()
    app.stop()
