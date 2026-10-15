#!/bin/sh
# Keep yt-dlp on the nightly channel: sites like ok.ru / vk often get fixed there first.
# Updates on every start, then checks daily and restarts the bot when a new build appears.

# The bot downloads any link it is sent, so it must never reach the home network, the tailnet
# or the Docker host: a link (or a redirect, or a DNS name) pointing there is refused at the
# network level for yt-dlp, requests, aria2 and ffmpeg alike. Internet and Telegram stay open.
setup_firewall() {
    iptables -F OUTPUT &&
    iptables -A OUTPUT -o lo -j ACCEPT || return 1  # Docker's embedded DNS lives on 127.0.0.11
    for net in 0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 169.254.0.0/16 172.16.0.0/12 \
        192.0.0.0/24 192.0.2.0/24 192.168.0.0/16 198.18.0.0/15 198.51.100.0/24 203.0.113.0/24 \
        224.0.0.0/4 240.0.0.0/4; do
        iptables -A OUTPUT -d "$net" -j REJECT || return 1
    done
    # no IPv6 route in the container; block it anyway in case it gets enabled
    { ip6tables -F OUTPUT && ip6tables -A OUTPUT -o lo -j ACCEPT && ip6tables -A OUTPUT -j REJECT; } 2>/dev/null \
        || echo "ip6tables unavailable, IPv6 not filtered"
}

if ! setup_firewall; then
    echo "Firewall setup failed (is NET_ADMIN granted?), refusing to start"
    exit 1
fi
echo "Firewall: home network and private addresses blocked"

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

# the bot never needs to touch the firewall: drop NET_ADMIN so it can't undo the rules
exec setpriv --bounding-set=-net_admin --inh-caps=-net_admin python main.py
