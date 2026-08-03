"""Monta o cliente Pyrogram com todos os handlers de comando registrados.

Não cria uma nova sessão MTProto: reusa `service.client`, a mesma instância
`pyrogram.Client` que `TelegramCallManager` usa para participar da chamada
(única `SESSION_STRING` configurada — ver `app/telegram/call_manager.py`).
"""

from __future__ import annotations

from pyrogram import Client

from app.bot.auth import build_authorized_filter
from app.bot.handlers import info, playback, queue, status, unauthorized
from app.config.settings import Settings
from app.services.playback_service import PlaybackService


def build_bot(service: PlaybackService, settings: Settings) -> Client:
    """Registra todos os handlers de comando no cliente de `service` e o retorna."""
    app = service.client
    authorized = build_authorized_filter(settings)

    info.register(app)
    playback.register(app, service, authorized)
    queue.register(app, service, authorized)
    status.register(app, service, authorized)
    unauthorized.register(app)

    return app
