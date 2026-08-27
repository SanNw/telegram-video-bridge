"""Painel de botões e controles sem comandos slash."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from app.bot.auth import build_authorized_filter, build_owner_filter
from app.bot.handlers import menu
from app.services.tmdb_service import TMDBMovie
from tests.test_bot_handlers import (
    FakeCallbackQuery,
    FakeClient,
    FakeMessage,
    _FakeAddonService,
    _FakeService,
    dispatch,
    dispatch_callback,
)
from tests.test_bot_movie_flow import _FakeBotAPI


def _callback(data: str, user_id: int) -> FakeCallbackQuery:
    callback = FakeCallbackQuery(data, user_id)
    callback.id = f"cb-{user_id}"
    callback.message = FakeMessage("", user_id)
    callback.message.chat = SimpleNamespace(id=-1001)
    return callback


async def test_main_menu_and_dashboard_actions_render(make_settings: Any) -> None:
    settings = make_settings(authorized_user_ids=[111], owner_user_id=111)
    client = FakeClient()
    playback = _FakeService()
    addons = _FakeAddonService()
    bot_api = _FakeBotAPI()
    menu.register(
        client,
        playback,
        addons,
        build_authorized_filter(settings),
        build_owner_filter(settings),
        bot_api,
    )

    for action in ("menu:home", "menu:now", "menu:queue", "menu:controls", "menu:addons"):
        assert await dispatch_callback(client, _callback(action, 111))

    assert len(bot_api.sent) == 5


async def test_help_topics_replace_the_existing_message(make_settings: Any) -> None:
    settings = make_settings(authorized_user_ids=[111], owner_user_id=111)
    client = FakeClient()
    bot_api = _FakeBotAPI()
    menu.register(
        client,
        _FakeService(),
        _FakeAddonService(),
        build_authorized_filter(settings),
        build_owner_filter(settings),
        bot_api,
    )

    for action in ("menu:help", "help:movies", "help:playback"):
        assert await dispatch_callback(client, _callback(action, 111))

    assert len(bot_api.sent) == 3
    assert all(item["replace_callback_query_message"] is True for item in bot_api.sent)


async def test_control_buttons_call_existing_playback_methods(make_settings: Any) -> None:
    settings = make_settings(authorized_user_ids=[111], owner_user_id=111)
    client = FakeClient()
    playback = _FakeService()
    bot_api = _FakeBotAPI()
    menu.register(
        client,
        playback,
        _FakeAddonService(),
        build_authorized_filter(settings),
        build_owner_filter(settings),
        bot_api,
    )

    await dispatch_callback(client, _callback("control:volume:150", 111))
    await dispatch_callback(client, _callback("control:restart", 111))

    assert playback.volume_calls == [150]
    assert playback.restart_called is True


async def test_admin_menu_rejects_non_owner(make_settings: Any) -> None:
    settings = make_settings(authorized_user_ids=[111, 222], owner_user_id=111)
    client = FakeClient()
    bot_api = _FakeBotAPI()
    menu.register(
        client,
        _FakeService(),
        _FakeAddonService(),
        build_authorized_filter(settings),
        build_owner_filter(settings),
        bot_api,
    )
    callback = _callback("menu:admin", 222)

    await dispatch_callback(client, callback)

    assert callback.answers[-1][1] is True
    assert bot_api.sent == []


async def test_find_button_consumes_next_text_without_slash_command(make_settings: Any) -> None:
    settings = make_settings(authorized_user_ids=[111], owner_user_id=111)
    client = FakeClient()
    addons = _FakeAddonService()
    addons.search_catalog = AsyncMock(  # type: ignore[attr-defined]
        return_value=[TMDBMovie(1, "The Matrix", None, None, None, 8.2, "1999-03-31")]
    )
    bot_api = _FakeBotAPI()
    menu.register(
        client,
        _FakeService(),
        addons,
        build_authorized_filter(settings),
        build_owner_filter(settings),
        bot_api,
    )
    await dispatch_callback(client, _callback("menu:find", 111))
    reply = FakeMessage("The Matrix", 111)
    reply.chat = SimpleNamespace(id=-1001)

    assert await dispatch(client, reply)

    addons.search_catalog.assert_awaited_once_with("The Matrix")  # type: ignore[attr-defined]
    assert "movie:0" in str(bot_api.sent[-1]["rich_message"])


async def test_remaining_control_buttons_call_service_methods(make_settings: Any) -> None:
    settings = make_settings(authorized_user_ids=[111], owner_user_id=111)
    client = FakeClient()
    playback = _FakeService()
    playback.pause = AsyncMock()  # type: ignore[method-assign]
    playback.resume = AsyncMock()  # type: ignore[method-assign]
    playback.stop_playback = AsyncMock()  # type: ignore[method-assign]
    playback.skip = AsyncMock(return_value=None)  # type: ignore[method-assign]
    bot_api = _FakeBotAPI()
    menu.register(
        client,
        playback,
        _FakeAddonService(),
        build_authorized_filter(settings),
        build_owner_filter(settings),
        bot_api,
    )

    for action in (
        "control:pause",
        "control:resume",
        "control:stop",
        "control:skip",
        "control:loop:queue",
    ):
        await dispatch_callback(client, _callback(action, 111))

    playback.pause.assert_awaited_once()  # type: ignore[attr-defined]
    playback.resume.assert_awaited_once()  # type: ignore[attr-defined]
    playback.stop_playback.assert_awaited_once()  # type: ignore[attr-defined]
    playback.skip.assert_awaited_once()  # type: ignore[attr-defined]
    assert playback.loop_calls


async def test_channel_prompt_consumes_text_and_renders_results(make_settings: Any) -> None:
    class _Channel:
        async def search(self, query: str) -> list[Any]:
            assert query == "matrix"
            return [SimpleNamespace(message_id=9, title="Matrix 1080p")]

    settings = make_settings(authorized_user_ids=[111], owner_user_id=111)
    client = FakeClient()
    bot_api = _FakeBotAPI()
    menu.register(
        client,
        _FakeService(),
        _FakeAddonService(),
        build_authorized_filter(settings),
        build_owner_filter(settings),
        bot_api,
        _Channel(),  # type: ignore[arg-type]
    )
    await dispatch_callback(client, _callback("menu:channel", 111))
    reply = FakeMessage("matrix", 111)
    reply.chat = SimpleNamespace(id=-1001)

    await dispatch(client, reply)

    assert "channel:9" in str(bot_api.sent[-1]["rich_message"])
