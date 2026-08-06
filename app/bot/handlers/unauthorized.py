"""Fallback para comandos de controle chamados por usuários fora da whitelist.

Registrado num grupo de handlers posterior (`group=1`): só é alcançado quando o
handler autorizado do mesmo comando (`group=0`) não casou por falha no filtro
`authorized` — ou seja, exatamente o caso de usuário não autorizado. O mesmo
vale para os botões inline de `/find` (callback query com `data` prefixado
por `"play:"`, ver `app/bot/handlers/addons.py`): sem esse fallback, um clique
não autorizado ficaria sem resposta (spinner do botão travado).
"""

from __future__ import annotations

from typing import Any

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, Message

_PLAY_CALLBACK_PREFIX = "play:"


def _is_play_callback(_flt: Any, _client: Any, callback_query: Any) -> bool:
    data = getattr(callback_query, "data", None)
    return isinstance(data, str) and data.startswith(_PLAY_CALLBACK_PREFIX)


_CONTROLLED_COMMANDS = [
    "play",
    "pause",
    "resume",
    "stop",
    "skip",
    "queue",
    "clear",
    "status",
    "remove",
    "loop",
    "volume",
    "restart",
    "nowplaying",
    "uptime",
    "addons",
    "addon",
    "find",
    "pick",
]


def register(app: Client) -> None:
    """Registra o fallback de "não autorizado" para os comandos de controle."""

    @app.on_message(filters.command(_CONTROLLED_COMMANDS), group=1)  # type: ignore[misc]
    async def _unauthorized(_: Client, message: Message) -> None:
        await message.reply_text("Você não tem permissão para usar este comando.")

    @app.on_callback_query(filters.create(_is_play_callback), group=1)  # type: ignore[misc]
    async def _unauthorized_callback(_: Client, callback_query: CallbackQuery) -> None:
        await callback_query.answer(
            "Você não tem permissão para usar este comando.", show_alert=True
        )
