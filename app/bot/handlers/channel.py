"""Comando `/canal` para reproduzir arquivos já publicados no canal."""

from __future__ import annotations

from typing import Any

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.player.exceptions import QueueFullError
from app.services.channel_media_service import ChannelMediaService

_PREFIX = "channel:"


def _is_channel_callback(_flt: Any, _client: Any, query: Any) -> bool:
    return isinstance(getattr(query, "data", None), str) and query.data.startswith(_PREFIX)


channel_callback_filter = filters.create(_is_channel_callback, "ChannelCallbackFilter")


def register(app: Client, service: ChannelMediaService, authorized: filters.Filter) -> None:
    @app.on_message(filters.command("canal") & authorized)  # type: ignore[misc]
    async def _channel(_: Client, message: Message) -> None:
        if message.command is None or message.text is None or len(message.command) < 2:
            await message.reply_text("Uso: `/canal <nome do filme>`")
            return
        movies = await service.search(message.text.split(maxsplit=1)[1].strip())
        if not movies:
            await message.reply_text("Nenhum filme encontrado no canal.")
            return
        buttons = [
            [InlineKeyboardButton(movie.title, callback_data=f"{_PREFIX}{movie.message_id}")]
            for movie in movies
        ]
        await message.reply_text(
            "Filmes encontrados no canal:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    @app.on_callback_query(channel_callback_filter & authorized)  # type: ignore[misc]
    async def _play_channel(client: Client, query: CallbackQuery) -> None:
        data = query.data if isinstance(query.data, str) else ""
        user_id = query.from_user.id if query.from_user else 0
        chat_id = query.message.chat.id if query.message and query.message.chat else None
        await query.answer("Preparando filme do canal...")
        try:
            position = await service.play(int(data.removeprefix(_PREFIX)), user_id)
        except (ValueError, TimeoutError, QueueFullError) as exc:
            if chat_id is not None:
                await client.send_message(chat_id, f"Não foi possível preparar: {exc}")
            return
        if chat_id is not None:
            await client.send_message(
                chat_id, f"Filme do canal adicionado à fila, posição {position}."
            )
            await query.edit_message_reply_markup(None)  # type: ignore[arg-type]
