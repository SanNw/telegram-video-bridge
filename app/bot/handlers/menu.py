"""Painel de botões para as operações cotidianas do bot."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, ForceReply

from app.bot.formatting import (
    format_addons_screen,
    format_help_screen,
    format_help_topic,
    format_main_menu,
    format_movie_results,
    format_now_playing_screen,
    format_playback_panel,
    format_queue_screen,
    format_rich_fallback,
    format_subtitle_options,
    format_subtitle_panel,
    format_volume_panel,
)
from app.player.models import LoopMode
from app.services.addon_service import AddonService
from app.services.channel_media_service import ChannelMediaService
from app.services.exceptions import InvalidVolumeError, NothingPlayingError, TorrentResolutionError
from app.services.playback_service import PlaybackService
from app.services.subtitle_service import SubtitleService
from app.telegram.bot_api import BotAPIClient, BotAPIError


def _is_menu_callback(_flt: Any, _client: Any, callback_query: Any) -> bool:
    data = getattr(callback_query, "data", None)
    return isinstance(data, str) and data.startswith(("menu:", "control:", "help:", "subtitle:"))


menu_callback_filter = filters.create(_is_menu_callback, "MenuCallbackFilter")


@dataclass(slots=True)
class _SubtitleState:
    media_id: str
    local: dict[str, Path] = field(default_factory=dict)
    online: list[Any] = field(default_factory=list)


def register(
    app: Client,
    playback: PlaybackService,
    addons: AddonService,
    authorized: filters.Filter,
    owner: filters.Filter,
    bot_api: BotAPIClient,
    channel_media: ChannelMediaService | None = None,
    subtitle_service: SubtitleService | None = None,
) -> None:
    awaiting: dict[tuple[int, int], str] = {}
    subtitle_states: dict[tuple[int, int], _SubtitleState] = {}

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
            if data.startswith("subtitle:"):
                current = playback.queue_snapshot().current
                if current is None or current.media_id is None:
                    raise NothingPlayingError(
                        "O filme atual não possui identificação para legendas."
                    )
                key = (message.chat.id, user.id)
                state = subtitle_states.get(key)
                if data == "subtitle:menu":
                    state = _SubtitleState(current.media_id)
                    subtitle_states[key] = state
                    screen = format_subtitle_panel(current)
                elif subtitle_service is None:
                    screen = format_subtitle_panel(current)
                elif data.startswith("subtitle:local:"):
                    entries = subtitle_service.list_local()
                    state = _SubtitleState(
                        current.media_id,
                        local={
                            entry.token: subtitle_service.resolve_local(entry.token)
                            for entry in entries
                        },
                    )
                    subtitle_states[key] = state
                    screen = format_subtitle_options(
                        "Legendas locais",
                        [entry.name for entry in entries],
                        int(data.rsplit(":", 1)[-1]),
                        "subtitle:local-pick",
                    )
                elif data.startswith("subtitle:local-pick:"):
                    token = data.rsplit(":", 1)[-1]
                    if (
                        state is None
                        or state.media_id != current.media_id
                        or token not in state.local
                    ):
                        await callback_query.answer("Essa opção expirou.", show_alert=True)
                        return
                    await playback.set_subtitle_path(str(state.local[token]))
                    screen = format_subtitle_panel(playback.queue_snapshot().current or current)
                elif data.startswith("subtitle:search:"):
                    options = await subtitle_service.search(current.media_id)
                    state = _SubtitleState(current.media_id, online=list(options))
                    subtitle_states[key] = state
                    screen = format_subtitle_options(
                        "OpenSubtitles",
                        [f"{item.language} · {item.release}" for item in options],
                        int(data.rsplit(":", 1)[-1]),
                        "subtitle:pick",
                    )
                elif data.startswith("subtitle:pick:"):
                    token = data.rsplit(":", 1)[-1]
                    if (
                        state is None
                        or state.media_id != current.media_id
                        or not token.isdigit()
                        or int(token) >= len(state.online)
                    ):
                        await callback_query.answer("Essa opção expirou.", show_alert=True)
                        return
                    path = await subtitle_service.download(
                        state.online[int(token)], current.media_id
                    )
                    await playback.set_subtitle_path(str(path))
                    screen = format_subtitle_panel(playback.queue_snapshot().current or current)
                elif data == "subtitle:toggle":
                    await playback.set_subtitles_enabled(not current.subtitles_enabled)
                    screen = format_subtitle_panel(playback.queue_snapshot().current or current)
                elif data == "subtitle:delay":
                    screen = _subtitle_delay_screen()
                elif data.startswith("subtitle:delay:"):
                    await playback.set_subtitle_delay(int(data.rsplit(":", 1)[-1]))
                    screen = format_subtitle_panel(playback.queue_snapshot().current or current)
                else:
                    screen = format_subtitle_panel(current)
            else:
                screen = await _screen(
                    data, playback, addons, getattr(user, "first_name", None) or "administrador"
                )
            if message.chat.id > 0 and isinstance(getattr(message, "id", None), int):
                await bot_api.edit_rich_message(message.chat.id, message.id, screen)
            else:
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
        except (NothingPlayingError, InvalidVolumeError, TorrentResolutionError) as exc:
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
        try:
            await bot_api.send_rich_message(
                message.chat.id,
                screen,
                receiver_user_id=message.from_user.id,
            )
        except BotAPIError:
            text, markup = format_rich_fallback(screen)
            await message.reply_text(text, reply_markup=markup)


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
    if data == "menu:controls":
        return format_playback_panel(playback.status(), playback.queue_snapshot())
    if data == "menu:volume":
        return format_volume_panel(playback.status())
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
        elif action[1] == "exit":
            await playback.exit_and_delete()
        elif action[1] == "skip":
            await playback.skip()
        elif action[1] == "restart":
            await playback.restart_current()
        elif action[1] == "loop":
            await playback.set_loop_mode(LoopMode(action[2]))
        elif action[1] == "volume":
            await playback.set_volume(int(action[2]))
    return format_playback_panel(playback.status(), playback.queue_snapshot())


def _subtitle_delay_screen() -> dict[str, object]:
    return {
        "blocks": [
            {"type": "heading", "text": "Sincronia", "size": 2},
            {"type": "paragraph", "text": "Negativo adianta; positivo atrasa."},
            {
                "type": "buttons",
                "align": "left",
                "buttons": [
                    {"text": "-1s", "style": "primary", "callback_data": "subtitle:delay:-1000"},
                    {"text": "-0.5s", "style": "primary", "callback_data": "subtitle:delay:-500"},
                    {"text": "+0.5s", "style": "primary", "callback_data": "subtitle:delay:500"},
                    {"text": "+1s", "style": "primary", "callback_data": "subtitle:delay:1000"},
                ],
            },
            {
                "type": "buttons",
                "align": "left",
                "buttons": [
                    {"text": "Voltar", "style": "primary", "callback_data": "subtitle:menu"}
                ],
            },
        ]
    }
