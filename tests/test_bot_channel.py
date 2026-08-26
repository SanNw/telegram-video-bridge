"""Busca e reprodução por botões de filmes publicados no canal."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from app.bot.auth import build_authorized_filter
from app.bot.handlers import channel
from app.services.channel_media_service import ChannelMovie
from tests.test_bot_handlers import (
    FakeCallbackQuery,
    FakeClient,
    FakeMessage,
    dispatch,
    dispatch_callback,
)


class _ChannelService:
    def __init__(self) -> None:
        self.movies: list[ChannelMovie] = []
        self.play_calls: list[tuple[int, int]] = []
        self.error: Exception | None = None

    async def search(self, _query: str) -> list[ChannelMovie]:
        return self.movies

    async def play(self, message_id: int, requested_by: int) -> int:
        if self.error is not None:
            raise self.error
        self.play_calls.append((message_id, requested_by))
        return 2


async def test_channel_search_renders_each_result_button(make_settings: Any) -> None:
    client = FakeClient()
    service = _ChannelService()
    service.movies = [ChannelMovie(10, "Movie A", 100), ChannelMovie(20, "Movie B", 200)]
    channel.register(
        client,
        service,  # type: ignore[arg-type]
        build_authorized_filter(make_settings(authorized_user_ids=[111])),
    )
    message = FakeMessage("/canal movie", 111)

    assert await dispatch(client, message)

    markup = message.reply_markups[-1]
    assert [row[0].callback_data for row in markup.inline_keyboard] == ["channel:10", "channel:20"]


async def test_channel_search_reports_empty_result(make_settings: Any) -> None:
    client = FakeClient()
    channel.register(
        client,
        _ChannelService(),  # type: ignore[arg-type]
        build_authorized_filter(make_settings(authorized_user_ids=[111])),
    )
    message = FakeMessage("/canal missing", 111)

    await dispatch(client, message)

    assert "Nenhum filme" in message.replies[-1]


async def test_channel_callback_plays_and_clears_buttons(make_settings: Any) -> None:
    client = FakeClient()
    client.send_message = AsyncMock()  # type: ignore[attr-defined]
    service = _ChannelService()
    channel.register(
        client,
        service,  # type: ignore[arg-type]
        build_authorized_filter(make_settings(authorized_user_ids=[111])),
    )
    callback = FakeCallbackQuery("channel:10", 111)
    callback.message = FakeMessage("", 111)
    callback.message.chat = SimpleNamespace(id=-1001)

    assert await dispatch_callback(client, callback)

    assert service.play_calls == [(10, 111)]
    assert callback.edited_reply_markup == [None]


async def test_channel_callback_reports_preparation_error(make_settings: Any) -> None:
    client = FakeClient()
    client.send_message = AsyncMock()  # type: ignore[attr-defined]
    service = _ChannelService()
    service.error = ValueError("missing")
    channel.register(
        client,
        service,  # type: ignore[arg-type]
        build_authorized_filter(make_settings(authorized_user_ids=[111])),
    )
    callback = FakeCallbackQuery("channel:10", 111)
    callback.message = FakeMessage("", 111)

    await dispatch_callback(client, callback)

    assert "missing" in client.send_message.await_args.args[1]  # type: ignore[attr-defined]
