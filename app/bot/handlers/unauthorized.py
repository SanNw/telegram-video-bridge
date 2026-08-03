"""Fallback para comandos de controle chamados por usuários fora da whitelist.

Registrado num grupo de handlers posterior (`group=1`): só é alcançado quando o
handler autorizado do mesmo comando (`group=0`) não casou por falha no filtro
`authorized` — ou seja, exatamente o caso de usuário não autorizado.
"""

from __future__ import annotations

from pyrogram import Client, filters
from pyrogram.types import Message

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
