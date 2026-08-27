from __future__ import annotations

import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from pyrogram.enums import ChatMemberStatus

from app.bot.handlers import onboarding
from app.config.settings import Settings
from app.telegram.bot_api import BotAPIError
from tests.test_bot_movie_flow import _FakeBotAPI


class _Client:
    def __init__(self, status: ChatMemberStatus) -> None:
        self.status = status
        self.handler: Callable[..., Any] | None = None

    def on_message(self, _filter: Any, group: int = 0) -> Callable[[Callable[..., Any]], Any]:
        assert group == -1

        def register(handler: Callable[..., Any]) -> Callable[..., Any]:
            self.handler = handler
            return handler

        return register

    async def get_chat_member(self, _chat_id: int, _user_id: int) -> Any:
        return SimpleNamespace(status=self.status)


class _Message:
    def __init__(self, user_id: int, first_name: str = "Rafael") -> None:
        self.text = "/start"
        self.command = ["start"]
        self.from_user = SimpleNamespace(id=user_id, first_name=first_name)
        self.chat = SimpleNamespace(id=user_id)
        self.replies: list[str] = []
        self.reply_markups: list[Any] = []
        self.stopped = False

    async def reply_text(self, text: str, **kwargs: Any) -> None:
        self.replies.append(text)
        self.reply_markups.append(kwargs.get("reply_markup"))

    def stop_propagation(self) -> None:
        self.stopped = True


async def _send(client: _Client, user_id: int) -> _Message:
    assert client.handler is not None
    message = _Message(user_id)
    await client.handler(client, message)
    return message


async def test_start_always_opens_one_personalized_rich_menu(
    make_settings: Callable[..., Settings],
) -> None:
    client = _Client(ChatMemberStatus.ADMINISTRATOR)
    bot_api = _FakeBotAPI()
    onboarding.register(
        client,
        make_settings(stream_chat_id=-1001, authorized_user_ids=[]),
        bot_api=bot_api,
    )  # type: ignore[arg-type]

    first = await _send(client, 123)
    second = await _send(client, 123)

    assert first.replies == []
    assert second.replies == []
    assert first.stopped is True
    assert second.stopped is True
    assert len(bot_api.sent) == 2
    payload = json.dumps(bot_api.sent[0]["rich_message"], ensure_ascii=False)
    assert "TELERION" in payload
    assert "Sua sessão de cinema começa aqui" in payload
    assert "Rafael" in payload


async def test_non_admin_start_is_denied_and_stopped(
    make_settings: Callable[..., Settings],
) -> None:
    client = _Client(ChatMemberStatus.MEMBER)
    onboarding.register(
        client,
        make_settings(stream_chat_id=-1001, authorized_user_ids=[]),
    )  # type: ignore[arg-type]

    message = await _send(client, 456)

    assert "não é administrador" in message.replies[0]
    assert message.stopped is True


async def test_admin_rich_menu_exposes_only_premium_home_actions(
    make_settings: Callable[..., Settings],
) -> None:
    client = _Client(ChatMemberStatus.ADMINISTRATOR)
    bot_api = _FakeBotAPI()
    onboarding.register(
        client,
        make_settings(stream_chat_id=-1001, authorized_user_ids=[]),
        bot_api=bot_api,
    )  # type: ignore[arg-type]

    await _send(client, 123)

    payload = json.dumps(bot_api.sent[0]["rich_message"], ensure_ascii=False)
    for action in (
        "menu:find",
        "menu:channel",
        "menu:now",
        "menu:queue",
        "menu:controls",
        "menu:help",
    ):
        assert action in payload
    assert "menu:addons" not in payload
    assert "menu:admin" not in payload


async def test_rejected_rich_menu_falls_back_to_same_inline_actions(
    make_settings: Callable[..., Settings],
) -> None:
    class _FailingBotAPI(_FakeBotAPI):
        async def send_rich_message(self, *args: Any, **kwargs: Any) -> dict[str, object]:
            raise BotAPIError("rejected")

    client = _Client(ChatMemberStatus.ADMINISTRATOR)
    onboarding.register(
        client,
        make_settings(stream_chat_id=-1001, authorized_user_ids=[]),
        bot_api=_FailingBotAPI(),
    )  # type: ignore[arg-type]

    message = await _send(client, 123)

    assert "*" not in message.replies[0]
    assert "TELERION" in message.replies[0]
    markup = message.reply_markups[0]
    assert markup is not None
    actions = {button.callback_data for row in markup.inline_keyboard for button in row}
    assert actions == {
        "menu:find",
        "menu:channel",
        "menu:now",
        "menu:queue",
        "menu:controls",
        "menu:help",
    }
