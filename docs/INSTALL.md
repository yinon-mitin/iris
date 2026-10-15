# Installation and settings

[English](INSTALL.md) · [Русский](ru/INSTALL.md)

How to run Iris on your own computer (macOS, Linux) or a VPS. Setup takes about 15 minutes.

- [1. Requirements](#1-requirements)
- [2. Telegram keys](#2-telegram-keys)
- [3. Install](#3-install)
- [4. Access for family and friends](#4-access-for-family-and-friends)
- [5. Bot branding](#5-bot-branding)
- [6. Every `.env` setting](#6-every-env-setting)
- [7. Where to run: home or VPS](#7-where-to-run-home-or-vps)

Updates, backups and troubleshooting are covered in [Operations](OPERATIONS.md).

## 1. Requirements

| What | Why |
| --- | --- |
| A computer or VPS that stays on 24/7 | Runs the bot |
| [Docker](https://docs.docker.com/engine/install/) with Compose v2. On macOS, [OrbStack](https://orbstack.dev) or Docker Desktop | Runs the bot container |
| Git and Make | Fetch and manage the project |
| 5+ GB of free disk space | Temporary files, up to 2 GB per download; removed right after upload |
| A Telegram account | Create the bot and get the keys |

Any architecture works: x86_64 or ARM, including Apple Silicon (Mac mini M1–M4).

## 2. Telegram keys

You need four values:

1. **`BOT_TOKEN`**, the bot token.
   Message [@BotFather](https://t.me/BotFather) → `/newbot` → choose a name and username. BotFather replies with a token like `123456789:AA...`.
2. **`APP_ID`** and **`APP_HASH`**, the Telegram API keys. They let the bot upload files up to 2 GB instead of 50 MB.
   Sign in at [my.telegram.org](https://my.telegram.org) → **API development tools** → create an application (any name).
3. **`OWNER`**, your numeric Telegram ID.
   [@userinfobot](https://t.me/userinfobot) will tell you.

> [!WARNING]
> Never share `BOT_TOKEN` or `APP_HASH`, and never publish `.env`. It is already in `.gitignore`.

## 3. Install

```sh
git clone https://github.com/yinon-mitin/iris.git
cd iris
make init
```

`make init` creates `.env` from the template, a `data/` directory for the database and an empty `youtube-cookies.txt`. Open `.env` in any editor and fill in the first block:

```ini
APP_ID=12345678
APP_HASH=0123456789abcdef0123456789abcdef
BOT_TOKEN=123456789:AA...
OWNER=111111111
AUTHORIZED_USER=
```

Check and start:

```sh
make test
make up
make logs
```

The first build takes a few minutes. The log should show `Firewall: home network and private addresses blocked`, `yt-dlp version: …` and `Session started`. Open your bot in Telegram, press **Start** and send a link.

> [!NOTE]
> The container needs `NET_ADMIN` so its entrypoint can close off your home network at startup. It is already set in `docker-compose.yml`; the bot process itself runs without it. See [Architecture](ARCHITECTURE.md#egress-firewall).

## 4. Access for family and friends

The bot is private: by default only the owner (`OWNER`) can use it. People are added from the bot itself, no restart needed. Only the owner sees and can run these commands:

| Command | What it does |
| --- | --- |
| `/invite` | A one-time invite link. Send it to someone: they press **Start**, get access immediately, and you get a notification |
| `/adduser 222222222` | Grant access by Telegram ID. The bot shows a stranger their ID when they press **Start**. You can also reply `/adduser` to a message forwarded from that person |
| `/deluser 222222222` | Revoke access |
| `/users` | Everyone who has access |
| `/notify` | Turn notifications about new people on or off |

When a stranger presses **Start**, the bot sends you a notification with **✅ Allow** and **🚫 Deny** buttons, at most once an hour per person.

People added through the bot are stored in the database (`data/`) and survive restarts. You can also list IDs in `.env` (comma-separated, no spaces) and run `make up`:

```ini
AUTHORIZED_USER=222222222,333333333
```

Those users can only be removed by editing `.env`, not with `/deluser`.

## 5. Bot branding

All through [@BotFather](https://t.me/BotFather):

| Command | What to set |
| --- | --- |
| `/setuserpic` | The avatar: [`assets/logo.png`](../assets/logo.png) |
| `/setdescription` | Text shown before Start, e.g. «Пришлите ссылку — я скачаю видео, фото или файл и отправлю сюда.» |
| `/setabouttext` | The short profile description |
| `/setjoingroups` → **Disable** | Prevent adding the bot to groups |

The bot sets up its own command menu («/») on start.

## 6. Every `.env` setting

| Variable | Default | Description |
| --- | --- | --- |
| `APP_ID`, `APP_HASH` | — | Telegram API keys from my.telegram.org. **Required** |
| `BOT_TOKEN` | — | Token from @BotFather. **Required** |
| `OWNER` | — | Owner Telegram ID(s), comma-separated. **Required** |
| `AUTHORIZED_USER` | empty | Allowed user IDs, comma-separated. The owner is always allowed |
| `WORKERS` | `16` | How many messages the bot handles at once |
| `DB_DSN` | `sqlite:////data/iris.db` | Users and settings database, kept in `./data` |
| `REDIS_HOST` | empty | Empty means an in-memory cache; an external Redis also works |
| `YTDLP_UPDATE_INTERVAL` | `86400` | How often to check for a new yt-dlp nightly, in seconds |
| `ENABLE_ARIA2` | `False` | Use aria2 for the `/direct` command |
| `M3U8_SUPPORT` | `False` | Allow direct `.m3u8` links |
| `AUDIO_FORMAT` | empty (m4a) | Format for the audio-only mode, e.g. `mp3` |
| `TMPFILE_PATH` | empty | Temporary directory inside the container |
| `ENABLE_VIP`, `PROVIDER_TOKEN` | `False` | Paid mode from the original project; not needed for a private bot |

**YouTube cookies.** If YouTube asks you to sign in (common on a VPS), export browser cookies in Netscape format to `youtube-cookies.txt` in the project root and run `make restart`. Use a separate account, not your main one: everything that account can see becomes downloadable by every bot user. The file is excluded from Git.

## 7. Where to run: home or VPS

| | Home computer (e.g. a Mac mini) | Data-center VPS |
| --- | --- | --- |
| Cost | Free | From ~$5/month |
| Availability | While the computer is on and online | 24/7 |
| Site blocking | Home IPs are blocked less often | YouTube often asks for cookies; VK may limit foreign IPs |
| Upload speed | Depends on your home connection | Usually high |

VK Video and OK.ru may restrict access from outside Russia. If videos do not open from your server, use a proxy with a suitable IP.

On macOS, disable sleep in **System Settings → Energy**, and start OrbStack or Docker Desktop at login. The container restarts by itself (`restart: unless-stopped`).
