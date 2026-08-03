"""Comandos `/status`, `/nowplaying`, `/uptime`.

Autorização: exigida (revelam detalhes operacionais: PID do FFmpeg, contagem de
reconexões, motivo de degradação, fonte em reprodução).
Erros: nenhum caminho de erro — sempre respondem com o snapshot atual.
`/nowplaying` sem reprodução ativa responde "Nada está tocando no momento."
(estado normal, não uma exceção).
Resposta: bloco de texto formatado por `format_status`/`format_now_playing`/`format_uptime`.
"""

from __future__ import annotations

from pyrogram import Client, filters
from pyrogram.types import Message

from app.bot.formatting import format_now_playing, format_status, format_uptime
from app.services.playback_service import PlaybackService


def register(app: Client, service: PlaybackService, authorized: filters.Filter) -> None:
    """Registra `/status`, `/nowplaying` e `/uptime` em `app`, restritos por `authorized`."""

    @app.on_message(filters.command("status") & authorized)  # type: ignore[misc]
    async def _status(_: Client, message: Message) -> None:
        await message.reply_text(format_status(service.status()))

    @app.on_message(filters.command("nowplaying") & authorized)  # type: ignore[misc]
    async def _now_playing(_: Client, message: Message) -> None:
        current = service.now_playing()
        if current is None:
            await message.reply_text("Nada está tocando no momento.")
            return
        item, started_at = current
        await message.reply_text(format_now_playing(item, started_at))

    @app.on_message(filters.command("uptime") & authorized)  # type: ignore[misc]
    async def _uptime(_: Client, message: Message) -> None:
        await message.reply_text(format_uptime(service.uptime()))
