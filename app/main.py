"""Ponto de entrada da aplicação.

Monta `PlaybackService` + `bot/`, inicia tudo e aguarda SIGINT/SIGTERM para um
shutdown gracioso. Qualquer exceção não capturada em uma task de background é
logada em `errors.log` (nunca derruba o processo) via `loop.set_exception_handler`.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys
from typing import Any

from pyrogram import Client

from app.bot.client import build_bot
from app.config.settings import get_settings
from app.services.addon_service import AddonService
from app.services.channel_media_service import ChannelMediaService
from app.services.playback_service import PlaybackService
from app.services.tmdb_service import TMDBService
from app.services.torrent_service import TorrentService
from app.telegram.bot_api import BotAPIClient
from app.utils.logging import get_logger, setup_logging

_logger = get_logger("services")


def _handle_task_exception(_loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
    exception = context.get("exception")
    message = context.get("message", "Exceção não tratada em task de background.")
    if exception is not None:
        _logger.opt(exception=exception).error(message)
    else:
        _logger.error(message)


async def _run() -> None:
    settings = get_settings()
    setup_logging(settings)

    asyncio.get_running_loop().set_exception_handler(_handle_task_exception)

    service = PlaybackService(settings)
    torrent_service = TorrentService(settings)
    service.set_source_released_callback(torrent_service.release)
    tmdb_service = TMDBService(settings)
    addon_service = AddonService(settings, service, torrent_service, tmdb_service)
    channel_media_service = ChannelMediaService(
        settings, service.client, service, addon_service, tmdb_service
    )
    service.set_source_released_callback(channel_media_service.release)

    bot_client: Client | None = None
    bot_api: BotAPIClient | None = None
    if settings.bot_token is not None:
        token = settings.bot_token.get_secret_value()
        bot_client = Client(
            name="telegram-video-bridge-botapi",
            workdir="/app/data",
            api_id=settings.api_id,
            api_hash=settings.api_hash.get_secret_value(),
            bot_token=token,
        )
        bot_api = BotAPIClient(token)
    build_bot(
        service,
        addon_service,
        settings,
        tmdb_service,
        bot_client,
        channel_media_service,
        bot_api,
    )

    await service.start()
    if bot_client is not None:
        await bot_client.start()
    await addon_service.start()
    _logger.info("Telegram Video Bridge em execução.")

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # Plataforma sem suporte a add_signal_handler (ex.: Windows); Ctrl+C ainda funciona.
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop_event.set)

    try:
        await stop_event.wait()
    finally:
        _logger.info("Encerrando...")
        await service.shutdown()
        if bot_client is not None:
            await bot_client.stop()
        if bot_api is not None:
            await bot_api.close()
        await addon_service.close()
        await channel_media_service.close()
        await tmdb_service.close()
        await torrent_service.close()


def main() -> None:
    """Instala uvloop (Linux) e executa a aplicação até receber SIGINT/SIGTERM."""
    if sys.platform != "win32":
        import uvloop

        uvloop.install()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
