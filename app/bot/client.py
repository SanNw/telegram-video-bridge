"""Monta o cliente Pyrogram com todos os handlers de comando registrados.

Não cria uma nova sessão MTProto: reusa `playback_service.client`, a mesma
instância `pyrogram.Client` que `TelegramCallManager` usa para participar da
chamada (única `SESSION_STRING` configurada — ver `app/telegram/call_manager.py`).
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
) -> Client:
    """Registra todos os handlers de comando no cliente de `playback_service` e o retorna."""
    app = playback_service.client
    authorized = build_authorized_filter(settings)
    owner = build_owner_filter(settings)

    info.register(app)
    playback.register(app, playback_service, authorized)
    queue.register(app, playback_service, authorized)
    status.register(app, playback_service, authorized)
    addons.register(app, addon_service, authorized, owner, tmdb_service)
    unauthorized.register(app)

    return app
