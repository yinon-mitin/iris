# Security policy

[English](SECURITY.md) · [Русский](SECURITY.ru.md)

## Reporting

Please report vulnerabilities privately through [GitHub security advisories](https://github.com/yinon-mitin/iris/security/advisories/new), not in a public issue. Do not include bot tokens, API keys, server addresses, logs with user IDs or screenshots of private chats.

## What Iris protects

| Threat | Protection |
| --- | --- |
| Strangers using your bot | Access gate on every command, message and button; owner-approved list |
| A link that reaches your home network, tailnet or Docker host (SSRF) | Container egress firewall, fail-closed, not removable by the bot process; refusal with a clear message |
| Downloads piling up on disk | Per-job temporary directory removed when the job ends; hourly sweep after crashes |
| A Telegram error breaking a download | Progress updates are isolated from the download; uploads are retried |
| Secrets in Git | `.env`, `data/`, cookies and sessions are ignored; `make check` scans for tokens and keys |

Design details: [Architecture](docs/ARCHITECTURE.md).

## Operator responsibilities

- Keep `.env` and backups private: the bot token and `APP_HASH` give full control of the bot.
- Keep access invite-based. There are no daily limits, and every download leaves through your IP address.
- If you add YouTube cookies, use a separate account: every allowed user can download what that account can see.
- Keep `cap_add: [NET_ADMIN]` in Compose; without it the container refuses to start rather than run without the firewall.
- If a secret was ever committed, deleting the file is not enough: revoke it (BotFather `/revoke`, a new `APP_HASH`) and rewrite history before sharing the repository.

## Supported versions

Only the latest `main` is supported. The bot updates yt-dlp by itself; update Iris with `make update`.
