# Operations

[English](OPERATIONS.md) · [Русский](ru/OPERATIONS.md)

Day-to-day running of an installed bot. For first-time setup see [Install](INSTALL.md).

## Commands

| Command | What it does |
| --- | --- |
| `make up` | Build the image and start or update the bot |
| `make down` | Stop and remove the container (data in `data/` stays) |
| `make restart` | Restart; installs the newest yt-dlp nightly on the way |
| `make ps` | Container status |
| `make logs` | Follow the log (`Ctrl+C` to stop following) |
| `make shell` | Shell inside the running container |
| `make check` | Static checks without Docker: secrets, docs, Python syntax, shell, Compose |
| `make test` | `make check`, then build the image and run the unit tests inside it |

## Updates

**yt-dlp updates itself.** The container installs the newest nightly build on every start and checks again every `YTDLP_UPDATE_INTERVAL` seconds (daily by default). When a new build appears, the bot restarts by itself.

**Updating Iris:**

```sh
make update        # git pull --ff-only, then make up
```

Settings, users and the allow list live in `data/` and `.env` and survive updates.

## Backups

Everything worth keeping is two items:

```sh
tar czf iris-backup.tgz .env data/
```

Restore by unpacking into a fresh clone and running `make up`. Treat the archive as a secret: `.env` holds the bot token and API keys.

## Health checks

```sh
make ps                               # container is "Up"
make logs                             # "Firewall: …", "yt-dlp version: …", "Session started"
docker compose exec bot ls /tmp      # empty when no job is running
```

## Troubleshooting

**The bot does not answer.**
Run `make logs`. Check that your ID is in `OWNER` or `AUTHORIZED_USER` and contains only digits.

**The container exits with "Firewall setup failed".**
The container lacks `NET_ADMIN`. Keep the `cap_add: [NET_ADMIN]` block in `docker-compose.yml`. Some rootless or restricted Docker setups do not allow it; the bot deliberately refuses to run without the firewall.

**"This link leads into the internal network".**
The link points to a private address, or a domain that resolves to one. That is intentional and cannot be switched off from the chat; see [Architecture](ARCHITECTURE.md#egress-firewall).

**"The video is too large".**
Telegram accepts files up to 2 GB. Choose medium or low quality in `/settings`.

**A site stopped working.**
Usually the site changed and yt-dlp has not caught up yet. Run `make restart`: the newest nightly is installed on start. If that does not help, search the [yt-dlp issues](https://github.com/yt-dlp/yt-dlp/issues).

**YouTube or Reddit asks to sign in.**
Add cookies as described in [Install](INSTALL.md#6-every-env-setting).

**`docker-credential-osxkeychain: executable file not found` (macOS + OrbStack over SSH).**
The SSH session does not have OrbStack's tools on `PATH`:

```sh
export PATH="$HOME/.orbstack/bin:$PATH"
```

**The bot did not come back after a reboot.**
Start OrbStack or Docker Desktop at login; the container restarts by itself.

## Pulling changes from the original ytdlbot

Iris is independent, but fixes from the original project can still be merged by hand:

```sh
git remote add upstream https://github.com/tgbot-collection/ytdlbot.git   # once
git fetch upstream
git merge upstream/master
make test && make up
```

Expect conflicts in `src/main.py`, `src/engine/` and `src/config/constant.py`, which Iris has changed substantially.
