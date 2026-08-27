"""Boas-vindas privadas e painel principal do bot."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

from pyrogram import Client, filters
from pyrogram.types import Message

from app.bot.auth import is_stream_admin
from app.bot.formatting import format_main_menu, format_rich_fallback
from app.config.settings import Settings
from app.telegram.bot_api import BotAPIClient, BotAPIError
from app.utils.logging import get_logger

_logger = get_logger("bot")
_DENIED_TEXT = (
    "Você não é administrador do canal configurado. Por isso, nenhum comando de "
    "controle enviado aqui será executado."
)


def _stop(message: Message) -> None:
    cast(Callable[[], None], message.stop_propagation)()


def register(
    app: Client,
    settings: Settings,
    bot_api: BotAPIClient | None = None,
) -> None:
    """Abre o painel em `/start` e impede respostas concorrentes."""

    @app.on_message(filters.private & filters.command("start"), group=-1)  # type: ignore[misc]
    async def _onboarding(client: Client, message: Message) -> None:
        user = message.from_user
        if user is None:
            return
        if not await is_stream_admin(settings, client, user.id):
            await message.reply_text(_DENIED_TEXT)
            _stop(message)
            return

        screen = format_main_menu(getattr(user, "first_name", None) or "administrador", None)
        if bot_api is not None and message.chat is not None and message.chat.id is not None:
            try:
                await bot_api.send_rich_message(
                    message.chat.id,
                    screen,
                    receiver_user_id=user.id,
                )
                _stop(message)
                return
            except BotAPIError as exc:
                _logger.warning("Rich Message de boas-vindas recusada: {error}", error=str(exc))

        text, markup = format_rich_fallback(screen)
        await message.reply_text(text, reply_markup=markup)
        _stop(message)
