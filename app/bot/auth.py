"""Autorização de comandos: whitelist de `user_id` (`Settings.authorized_user_ids`).

Comandos de controle (`/play`, `/pause`, `/resume`, `/stop`, `/skip`, `/queue`,
`/clear`, `/status`) exigem autorização. Comandos somente-leitura sem efeito
colateral (`/start`, `/help`, `/ping`, `/version`) são públicos.

`AUTHORIZED_USER_IDS=all` autoriza qualquer membro ATUAL do grupo em
`CHAT_ID` — não qualquer usuário do Telegram. A checagem é feita em tempo
real via `get_chat_member`, então quem sai ou é banido do grupo perde acesso
imediatamente, sem precisar reiniciar o processo.

Gerenciamento de addons (enable/disable/reload/uninstall) usa um filtro à
parte, `build_owner_filter`: exige `Settings.owner_user_id` mesmo quando
`AUTHORIZED_USER_IDS=all` — instalar/remover addons executa código de
terceiro no mesmo processo que tem a `SESSION_STRING`.
"""

from __future__ import annotations

from typing import Any

from pyrogram import filters
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import RPCError
from pyrogram.types import Message

from app.config.settings import Settings

_DENIED_MEMBER_STATUSES = frozenset({ChatMemberStatus.LEFT, ChatMemberStatus.BANNED})


def build_authorized_filter(settings: Settings) -> filters.Filter:
    """Filtro Pyrogram: verdadeiro se o remetente está autorizado a controlar o bot."""

    async def _is_authorized(_flt: Any, client: Any, message: Message) -> bool:
        user = message.from_user
        if user is None:
            return False

        if settings.authorized_user_ids == "all":
            try:
                member = await client.get_chat_member(settings.chat_id, user.id)
            except RPCError:
                # Não é membro do chat (ex.: USER_NOT_PARTICIPANT) ou a checagem
                # falhou por outro motivo transitório — nega por padrão.
                return False
            return member.status not in _DENIED_MEMBER_STATUSES

        return user.id in settings.authorized_user_ids

    return filters.create(_is_authorized)


def build_owner_filter(settings: Settings) -> filters.Filter:
    """Filtro Pyrogram: verdadeiro só para `settings.owner_user_id`.

    Usado para ações de gerenciamento de addons (enable/disable/reload/
    uninstall) — instalam/desinstalam código de terceiro no mesmo processo
    que tem a `SESSION_STRING`, então não devem ficar sob
    `AUTHORIZED_USER_IDS=all` (qualquer membro do grupo). Sem `OWNER_USER_ID`
    configurado, nega a todos (fail-safe).
    """

    async def _is_owner(_flt: Any, _client: Any, message: Message) -> bool:
        user = message.from_user
        if user is None or settings.owner_user_id is None:
            return False
        return user.id == settings.owner_user_id

    return filters.create(_is_owner)
