#!/bin/sh
# Keep yt-dlp on the nightly channel: sites like ok.ru / vk often get fixed there first.
# Updates on every start, then checks daily and restarts the bot when a new build appears.

update_ytdlp() {
    pip install -q --root-user-action=ignore --disable-pip-version-check -U --pre "yt-dlp[default]" \
        || echo "yt-dlp update failed, keeping current version"
}

update_ytdlp
echo "yt-dlp version: $(python -m yt_dlp --version)"

(
    while true; do
        sleep "${YTDLP_UPDATE_INTERVAL:-86400}"
        old=$(python -m yt_dlp --version)
        update_ytdlp
        new=$(python -m yt_dlp --version)
        if [ "$old" != "$new" ]; then
            echo "yt-dlp updated $old -> $new, restarting bot"
            kill 1
        fi
    done
) &

exec python main.py
