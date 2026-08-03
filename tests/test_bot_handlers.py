"""Testes de `app/bot/` — autorização e handlers de comando.

Usa um `Client` e `Message` falsos, mas o filtro real do Pyrogram
(`pyrogram.filters.command`, `AndFilter`) e o `authorized` real de
`app/bot/auth.py` — só o transporte de rede é substituído, a lógica de
roteamento/autorização é a mesma do código de produção.
"""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

from app.bot.auth import build_authorized_filter
from app.bot.handlers import info, playback, queue, status, unauthorized
from app.config.settings import Settings
from app.player.exceptions import QueueFullError
from app.player.models import LoopMode, PlaybackState, QueueItem
from app.services.exceptions import NothingPlayingError
from app.services.models import ServiceStatus
from app.streaming.models import FFmpegProcessState, HealthStatus
from app.telegram.models import CallHealth, CallState
from app.utils.sanitize import InvalidSourceError, MediaSource, SourceType


class FakeMessage:
    def __init__(self, text: str, user_id: int | None) -> None:
        self.text = text
        self.caption: str | None = None
        self.command: list[str] | None = None
        self.from_user = SimpleNamespace(id=user_id) if user_id is not None else None
        self.replies: list[str] = []
        self.edits: list[str] = []

    async def reply_text(self, text: str, **_kwargs: Any) -> FakeMessage:
        self.replies.append(text)
        return FakeMessage(text=text, user_id=None)

    async def edit_text(self, text: str, **_kwargs: Any) -> None:
        self.edits.append(text)


class FakeClient:
    def __init__(self) -> None:
        self.me = SimpleNamespace(username="testbot")
        self.handlers: dict[int, list[tuple[Any, Callable[..., Any]]]] = {}

    def on_message(
        self, filters_obj: Any, group: int = 0
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def _decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self.handlers.setdefault(group, []).append((filters_obj, func))
            return func

        return _decorator


async def dispatch(client: FakeClient, message: FakeMessage) -> bool:
    """Reproduz o roteamento do Pyrogram: primeiro handler cujo filtro casa, em ordem de grupo."""
    for group in sorted(client.handlers):
        for flt, func in client.handlers[group]:
            if await flt(client, message):
                await func(client, message)
                return True
    return False


class _FakeService:
    def __init__(self) -> None:
        self.play_calls: list[tuple[str, int]] = []
        self.play_result: int = 1
        self.play_exception: Exception | None = None
        self.pause_exception: Exception | None = None
        self.resume_exception: Exception | None = None
        self.stop_exception: Exception | None = None
        self.skip_result: QueueItem | None = None
        self.skip_exception: Exception | None = None
        self.cleared = False
        self._status = ServiceStatus(
            streaming=HealthStatus(
                state=FFmpegProcessState.RUNNING,
                pid=1,
                current_source="/media/a.mp4",
                restart_count=0,
                last_error=None,
            ),
            call=CallHealth(
                state=CallState.CONNECTED, chat_id=-1, reconnect_count=0, last_error=None
            ),
            queue_length=0,
            loop_mode=LoopMode.OFF,
            degraded=False,
            degraded_reason=None,
        )
        self._queue_snapshot = PlaybackState(items=[], current=None, loop_mode=LoopMode.OFF)

    async def play(self, source_raw: str, requested_by: int) -> int:
        if self.play_exception is not None:
            raise self.play_exception
        self.play_calls.append((source_raw, requested_by))
        return self.play_result

    async def pause(self) -> None:
        if self.pause_exception is not None:
            raise self.pause_exception

    async def resume(self) -> None:
        if self.resume_exception is not None:
            raise self.resume_exception

    async def stop_playback(self) -> None:
        if self.stop_exception is not None:
            raise self.stop_exception

    async def skip(self) -> QueueItem | None:
        if self.skip_exception is not None:
            raise self.skip_exception
        return self.skip_result

    async def clear_queue(self) -> None:
        self.cleared = True

    def queue_snapshot(self) -> PlaybackState:
        return self._queue_snapshot

    def status(self) -> ServiceStatus:
        return self._status


@pytest.fixture
def wired_client(
    make_settings: Callable[..., Settings],
) -> tuple[FakeClient, _FakeService, Settings]:
    settings = make_settings(authorized_user_ids=[111])
    client = FakeClient()
    service = _FakeService()
    authorized = build_authorized_filter(settings)

    info.register(client)  # type: ignore[arg-type]
    playback.register(client, service, authorized)  # type: ignore[arg-type]
    queue.register(client, service, authorized)  # type: ignore[arg-type]
    status.register(client, service, authorized)  # type: ignore[arg-type]
    unauthorized.register(client)  # type: ignore[arg-type]

    return client, service, settings


# --- autorização (app/bot/auth.py) ---


async def test_authorized_filter_true_for_whitelisted_user(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings(authorized_user_ids=[111])
    authorized = build_authorized_filter(settings)
    message = FakeMessage(text="/status", user_id=111)
    assert await authorized(None, message) is True


async def test_authorized_filter_false_for_other_user(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings(authorized_user_ids=[111])
    authorized = build_authorized_filter(settings)
    message = FakeMessage(text="/status", user_id=999)
    assert await authorized(None, message) is False


async def test_authorized_filter_false_when_no_from_user(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings(authorized_user_ids=[111])
    authorized = build_authorized_filter(settings)
    message = FakeMessage(text="/status", user_id=None)
    assert await authorized(None, message) is False


# --- comandos públicos ---


async def test_start_and_help_work_for_anyone(
    wired_client: tuple[FakeClient, _FakeService, Settings],
) -> None:
    client, _, _ = wired_client
    message = FakeMessage(text="/start", user_id=999)
    await dispatch(client, message)
    assert "Telegram Video Bridge" in message.replies[0]

    message = FakeMessage(text="/help", user_id=999)
    await dispatch(client, message)
    assert "Comandos" in message.replies[0]


async def test_ping_replies_with_latency(
    wired_client: tuple[FakeClient, _FakeService, Settings],
) -> None:
    client, _, _ = wired_client
    message = FakeMessage(text="/ping", user_id=999)
    await dispatch(client, message)
    sent = message.replies[0]
    assert sent == "Pong!"


async def test_version_replies_with_version_string(
    wired_client: tuple[FakeClient, _FakeService, Settings],
) -> None:
    client, _, _ = wired_client
    message = FakeMessage(text="/version", user_id=999)
    await dispatch(client, message)
    assert "telegram-video-bridge" in message.replies[0]


# --- /play ---


async def test_play_authorized_success(
    wired_client: tuple[FakeClient, _FakeService, Settings],
) -> None:
    client, service, _ = wired_client
    message = FakeMessage(text="/play movie.mp4", user_id=111)
    handled = await dispatch(client, message)
    assert handled is True
    assert service.play_calls == [("movie.mp4", 111)]
    assert "posição 1" in message.replies[0]


async def test_play_without_arguments_shows_usage(
    wired_client: tuple[FakeClient, _FakeService, Settings],
) -> None:
    client, service, _ = wired_client
    message = FakeMessage(text="/play", user_id=111)
    await dispatch(client, message)
    assert service.play_calls == []
    assert "Uso:" in message.replies[0]


async def test_play_invalid_source_replies_with_reason(
    wired_client: tuple[FakeClient, _FakeService, Settings],
) -> None:
    client, service, _ = wired_client
    service.play_exception = InvalidSourceError("Extensão não suportada.")
    message = FakeMessage(text="/play movie.exe", user_id=111)
    await dispatch(client, message)
    assert "Fonte inválida" in message.replies[0]


async def test_play_queue_full_replies_with_reason(
    wired_client: tuple[FakeClient, _FakeService, Settings],
) -> None:
    client, service, _ = wired_client
    service.play_exception = QueueFullError("Fila cheia.")
    message = FakeMessage(text="/play movie.mp4", user_id=111)
    await dispatch(client, message)
    assert "Não foi possível adicionar" in message.replies[0]


async def test_play_unauthorized_falls_back_to_denial(
    wired_client: tuple[FakeClient, _FakeService, Settings],
) -> None:
    client, service, _ = wired_client
    message = FakeMessage(text="/play movie.mp4", user_id=999)
    handled = await dispatch(client, message)
    assert handled is True
    assert service.play_calls == []
    assert "não tem permissão" in message.replies[0]


# --- /pause /resume /stop /skip ---


async def test_pause_success_and_nothing_playing(
    wired_client: tuple[FakeClient, _FakeService, Settings],
) -> None:
    client, service, _ = wired_client
    message = FakeMessage(text="/pause", user_id=111)
    await dispatch(client, message)
    assert "pausada" in message.replies[0]

    service.pause_exception = NothingPlayingError("Nada está tocando no momento.")
    message = FakeMessage(text="/pause", user_id=111)
    await dispatch(client, message)
    assert "Nada está tocando" in message.replies[0]


async def test_resume_success_and_nothing_playing(
    wired_client: tuple[FakeClient, _FakeService, Settings],
) -> None:
    client, service, _ = wired_client
    message = FakeMessage(text="/resume", user_id=111)
    await dispatch(client, message)
    assert "retomada" in message.replies[0]

    service.resume_exception = NothingPlayingError("Nada está tocando no momento.")
    message = FakeMessage(text="/resume", user_id=111)
    await dispatch(client, message)
    assert "Nada está tocando" in message.replies[0]


async def test_stop_success_and_nothing_playing(
    wired_client: tuple[FakeClient, _FakeService, Settings],
) -> None:
    client, service, _ = wired_client
    message = FakeMessage(text="/stop", user_id=111)
    await dispatch(client, message)
    assert "parada" in message.replies[0]

    service.stop_exception = NothingPlayingError("Nada está tocando no momento.")
    message = FakeMessage(text="/stop", user_id=111)
    await dispatch(client, message)
    assert "Nada está tocando" in message.replies[0]


async def test_skip_with_next_item(wired_client: tuple[FakeClient, _FakeService, Settings]) -> None:
    client, service, _ = wired_client
    service.skip_result = QueueItem(
        source=MediaSource(raw="/media/next.mp4", type=SourceType.LOCAL_FILE), requested_by=1
    )
    message = FakeMessage(text="/skip", user_id=111)
    await dispatch(client, message)
    assert "next.mp4" in message.replies[0]


async def test_skip_with_empty_queue(
    wired_client: tuple[FakeClient, _FakeService, Settings],
) -> None:
    client, service, _ = wired_client
    service.skip_result = None
    message = FakeMessage(text="/skip", user_id=111)
    await dispatch(client, message)
    assert "Fila vazia" in message.replies[0]


async def test_skip_nothing_playing(
    wired_client: tuple[FakeClient, _FakeService, Settings],
) -> None:
    client, service, _ = wired_client
    service.skip_exception = NothingPlayingError("Nada está tocando no momento.")
    message = FakeMessage(text="/skip", user_id=111)
    await dispatch(client, message)
    assert "Nada está tocando" in message.replies[0]


# --- /queue /clear /status ---


async def test_queue_formats_snapshot(
    wired_client: tuple[FakeClient, _FakeService, Settings],
) -> None:
    client, service, _ = wired_client
    message = FakeMessage(text="/queue", user_id=111)
    await dispatch(client, message)
    assert "Fila vazia." in message.replies[0]


async def test_clear_confirms_and_calls_service(
    wired_client: tuple[FakeClient, _FakeService, Settings],
) -> None:
    client, service, _ = wired_client
    message = FakeMessage(text="/clear", user_id=111)
    await dispatch(client, message)
    assert service.cleared is True
    assert "esvaziada" in message.replies[0]


async def test_status_formats_service_status(
    wired_client: tuple[FakeClient, _FakeService, Settings],
) -> None:
    client, _, _ = wired_client
    message = FakeMessage(text="/status", user_id=111)
    await dispatch(client, message)
    assert "Status" in message.replies[0]


@pytest.mark.parametrize("command", ["queue", "clear", "status", "pause", "resume", "stop", "skip"])
async def test_all_controlled_commands_deny_unauthorized_users(
    wired_client: tuple[FakeClient, _FakeService, Settings], command: str
) -> None:
    client, _, _ = wired_client
    message = FakeMessage(text=f"/{command}", user_id=999)
    await dispatch(client, message)
    assert "não tem permissão" in message.replies[0]
