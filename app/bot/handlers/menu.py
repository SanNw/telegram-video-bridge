"""Painel de botões para as operações cotidianas do bot."""

from __future__ import annotations

from typing import Any

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, ForceReply

from app.bot.formatting import (
    format_addons_screen,
    format_controls_screen,
    format_help_screen,
    format_help_topic,
    format_main_menu,
    format_movie_results,
    format_now_playing_screen,
    format_queue_screen,
    format_rich_fallback,
)
from app.player.models import LoopMode
from app.services.addon_service import AddonService
from app.services.channel_media_service import ChannelMediaService
from app.services.exceptions import InvalidVolumeError, NothingPlayingError
from app.services.playback_service import PlaybackService
from app.telegram.bot_api import BotAPIClient, BotAPIError


def _is_menu_callback(_flt: Any, _client: Any, callback_query: Any) -> bool:
    data = getattr(callback_query, "data", None)
    return isinstance(data, str) and data.startswith(("menu:", "control:", "help:"))


menu_callback_filter = filters.create(_is_menu_callback, "MenuCallbackFilter")


def register(
    app: Client,
    playback: PlaybackService,
    addons: AddonService,
    authorized: filters.Filter,
    owner: filters.Filter,
    bot_api: BotAPIClient,
    channel_media: ChannelMediaService | None = None,
) -> None:
    awaiting: dict[tuple[int, int], str] = {}

    def _is_awaited_text(_flt: Any, _client: Any, message: Any) -> bool:
        chat = getattr(message, "chat", None)
        user = getattr(message, "from_user", None)
        return (
            chat is not None
            and chat.id is not None
            and user is not None
            and isinstance(getattr(message, "text", None), str)
            and (chat.id, user.id) in awaiting
        )

    awaited_text_filter = filters.create(_is_awaited_text, "AwaitedMenuTextFilter")

    @app.on_callback_query(menu_callback_filter & authorized)  # type: ignore[misc]
    async def _menu(client: Client, callback_query: CallbackQuery) -> None:
        data = callback_query.data if isinstance(callback_query.data, str) else ""
        message = callback_query.message
        user = callback_query.from_user
        if message is None or message.chat is None or message.chat.id is None or user is None:
            await callback_query.answer("Não foi possível abrir o painel.", show_alert=True)
            return
        if data == "menu:admin" and not await owner(client, callback_query):
            await callback_query.answer(
                "Somente o operador pode acessar esta área.", show_alert=True
            )
            return
        if data in {"menu:find", "menu:channel"}:
            awaiting[(message.chat.id, user.id)] = data.removeprefix("menu:")
            prompt = (
                "Qual filme você quer buscar?"
                if data == "menu:find"
                else "O que procurar no canal?"
            )
            await message.reply_text(prompt, reply_markup=ForceReply(selective=True))
            await callback_query.answer()
            return

        try:
            screen = await _screen(
                data, playback, addons, getattr(user, "first_name", None) or "administrador"
            )
            await bot_api.send_rich_message(
                message.chat.id,
                screen,
                receiver_user_id=user.id,
                callback_query_id=callback_query.id,
                replace_callback_query_message=True,
            )
        except BotAPIError:
            text, markup = format_rich_fallback(screen)
            await message.edit_text(text, reply_markup=markup)
        except (NothingPlayingError, InvalidVolumeError) as exc:
            await callback_query.answer(str(exc), show_alert=True)
            return
        await callback_query.answer()

    @app.on_message(awaited_text_filter & authorized)  # type: ignore[misc]
    async def _text_reply(_: Client, message: Any) -> None:
        if message.chat is None or message.from_user is None or message.text is None:
            return
        action = awaiting.pop((message.chat.id, message.from_user.id), None)
        if action == "find":
            screen = format_movie_results(await addons.search_catalog(message.text.strip()), 0)
        elif action == "channel" and channel_media is not None:
            movies = await channel_media.search(message.text.strip())
            screen = {
                "blocks": [
                    {"type": "heading", "text": "Filmes no canal", "size": 2},
                    *(
                        {
                            "type": "buttons",
                            "buttons": [
                                {
                                    "text": movie.title,
                                    "callback_data": f"channel:{movie.message_id}",
                                    "style": "primary",
                                }
                            ],
                        }
                        for movie in movies
                    ),
                ]
            }
        else:
            screen = {"blocks": [{"type": "paragraph", "text": "Busca no canal não configurada."}]}
        await bot_api.send_rich_message(
            message.chat.id,
            screen,
            receiver_user_id=message.from_user.id,
        )


async def _screen(
    data: str,
    playback: PlaybackService,
    addons: AddonService,
    first_name: str,
) -> dict[str, object]:
    if data == "menu:home":
        return format_main_menu(first_name, None)
    if data == "menu:now":
        return format_now_playing_screen(playback.queue_snapshot())
    if data == "menu:queue":
        return format_queue_screen(playback.queue_snapshot())
    if data == "menu:addons":
        return format_addons_screen(addons.list_addons())
    if data == "menu:help":
        return format_help_screen()
    if data.startswith("help:"):
        return format_help_topic(data.removeprefix("help:"))
    if data == "menu:admin":
        return format_addons_screen(addons.list_addons())
    if data.startswith("control:"):
        action = data.split(":")
        if action[1] == "pause":
            await playback.pause()
        elif action[1] == "resume":
            await playback.resume()
        elif action[1] == "stop":
            await playback.stop_playback()
        elif action[1] == "skip":
            await playback.skip()
        elif action[1] == "restart":
            await playback.restart_current()
        elif action[1] == "loop":
            await playback.set_loop_mode(LoopMode(action[2]))
        elif action[1] == "volume":
            await playback.set_volume(int(action[2]))
    return format_controls_screen(playback.status())
