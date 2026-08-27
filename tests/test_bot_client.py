"""Testes de `app/bot/client.py` (wiring de handlers)."""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import ANY, MagicMock, patch

from app.bot.client import build_bot
from app.config.settings import Settings


def _make_fake_client() -> tuple[MagicMock, list[object]]:
    fake_client = MagicMock()
    registered: list[object] = []

    def _on_message(filters_obj: object, group: int = 0) -> Callable[[object], object]:
        def _decorator(func: object) -> object:
            registered.append(func)
            return func

        return _decorator

    fake_client.on_message.side_effect = _on_message
    return fake_client, registered


def test_build_bot_registers_all_command_groups_without_bot_client(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings(authorized_user_ids=[1])
    fake_client, registered = _make_fake_client()

    fake_service = MagicMock()
    fake_service.client = fake_client
    fake_addon_service = MagicMock()
    fake_tmdb_service = MagicMock()

    returned = build_bot(fake_service, fake_addon_service, settings, fake_tmdb_service)

    assert returned is fake_client
    # 4 públicos (start/help/ping/version)
    # + 7 reprodução (play/pause/resume/stop/skip/volume/restart)
    # + 4 fila (queue/clear/remove/loop)
    # + 3 status (status/nowplaying/uptime)
    # + 4 addons (addons/addon/find/pick)
    # + 1 fallback = 23 handlers registrados, tudo no client de sessão
    #   (sem bot_client, /find e /pick caem de volta nele).
    assert len(registered) == 25


def test_build_bot_registers_search_on_bot_client_when_provided(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings(authorized_user_ids=[1])
    fake_client, session_registered = _make_fake_client()
    fake_bot_client, bot_registered = _make_fake_client()

    fake_service = MagicMock()
    fake_service.client = fake_client
    fake_addon_service = MagicMock()
    fake_tmdb_service = MagicMock()

    returned = build_bot(
        fake_service, fake_addon_service, settings, fake_tmdb_service, bot_client=fake_bot_client
    )

    assert returned is fake_client
    # Client de sessão perde find/pick (migraram pro bot_client), mas ganha
    # um segundo registro do fallback "não autorizado" (agora um por client):
    # 4 públicos + 7 reprodução + 4 fila + 3 status + 2 addons (addons/addon)
    # + 1 fallback = 21.
    assert len(session_registered) == 21
    # Client de bot recebe também o onboarding privado de primeiro contato.
    assert len(bot_registered) == 25


def test_build_bot_leaves_start_exclusively_to_bot_onboarding(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings(authorized_user_ids=[1])
    session_client, _ = _make_fake_client()
    bot_client, _ = _make_fake_client()
    service = MagicMock(client=session_client)

    with patch("app.bot.client.info.register") as register_info:
        build_bot(service, MagicMock(), settings, MagicMock(), bot_client=bot_client)

    assert register_info.call_count == 2
    assert all(call.kwargs["include_start"] is False for call in register_info.call_args_list)
    assert register_info.call_args_list[0].kwargs["include_help"] is False
    assert register_info.call_args_list[1].kwargs["include_help"] is True


def test_build_bot_uses_service_client_not_a_new_session(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings()
    fake_client = MagicMock()
    fake_service = MagicMock()
    fake_service.client = fake_client
    fake_addon_service = MagicMock()
    fake_tmdb_service = MagicMock()

    build_bot(fake_service, fake_addon_service, settings, fake_tmdb_service)

    assert fake_client.on_message.called


def test_build_bot_keeps_search_fallback_off_session_client_when_bot_is_separate(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings(authorized_user_ids=[1])
    fake_client, _ = _make_fake_client()
    fake_bot_client, _ = _make_fake_client()
    fake_service = MagicMock(client=fake_client)

    with patch("app.bot.client.unauthorized.register") as register:
        build_bot(fake_service, MagicMock(), settings, MagicMock(), fake_bot_client)

    register.assert_any_call(fake_client, ANY, search_commands=False)
    register.assert_any_call(fake_bot_client, ANY)


def test_build_bot_wires_rich_menu_and_movie_flow(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings(authorized_user_ids=[1])
    fake_client, _ = _make_fake_client()
    fake_bot_client, _ = _make_fake_client()
    fake_service = MagicMock(client=fake_client)
    bot_api = MagicMock()

    with (
        patch("app.bot.client.menu.register") as register_menu,
        patch("app.bot.client.addons.register_search") as register_search,
    ):
        build_bot(
            fake_service,
            MagicMock(),
            settings,
            MagicMock(),
            bot_client=fake_bot_client,
            bot_api=bot_api,
        )

    register_menu.assert_called_once()
    assert register_menu.call_args.args[-2] is bot_api
    assert register_search.call_args.kwargs["bot_api"] is bot_api
