from __future__ import annotations

from app.bot.handlers import info
from tests.test_bot_handlers import FakeClient, FakeMessage, dispatch
from tests.test_bot_movie_flow import _FakeBotAPI


async def test_help_command_opens_the_same_rich_help_home() -> None:
    client = FakeClient()
    bot_api = _FakeBotAPI()
    info.register(client, include_start=False, bot_api=bot_api)
    message = FakeMessage("/help", 111)

    assert await dispatch(client, message)

    payload = str(bot_api.sent[0]["rich_message"])
    assert "help:movies" in payload
    assert "help:playback" in payload
    assert message.replies == []
