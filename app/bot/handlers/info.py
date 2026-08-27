"""Comandos informativos: `/start`, `/help`, `/ping`, `/version`.

Autorização: públicos (sem efeito colateral, não expõem dados operacionais).
Erros: nenhum caminho de erro — respostas são texto estático ou latência medida.
Resposta: painel rico com fallback para texto e botões inline tradicionais.
"""

from __future__ import annotations

import time
from typing import cast

from pyrogram import Client, filters
from pyrogram.types import Message

from app import __version__
from app.bot.formatting import format_help_screen, format_rich_fallback
from app.telegram.bot_api import BotAPIClient, BotAPIError

_START_TEXT = (
    "TELERION\n\nSua sessão de cinema começa aqui.\n\n" "Use /help para abrir a central de ajuda."
)


def register(
    app: Client,
    authorized: filters.Filter | None = None,
    *,
    include_start: bool = True,
    include_help: bool = True,
    bot_api: BotAPIClient | None = None,
) -> None:
    """Registra `/start`, `/help`, `/ping` e `/version` em `app`."""

    def command(name: str) -> filters.Filter:
        command_filter = filters.command(name)
        return (
            command_filter
            if authorized is None
            else cast(filters.Filter, command_filter & authorized)
        )

    if include_start:

        @app.on_message(command("start"))  # type: ignore[misc]
        async def _start(_: Client, message: Message) -> None:
            await message.reply_text(_START_TEXT)

    if include_help:

        @app.on_message(command("help"))  # type: ignore[misc]
        async def _help(_: Client, message: Message) -> None:
            screen = format_help_screen()
            if bot_api is not None and message.chat is not None and message.chat.id is not None:
                try:
                    await bot_api.send_rich_message(
                        message.chat.id,
                        screen,
                        receiver_user_id=message.from_user.id if message.from_user else None,
                    )
                    return
                except BotAPIError:
                    pass
            text, markup = format_rich_fallback(screen)
            await message.reply_text(text, reply_markup=markup)

    @app.on_message(command("ping"))  # type: ignore[misc]
    async def _ping(_: Client, message: Message) -> None:
        started = time.monotonic()
        sent = await message.reply_text("Pong!")
        elapsed_ms = (time.monotonic() - started) * 1000
        if sent is not None:
            await sent.edit_text(f"Pong! `{elapsed_ms:.0f}ms`")

    @app.on_message(command("version"))  # type: ignore[misc]
    async def _version(_: Client, message: Message) -> None:
        await message.reply_text(f"telegram-video-bridge v{__version__}")
