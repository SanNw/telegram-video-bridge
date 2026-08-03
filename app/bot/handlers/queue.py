"""Comandos de fila: `/queue`, `/clear`.

Autorização: exigida (revela conteúdo da fila).
Erros: nenhum caminho de erro — ambos sempre têm uma resposta válida (fila vazia
é um estado normal, não um erro).
Resposta: `/queue` lista o item atual e os pendentes (ou "Fila vazia.");
`/clear` confirma a limpeza.
"""

from __future__ import annotations

from pyrogram import Client, filters
from pyrogram.types import Message

from app.bot.formatting import format_queue
from app.services.playback_service import PlaybackService


def register(app: Client, service: PlaybackService, authorized: filters.Filter) -> None:
    """Registra `/queue` e `/clear` em `app`, restritos por `authorized`."""

    @app.on_message(filters.command("queue") & authorized)  # type: ignore[misc]
    async def _queue(_: Client, message: Message) -> None:
        await message.reply_text(format_queue(service.queue_snapshot()))

    @app.on_message(filters.command("clear") & authorized)  # type: ignore[misc]
    async def _clear(_: Client, message: Message) -> None:
        await service.clear_queue()
        await message.reply_text("Fila pendente esvaziada.")
