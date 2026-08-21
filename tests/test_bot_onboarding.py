from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from pyrogram.enums import ChatMemberStatus

from app.bot.handlers import onboarding
from app.config.settings import Settings


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
    def __init__(self, user_id: int) -> None:
        self.from_user = SimpleNamespace(id=user_id)
        self.replies: list[str] = []
        self.stopped = False

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)

    def stop_propagation(self) -> None:
        self.stopped = True


async def _send(client: _Client, user_id: int) -> _Message:
    assert client.handler is not None
    message = _Message(user_id)
    await client.handler(client, message)
    return message


async def test_admin_receives_guide_once_and_persists(
    tmp_path: Path, make_settings: Callable[..., Settings]
) -> None:
    path = tmp_path / "onboarding.json"
    settings = make_settings(stream_chat_id=-1001, authorized_user_ids=[])
    client = _Client(ChatMemberStatus.ADMINISTRATOR)
    onboarding.register(client, settings, path)  # type: ignore[arg-type]

    first = await _send(client, 123)
    second = await _send(client, 123)

    assert "/find" in first.replies[0]
    assert "/canal" in first.replies[0]
    assert second.replies == []
    assert json.loads(path.read_text())["admins"] == [123]


async def test_non_admin_receives_denial_once_and_command_is_stopped(
    tmp_path: Path, make_settings: Callable[..., Settings]
) -> None:
    path = tmp_path / "onboarding.json"
    settings = make_settings(stream_chat_id=-1001, authorized_user_ids=[])
    client = _Client(ChatMemberStatus.MEMBER)
    onboarding.register(client, settings, path)  # type: ignore[arg-type]

    first = await _send(client, 456)
    second = await _send(client, 456)

    assert "não é administrador" in first.replies[0]
    assert first.stopped is True
    assert second.replies == []
    assert json.loads(path.read_text())["denied"] == [456]


async def test_persisted_admin_is_not_greeted_after_restart(
    tmp_path: Path, make_settings: Callable[..., Settings]
) -> None:
    path = tmp_path / "onboarding.json"
    path.write_text('{"admins": [123], "denied": []}', encoding="utf-8")
    client = _Client(ChatMemberStatus.ADMINISTRATOR)
    onboarding.register(
        client,
        make_settings(stream_chat_id=-1001, authorized_user_ids=[]),
        path,
    )  # type: ignore[arg-type]

    assert (await _send(client, 123)).replies == []
