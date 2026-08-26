"""Monta o(s) cliente(s) Pyrogram com todos os handlers de comando registrados.

`playback_service.client` nunca é substituído: é a mesma sessão MTProto
(`SESSION_STRING`) que `TelegramCallManager` usa para participar da chamada
de vídeo, e continua sendo o client de `/play`/`/pause`/etc. e de
`/addons`/`/addon` (gerenciamento).

`bot_client`, se passado (`BOT_TOKEN` configurado — ver `app/main.py`), é um
segundo `Client` (conta de bot via BotFather) usado só para `/find`/`/pick` e
o callback de botão: mensagens enviadas por uma conta de bot podem carregar
`reply_markup` (botões inline), o que o Telegram descarta silenciosamente em
mensagens de uma conta de usuário. Sem `bot_client`, `/find`/`/pick` caem de
volta no client de sessão, sem botão — mesmo comportamento de sempre.
"""

from __future__ import annotations

from pyrogram import Client, filters

from app.bot.auth import build_authorized_filter, build_owner_filter, build_stream_admin_filter
from app.bot.handlers import (
    addons,
    channel,
    info,
    menu,
    onboarding,
    playback,
    queue,
    status,
    unauthorized,
)
from app.config.settings import Settings
from app.services.addon_service import AddonService
from app.services.channel_media_service import ChannelMediaService
from app.services.playback_service import PlaybackService
from app.services.tmdb_service import TMDBService
from app.telegram.bot_api import BotAPIClient


def build_bot(
    playback_service: PlaybackService,
    addon_service: AddonService,
    settings: Settings,
    tmdb_service: TMDBService,
    bot_client: Client | None = None,
    channel_media_service: ChannelMediaService | None = None,
    bot_api: BotAPIClient | None = None,
) -> Client:
    """Registra todos os handlers de comando e devolve o client de sessão.

    `tmdb_service` não é mais tocado diretamente aqui (o filtro de `/find`
    mora em `AddonService`) — mantido como parâmetro para não quebrar quem
    já constrói/injeta `TMDBService` antes de chamar `build_bot`.
    """
    app = playback_service.client
    authorized = build_authorized_filter(settings)
    bot_authorized = build_stream_admin_filter(settings)
    owner = build_owner_filter(settings)

    def _register_controls(client: Client, access: filters.Filter = authorized) -> None:
        info.register(client, access if client is bot_client else None)
        playback.register(client, playback_service, access)
        queue.register(client, playback_service, access)
        status.register(client, playback_service, access)
        addons.register_management(client, addon_service, access, owner)

    _register_controls(app)
    if bot_client is not None and bot_client is not app:
        onboarding.register(bot_client, settings, bot_api=bot_api)
        _register_controls(bot_client, bot_authorized)
        if channel_media_service is not None:
            channel.register(bot_client, channel_media_service, bot_authorized)
        if bot_api is not None:
            menu.register(
                bot_client,
                playback_service,
                addon_service,
                bot_authorized,
                owner,
                bot_api,
                channel_media_service,
            )

    search_client = bot_client if bot_client is not None else app
    addons.register_search(
        search_client,
        addon_service,
        bot_authorized if bot_client is not None else authorized,
        buttons_enabled=bot_client is not None,
        bot_api=bot_api,
    )

    unauthorized.register(app, authorized, search_commands=bot_client is None)
    if bot_client is not None and bot_client is not app:
        unauthorized.register(bot_client, bot_authorized)

    return app
