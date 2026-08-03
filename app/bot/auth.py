"""Autorização de comandos: whitelist de `user_id` (`Settings.authorized_user_ids`).

Comandos de controle (`/play`, `/pause`, `/resume`, `/stop`, `/skip`, `/queue`,
`/clear`, `/status`) exigem autorização. Comandos somente-leitura sem efeito
colateral (`/start`, `/help`, `/ping`, `/version`) são públicos.
"""

from __future__ import annotations

from typing import Any

from pyrogram import filters
from pyrogram.types import Message

from app.config.settings import Settings


def build_authorized_filter(settings: Settings) -> filters.Filter:
    """Filtro Pyrogram: verdadeiro se `message.from_user.id` está na whitelist."""

    async def _is_authorized(_flt: Any, _client: Any, message: Message) -> bool:
        user = message.from_user
        return user is not None and user.id in settings.authorized_user_ids

    return filters.create(_is_authorized)
