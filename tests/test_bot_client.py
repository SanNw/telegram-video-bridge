"""Testes de `app/bot/client.py` (wiring de handlers)."""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import MagicMock

from app.bot.client import build_bot
from app.config.settings import Settings


def test_build_bot_registers_all_command_groups(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings(authorized_user_ids=[1])
    fake_client = MagicMock()

    registered: list[object] = []

    def _on_message(filters_obj: object, group: int = 0) -> Callable[[object], object]:
        def _decorator(func: object) -> object:
            registered.append(func)
            return func

        return _decorator

    fake_client.on_message.side_effect = _on_message

    fake_service = MagicMock()
    fake_service.client = fake_client

    returned = build_bot(fake_service, settings)  # type: ignore[arg-type]

    assert returned is fake_client
    # 4 públicos (start/help/ping/version) + 5 autorizados (play/pause/resume/stop/skip)
    # + 2 fila (queue/clear) + 1 status + 1 fallback = 13 handlers registrados.
    assert len(registered) == 13


def test_build_bot_uses_service_client_not_a_new_session(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings()
    fake_client = MagicMock()
    fake_service = MagicMock()
    fake_service.client = fake_client

    build_bot(fake_service, settings)  # type: ignore[arg-type]

    assert fake_client.on_message.called
