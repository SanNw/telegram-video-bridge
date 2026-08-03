"""Comandos de reprodução: `/play`, `/pause`, `/resume`, `/stop`, `/skip`,
`/volume`, `/restart`.

Autorização: exigida (whitelist de `user_id`); usuário fora da whitelist recebe
a resposta padrão de `handlers/unauthorized.py`.

Erros: `/play` com fonte inválida responde com o motivo (`InvalidSourceError`);
com fila cheia, responde com o limite atingido (`QueueFullError`). `/volume`
fora de 0-200 responde com o motivo (`InvalidVolumeError`). Os demais
comandos, chamados sem reprodução ativa, respondem "Nada está tocando no momento."
(`NothingPlayingError`). Nenhum erro chega a derrubar o processo — todos são
capturados aqui e viram uma resposta ao usuário.

Resposta: sempre uma única mensagem de texto confirmando a ação ou explicando o erro.
"""

from __future__ import annotations

from pyrogram import Client, filters
from pyrogram.types import Message

from app.player.exceptions import QueueFullError
from app.services.exceptions import InvalidVolumeError, NothingPlayingError
from app.services.playback_service import PlaybackService
from app.utils.logging import get_logger
from app.utils.sanitize import InvalidSourceError

_logger = get_logger("bot")


def register(app: Client, service: PlaybackService, authorized: filters.Filter) -> None:
    """Registra os comandos de reprodução em `app`, restritos por `authorized`."""

    @app.on_message(filters.command("play") & authorized)  # type: ignore[misc]
    async def _play(_: Client, message: Message) -> None:
        # `filters.command` já garante message.command/message.text populados
        # em runtime; a checagem abaixo só satisfaz o tipo Optional do stub.
        if message.command is None or message.text is None or len(message.command) < 2:
            await message.reply_text("Uso: `/play <arquivo local ou URL>`")
            return
        source_raw = message.text.split(maxsplit=1)[1].strip()
        user_id = message.from_user.id if message.from_user else 0
        try:
            position = await service.play(source_raw, user_id)
        except InvalidSourceError as exc:
            await message.reply_text(f"Fonte inválida: {exc}")
        except QueueFullError as exc:
            await message.reply_text(f"Não foi possível adicionar: {exc}")
        else:
            await message.reply_text(f"Adicionado à fila na posição {position}.")

    @app.on_message(filters.command("pause") & authorized)  # type: ignore[misc]
    async def _pause(_: Client, message: Message) -> None:
        try:
            await service.pause()
        except NothingPlayingError as exc:
            await message.reply_text(str(exc))
        else:
            await message.reply_text("Chamada pausada.")

    @app.on_message(filters.command("resume") & authorized)  # type: ignore[misc]
    async def _resume(_: Client, message: Message) -> None:
        try:
            await service.resume()
        except NothingPlayingError as exc:
            await message.reply_text(str(exc))
        else:
            await message.reply_text("Chamada retomada.")

    @app.on_message(filters.command("stop") & authorized)  # type: ignore[misc]
    async def _stop(_: Client, message: Message) -> None:
        try:
            await service.stop_playback()
        except NothingPlayingError as exc:
            await message.reply_text(str(exc))
        else:
            await message.reply_text("Reprodução parada e chamada encerrada.")

    @app.on_message(filters.command("skip") & authorized)  # type: ignore[misc]
    async def _skip(_: Client, message: Message) -> None:
        try:
            next_item = await service.skip()
        except NothingPlayingError as exc:
            await message.reply_text(str(exc))
        else:
            if next_item is None:
                await message.reply_text("Fila vazia; reprodução encerrada.")
            else:
                await message.reply_text(f"Tocando agora: `{next_item.source.raw}`")

    @app.on_message(filters.command("volume") & authorized)  # type: ignore[misc]
    async def _volume(_: Client, message: Message) -> None:
        if message.command is None or len(message.command) < 2:
            await message.reply_text("Uso: `/volume <0-200>`")
            return
        try:
            volume = int(message.command[1])
        except ValueError:
            await message.reply_text("Volume inválido: precisa ser um número.")
            return
        try:
            await service.set_volume(volume)
        except (NothingPlayingError, InvalidVolumeError) as exc:
            await message.reply_text(str(exc))
        else:
            await message.reply_text(f"Volume ajustado para {volume}.")

    @app.on_message(filters.command("restart") & authorized)  # type: ignore[misc]
    async def _restart(_: Client, message: Message) -> None:
        try:
            await service.restart_current()
        except NothingPlayingError as exc:
            await message.reply_text(str(exc))
        else:
            await message.reply_text("Item atual reiniciado.")
