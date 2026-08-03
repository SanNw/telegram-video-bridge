"""Comandos de fila: `/queue`, `/clear`, `/remove`, `/loop`.

Autorização: exigida (revela ou altera conteúdo da fila).
Erros: `/remove` com posição não numérica ou fora da fila responde com o
motivo (`InvalidQueueIndexError`). `/loop` com modo desconhecido responde com
os valores aceitos. `/queue` e `/clear` sempre têm uma resposta válida (fila
vazia é um estado normal, não um erro).
Resposta: `/queue` lista o item atual e os pendentes (ou "Fila vazia.");
`/clear`, `/remove` e `/loop` confirmam a ação.
"""

from __future__ import annotations

from pyrogram import Client, filters
from pyrogram.types import Message

from app.bot.formatting import format_queue
from app.player.exceptions import InvalidQueueIndexError
from app.player.models import LoopMode
from app.services.playback_service import PlaybackService


def register(app: Client, service: PlaybackService, authorized: filters.Filter) -> None:
    """Registra `/queue`, `/clear`, `/remove` e `/loop` em `app`, restritos por `authorized`."""

    @app.on_message(filters.command("queue") & authorized)  # type: ignore[misc]
    async def _queue(_: Client, message: Message) -> None:
        await message.reply_text(format_queue(service.queue_snapshot()))

    @app.on_message(filters.command("clear") & authorized)  # type: ignore[misc]
    async def _clear(_: Client, message: Message) -> None:
        await service.clear_queue()
        await message.reply_text("Fila pendente esvaziada.")

    @app.on_message(filters.command("remove") & authorized)  # type: ignore[misc]
    async def _remove(_: Client, message: Message) -> None:
        if message.command is None or len(message.command) < 2:
            await message.reply_text("Uso: `/remove <posição>`")
            return
        try:
            position = int(message.command[1])
        except ValueError:
            await message.reply_text("Posição inválida: precisa ser um número.")
            return
        try:
            removed = await service.remove_from_queue(position)
        except InvalidQueueIndexError as exc:
            await message.reply_text(str(exc))
        else:
            await message.reply_text(f"Removido da fila: `{removed.source.raw}`")

    @app.on_message(filters.command("loop") & authorized)  # type: ignore[misc]
    async def _loop(_: Client, message: Message) -> None:
        if message.command is None or len(message.command) < 2:
            await message.reply_text("Uso: `/loop <off|item|queue>`")
            return
        try:
            mode = LoopMode(message.command[1].lower())
        except ValueError:
            await message.reply_text("Modo inválido. Use `off`, `item` ou `queue`.")
            return
        await service.set_loop_mode(mode)
        await message.reply_text(f"Loop definido para `{mode.value}`.")
