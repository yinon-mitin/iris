# Contributing

[English](CONTRIBUTING.md) · [Русский](CONTRIBUTING.ru.md)

Iris is a small, self-hosted project. Keep changes focused, reviewable and free of personal data.

## Workflow

1. Create a branch from `main`.
2. Make the change. User-facing text goes into `src/config/constant.py`, in Russian, with HTML markup.
3. Run `make test`: static checks, an image build and the unit tests inside it.
4. Update both language versions of any document you touch.
5. Open a pull request that explains the behaviour change and how you verified it.

## Rules

- Never commit `.env`, `data/`, session files, cookies, logs, tokens or downloaded media. `make check` rejects the obvious cases.
- Do not weaken the egress firewall or the access gate. A change that touches either needs a test or a written reproduction.
- New engines must download into the job's temporary directory (`self._tempdir`) so cleanup covers them.
- Every message the bot sends must be in `BotText`; no hard-coded strings in handlers.
- Keep GitHub Actions pinned by commit SHA, with the version in a comment.
- Real links in tests and docs must point to public, stable content, never to private people's posts.

## Tests

Unit tests live in `tests/` and run inside the production image, so they see the same Python, yt-dlp and ffmpeg as the bot:

```sh
make test
```

They do not need Telegram credentials or network access.

## Acceptance

A change is ready when `make test` passes, the diff is reviewed, and any change to downloading or uploading has been tried against the running bot with at least one real link.
