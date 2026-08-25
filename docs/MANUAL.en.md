# Telerion operations manual

[Português](MANUAL.md) · [Español](MANUAL.es.md)

## 1. Required architecture

Telerion uses two Telegram identities: a dedicated user account authenticated by `SESSION_STRING` for MTProto/live access, and an optional BotFather bot for private commands and inline buttons. The target account and bot must be administrators of the live-enabled channel.

External components are TMDB for catalog metadata, qBittorrent for progressive torrent downloads and FFmpeg for encoding. qBittorrent and Telerion must see the same download directory, even if their path names differ.

## 2. Installation

```bash
git clone https://github.com/SanNw/telegram-video-bridge.git
cd telegram-video-bridge
cp .env.example .env
uv sync
uv run python -u scripts/generate_session.py
uv run python scripts/list_chats.py @operator_username
uv run python -m app.doctor
docker compose up -d --build
```

Set `MEDIA_HOST_PATH` to the host directory mounted at `/app/media/torrents`. Set `QBITTORRENT_SAVE_PATH` to the path reported by qBittorrent and `QBITTORRENT_LOCAL_PATH=/app/media/torrents` inside Docker.

## 3. Operation

Check the service with `docker compose ps` and `docker compose logs --tail 100 bridge`. Use `/status` for queue, FFmpeg and Telegram transport state. Stop with `docker compose stop bridge`; do not use `docker compose down -v` unless persistent data should be deleted.

## 4. Security

Never commit `.env`, session strings, tokens or passwords. Use a dedicated Telegram account with 2FA. Keep qBittorrent on a trusted network. Addons execute inside the credential-bearing process and must be reviewed before installation.

## 5. Backup and upgrades

Record the current commit or release tag, stop the old instance, back up `.env` outside Git and preserve the Docker `data` volume if queue/addon state matters. Upgrade with `git pull --ff-only` and `docker compose up -d --build --force-recreate`. Never run two instances with the same bot token and session.

## 6. Troubleshooting

- Bot silent: verify `BOT_TOKEN`, channel membership and that no second instance uses the token.
- Live does not start: verify account permissions, `STREAM_CHAT_ID`, FFmpeg logs and upload bandwidth.
- Torrent file missing: fix the mapping between `MEDIA_HOST_PATH`, `QBITTORRENT_SAVE_PATH` and `QBITTORRENT_LOCAL_PATH`.
- Subtitle offset: use `/subdelay`; growing drift requires a subtitle from the same media release.

The [Portuguese manual](MANUAL.md) remains the exhaustive variable-by-variable reference.
