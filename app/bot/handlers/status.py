"""Comando `/status`.

Autorização: exigida (revela detalhes operacionais: PID do FFmpeg, contagem de
reconexões, motivo de degradação).
Erros: nenhum caminho de erro — sempre responde com o snapshot atual.
Resposta: bloco de texto formatado por `format_status`.
"""

from __future__ import annotations

from pyrogram import Client, filters
from pyrogram.types import Message

from app.bot.formatting import format_status
from app.services.playback_service import PlaybackService


def register(app: Client, service: PlaybackService, authorized: filters.Filter) -> None:
    """Registra `/status` em `app`, restrito por `authorized`."""

    @app.on_message(filters.command("status") & authorized)  # type: ignore[misc]
    async def _status(_: Client, message: Message) -> None:
        await message.reply_text(format_status(service.status()))
