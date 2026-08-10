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

from pyrogram import Client

from app.bot.auth import build_authorized_filter, build_owner_filter
from app.bot.handlers import addons, info, playback, queue, status, unauthorized
from app.config.settings import Settings
from app.services.addon_service import AddonService
from app.services.playback_service import PlaybackService
from app.services.tmdb_service import TMDBService


def build_bot(
    playback_service: PlaybackService,
    addon_service: AddonService,
    settings: Settings,
    tmdb_service: TMDBService,
    bot_client: Client | None = None,
) -> Client:
    """Registra todos os handlers de comando e devolve o client de sessão.

    `tmdb_service` não é mais tocado diretamente aqui (o filtro de `/find`
    mora em `AddonService`) — mantido como parâmetro para não quebrar quem
    já constrói/injeta `TMDBService` antes de chamar `build_bot`.
    """
    app = playback_service.client
    authorized = build_authorized_filter(settings)
    owner = build_owner_filter(settings)

    info.register(app)
    playback.register(app, playback_service, authorized)
    queue.register(app, playback_service, authorized)
    status.register(app, playback_service, authorized)
    addons.register_management(app, addon_service, authorized, owner)

    search_client = bot_client if bot_client is not None else app
    addons.register_search(search_client, addon_service, authorized, buttons_enabled=bot_client is not None)

    unauthorized.register(app)
    if bot_client is not None:
        unauthorized.register(bot_client)

    return app
