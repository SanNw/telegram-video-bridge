"""Painel de botões e controles sem comandos slash."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from app.bot.auth import build_authorized_filter, build_owner_filter
from app.bot.handlers import menu
from app.services.tmdb_service import TMDBMovie
from app.telegram.bot_api import BotAPIError
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


async def test_private_help_button_edits_the_existing_rich_message(make_settings: Any) -> None:
    class _PrivateBotAPI(_FakeBotAPI):
        async def edit_rich_message(
            self, chat_id: int, message_id: int, rich_message: dict[str, object]
        ) -> None:
            self.edited.append(
                {"chat_id": chat_id, "message_id": message_id, "rich_message": rich_message}
            )

    settings = make_settings(authorized_user_ids=[111], owner_user_id=111)
    client = FakeClient()
    bot_api = _PrivateBotAPI()
    menu.register(
        client,
        _FakeService(),
        _FakeAddonService(),
        build_authorized_filter(settings),
        build_owner_filter(settings),
        bot_api,
    )
    callback = _callback("menu:help", 111)
    callback.message.chat = SimpleNamespace(id=111)
    callback.message.id = 9

    assert await dispatch_callback(client, callback)
    assert bot_api.sent == []
    assert bot_api.edited[0]["message_id"] == 9
    assert '"type": "buttons"' in json.dumps(bot_api.edited[0]["rich_message"])


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


async def test_local_subtitle_selection_updates_same_message(
    make_settings: Any, tmp_path: Path
) -> None:
    class _Subtitles:
        def list_local(self) -> list[Any]:
            return [SimpleNamespace(token="0", name="matrix.srt")]

        def resolve_local(self, token: str) -> Path:
            assert token == "0"
            return tmp_path / "matrix.srt"

    settings = make_settings(authorized_user_ids=[111], owner_user_id=111)
    client = FakeClient()
    playback = _FakeService()
    playback._queue_snapshot.current = SimpleNamespace(
        media_id="tt0133093",
        source=SimpleNamespace(raw="/media/matrix.mkv"),
        display_title="The Matrix",
        subtitle_path=None,
        subtitles_enabled=False,
        subtitle_delay_ms=0,
    )
    playback.set_subtitle_path = AsyncMock()  # type: ignore[attr-defined]
    bot_api = _FakeBotAPI()
    menu.register(
        client,
        playback,
        _FakeAddonService(),
        build_authorized_filter(settings),
        build_owner_filter(settings),
        bot_api,
        subtitle_service=_Subtitles(),  # type: ignore[arg-type]
    )

    await dispatch_callback(client, _callback("subtitle:local:0", 111))
    await dispatch_callback(client, _callback("subtitle:local-pick:0", 111))

    playback.set_subtitle_path.assert_awaited_once_with(  # type: ignore[attr-defined]
        str(tmp_path / "matrix.srt")
    )
    assert all(item["replace_callback_query_message"] is True for item in bot_api.sent)


async def test_online_subtitle_result_expires_when_movie_changes(
    make_settings: Any,
) -> None:
    option = SimpleNamespace(file_id=7, release="Matrix", language="pt-BR", downloads=10)
    subtitles = SimpleNamespace(
        search=AsyncMock(return_value=[option]),
        download=AsyncMock(return_value=Path("matrix.srt")),
    )
    settings = make_settings(authorized_user_ids=[111], owner_user_id=111)
    client = FakeClient()
    playback = _FakeService()
    current = SimpleNamespace(
        media_id="tt0133093",
        source=SimpleNamespace(raw="/media/matrix.mkv"),
        display_title="The Matrix",
        subtitle_path=None,
        subtitles_enabled=False,
        subtitle_delay_ms=0,
    )
    playback._queue_snapshot.current = current
    playback.set_subtitle_path = AsyncMock()  # type: ignore[attr-defined]
    menu.register(
        client,
        playback,
        _FakeAddonService(),
        build_authorized_filter(settings),
        build_owner_filter(settings),
        _FakeBotAPI(),
        subtitle_service=subtitles,  # type: ignore[arg-type]
    )
    await dispatch_callback(client, _callback("subtitle:search:0", 111))
    current.media_id = "tt0234215"
    callback = _callback("subtitle:pick:0", 111)

    await dispatch_callback(client, callback)

    assert callback.answers[-1][1] is True
    assert "expirou" in callback.answers[-1][0].lower()
    subtitles.download.assert_not_awaited()


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


async def test_private_find_falls_back_when_rich_message_is_rejected(
    make_settings: Any,
) -> None:
    class _RejectedBotAPI(_FakeBotAPI):
        async def send_rich_message(self, *args: Any, **kwargs: Any) -> dict[str, object]:
            raise BotAPIError("Bad Request: BOT_NOT_ADMIN")

    settings = make_settings(authorized_user_ids=[111], owner_user_id=111)
    client = FakeClient()
    addons = _FakeAddonService()
    addons.search_catalog = AsyncMock(  # type: ignore[attr-defined]
        return_value=[TMDBMovie(1, "The Matrix", None, None, None, 8.2, "1999-03-31")]
    )
    menu.register(
        client,
        _FakeService(),
        addons,
        build_authorized_filter(settings),
        build_owner_filter(settings),
        _RejectedBotAPI(),
    )
    callback = _callback("menu:find", 111)
    callback.message.chat = SimpleNamespace(id=111)
    await dispatch_callback(client, callback)
    reply = FakeMessage("matrix", 111)
    reply.chat = SimpleNamespace(id=111)

    assert await dispatch(client, reply)
    assert "Escolha o filme" in reply.replies[-1]
    assert reply.reply_markups[-1].inline_keyboard[0][0].callback_data == "movie:0"


async def test_remaining_control_buttons_call_service_methods(make_settings: Any) -> None:
    settings = make_settings(authorized_user_ids=[111], owner_user_id=111)
    client = FakeClient()
    playback = _FakeService()
    playback.pause = AsyncMock()  # type: ignore[method-assign]
    playback.resume = AsyncMock()  # type: ignore[method-assign]
    playback.stop_playback = AsyncMock()  # type: ignore[method-assign]
    playback.exit_and_delete = AsyncMock()  # type: ignore[method-assign]
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
        "control:exit",
        "control:skip",
        "control:loop:queue",
    ):
        await dispatch_callback(client, _callback(action, 111))

    playback.pause.assert_awaited_once()  # type: ignore[attr-defined]
    playback.resume.assert_awaited_once()  # type: ignore[attr-defined]
    playback.stop_playback.assert_awaited_once()  # type: ignore[attr-defined]
    playback.exit_and_delete.assert_awaited_once()  # type: ignore[attr-defined]
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
