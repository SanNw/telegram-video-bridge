"""Controles de legenda expostos pelos comandos de fallback."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from app.bot.auth import build_authorized_filter
from app.bot.handlers import playback
from app.services.exceptions import NothingPlayingError
from tests.test_bot_handlers import FakeClient, FakeMessage, _FakeService, dispatch


def _wired(make_settings: Any) -> tuple[FakeClient, _FakeService]:
    client = FakeClient()
    service = _FakeService()
    service.set_subtitle_delay = AsyncMock()  # type: ignore[attr-defined]
    service.set_subtitles_enabled = AsyncMock()  # type: ignore[attr-defined]
    playback.register(
        client,
        service,  # type: ignore[arg-type]
        build_authorized_filter(make_settings(authorized_user_ids=[111])),
    )
    return client, service


async def test_subtitle_delay_success_usage_and_invalid_number(make_settings: Any) -> None:
    client, service = _wired(make_settings)

    missing = FakeMessage("/subdelay", 111)
    invalid = FakeMessage("/subdelay nope", 111)
    valid = FakeMessage("/subdelay -750", 111)
    await dispatch(client, missing)
    await dispatch(client, invalid)
    await dispatch(client, valid)

    assert "Uso" in missing.replies[-1]
    assert "inválido" in invalid.replies[-1]
    service.set_subtitle_delay.assert_awaited_once_with(-750)  # type: ignore[attr-defined]
    assert "-750 ms" in valid.replies[-1]


async def test_subtitle_toggle_success_usage_invalid_and_nothing_playing(
    make_settings: Any,
) -> None:
    client, service = _wired(make_settings)
    missing = FakeMessage("/legenda", 111)
    invalid = FakeMessage("/legenda talvez", 111)
    enabled = FakeMessage("/legenda on", 111)
    await dispatch(client, missing)
    await dispatch(client, invalid)
    await dispatch(client, enabled)

    assert "Uso" in missing.replies[-1]
    assert "inválida" in invalid.replies[-1]
    service.set_subtitles_enabled.assert_awaited_once_with(True)  # type: ignore[attr-defined]
    assert "ativada" in enabled.replies[-1]

    service.set_subtitles_enabled.side_effect = NothingPlayingError("Nada tocando")  # type: ignore[attr-defined]
    disabled = FakeMessage("/legenda off", 111)
    await dispatch(client, disabled)
    assert disabled.replies[-1] == "Nada tocando"


async def test_subtitle_delay_reports_nothing_playing(make_settings: Any) -> None:
    client, service = _wired(make_settings)
    service.set_subtitle_delay.side_effect = NothingPlayingError("Nada tocando")  # type: ignore[attr-defined]
    message = FakeMessage("/subdelay 500", 111)

    await dispatch(client, message)

    assert message.replies[-1] == "Nada tocando"
