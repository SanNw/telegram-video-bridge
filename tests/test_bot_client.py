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
    fake_addon_service = MagicMock()
    fake_tmdb_service = MagicMock()

    returned = build_bot(  # type: ignore[arg-type]
        fake_service, fake_addon_service, settings, fake_tmdb_service
    )

    assert returned is fake_client
    # 4 públicos (start/help/ping/version)
    # + 7 reprodução (play/pause/resume/stop/skip/volume/restart)
    # + 4 fila (queue/clear/remove/loop)
    # + 3 status (status/nowplaying/uptime)
    # + 4 addons (addons/addon/find/pick)
    # + 1 fallback = 23 handlers registrados.
    assert len(registered) == 23


def test_build_bot_uses_service_client_not_a_new_session(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings()
    fake_client = MagicMock()
    fake_service = MagicMock()
    fake_service.client = fake_client
    fake_addon_service = MagicMock()
    fake_tmdb_service = MagicMock()

    build_bot(fake_service, fake_addon_service, settings, fake_tmdb_service)  # type: ignore[arg-type]

    assert fake_client.on_message.called
