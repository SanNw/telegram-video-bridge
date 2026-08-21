"""Testes de `app/bot/` — autorização e handlers de comando.

Usa um `Client` e `Message` falsos, mas o filtro real do Pyrogram
(`pyrogram.filters.command`, `AndFilter`) e o `authorized` real de
`app/bot/auth.py` — só o transporte de rede é substituído, a lógica de
roteamento/autorização é a mesma do código de produção.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.addon_system.base import AddonHealth, SearchResult, StreamCandidate
from app.addon_system.exceptions import AddonNotFoundError
from app.addon_system.manager import AddonInfo
from app.bot.auth import build_authorized_filter, build_owner_filter, build_stream_admin_filter
from app.bot.handlers import addons, info, playback, queue, status, unauthorized
from app.config.settings import Settings
from app.player.exceptions import InvalidQueueIndexError, QueueFullError
from app.player.models import LoopMode, PlaybackState, QueueItem
from app.services.exceptions import (
    InvalidSearchIndexError,
    InvalidVolumeError,
    NoStreamsAvailableError,
    NothingPlayingError,
)
from app.services.models import ServiceStatus
from app.services.tmdb_service import TMDBMetadata
from app.streaming.models import FFmpegProcessState, HealthStatus
from app.telegram.models import CallHealth, CallState
from app.utils.sanitize import InvalidSourceError, MediaSource, SourceType


class FakeMessage:
    def __init__(self, text: str, user_id: int | None) -> None:
        self.text = text
        self.caption: str | None = None
        self.command: list[str] | None = None
        self.from_user = SimpleNamespace(id=user_id) if user_id is not None else None
        self.chat = SimpleNamespace(id=-1001234567890)
        self.replies: list[str] = []
        self.reply_markups: list[Any] = []
        self.edits: list[str] = []

    async def reply_text(self, text: str, **kwargs: Any) -> FakeMessage:
        self.replies.append(text)
        self.reply_markups.append(kwargs.get("reply_markup"))
        return FakeMessage(text=text, user_id=None)

    async def edit_text(self, text: str, **_kwargs: Any) -> None:
        self.edits.append(text)

    def stop_propagation(self) -> None:
        self.propagated = False


class FakeCallbackQuery:
    def __init__(self, data: str, user_id: int | None) -> None:
        self.data = data
        self.from_user = SimpleNamespace(id=user_id) if user_id is not None else None
        self.answers: list[tuple[str, bool]] = []
        self.edited_reply_markup: list[Any] = []

    async def answer(self, text: str = "", show_alert: bool = False, **_kwargs: Any) -> None:
        self.answers.append((text, show_alert))

    async def edit_message_reply_markup(self, reply_markup: Any = None) -> None:
        self.edited_reply_markup.append(reply_markup)


class FakeClient:
    def __init__(self) -> None:
        self.me = SimpleNamespace(username="testbot")
        self.handlers: dict[int, list[tuple[Any, Callable[..., Any]]]] = {}
        self.callback_handlers: dict[int, list[tuple[Any, Callable[..., Any]]]] = {}
        self.rich_messages: list[tuple[int, Any]] = []
        self.executor = None

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        # `filters.create` (usado por `_is_play_callback`) gera um filtro síncrono;
        # `AndFilter.__call__` do Pyrogram roda esses via `client.loop.run_in_executor`.
        # Acessado só dentro de um teste `async def` (loop rodando via pytest-asyncio),
        # nunca durante `__init__` (chamado por fixtures síncronas).
        return asyncio.get_event_loop()

    def on_message(
        self, filters_obj: Any, group: int = 0
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def _decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self.handlers.setdefault(group, []).append((filters_obj, func))
            return func

        return _decorator

    def on_callback_query(
        self, filters_obj: Any, group: int = 0
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def _decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self.callback_handlers.setdefault(group, []).append((filters_obj, func))
            return func

        return _decorator

    async def send_rich_message(
        self, chat_id: int, rich_message: Any, reply_markup: Any = None, **_kwargs: Any
    ) -> None:
        self.rich_messages.append((chat_id, rich_message))
        self.last_reply_markup = reply_markup


async def dispatch(client: FakeClient, message: FakeMessage) -> bool:
    """Reproduz o roteamento do Pyrogram: primeiro handler cujo filtro casa, em ordem de grupo."""
    for group in sorted(client.handlers):
        for flt, func in client.handlers[group]:
            if await flt(client, message):
                await func(client, message)
                return True
    return False


async def _check_filter(flt: Any, client: FakeClient, update: Any) -> bool:
    """Mesma lógica de `Handler.check` do Pyrogram: filtros podem ter `__call__` sync ou async.

    `filters.create(func)` sem envolver `func` numa coroutine preserva o `__call__`
    original — no caso de `_is_play_callback` (síncrono), chamar `await flt(...)`
    direto levanta `TypeError: object bool can't be used in 'await' expression`.
    """
    import inspect

    if inspect.iscoroutinefunction(flt.__call__):
        return bool(await flt(client, update))
    return bool(flt(client, update))


async def dispatch_callback(client: FakeClient, callback_query: FakeCallbackQuery) -> bool:
    """Mesma lógica de `dispatch`, mas para callback queries."""
    for group in sorted(client.callback_handlers):
        for flt, func in client.callback_handlers[group]:
            if await _check_filter(flt, client, callback_query):
                await func(client, callback_query)
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
        self.volume_calls: list[int] = []
        self.volume_exception: Exception | None = None
        self.restart_exception: Exception | None = None
        self.restart_called = False
        self.remove_result: QueueItem | None = None
        self.remove_exception: Exception | None = None
        self.loop_calls: list[LoopMode] = []
        self.now_playing_result: tuple[QueueItem, datetime] | None = None
        self.uptime_result: timedelta = timedelta(0)
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

    async def set_volume(self, volume: int) -> None:
        if self.volume_exception is not None:
            raise self.volume_exception
        self.volume_calls.append(volume)

    async def restart_current(self) -> None:
        if self.restart_exception is not None:
            raise self.restart_exception
        self.restart_called = True

    async def remove_from_queue(self, position: int) -> QueueItem:
        if self.remove_exception is not None:
            raise self.remove_exception
        assert self.remove_result is not None
        return self.remove_result

    async def set_loop_mode(self, mode: LoopMode) -> None:
        self.loop_calls.append(mode)

    def now_playing(self) -> tuple[QueueItem, datetime] | None:
        return self.now_playing_result

    def uptime(self) -> timedelta:
        return self.uptime_result

    def queue_snapshot(self) -> PlaybackState:
        return self._queue_snapshot

    def status(self) -> ServiceStatus:
        return self._status


class _FakeAddonService:
    def __init__(self) -> None:
        self.addons_list: list[AddonInfo] = []
        self.info_result: AddonInfo | None = None
        self.info_exception: Exception | None = None
        self.health_result = AddonHealth(healthy=True)
        self.enable_calls: list[str] = []
        self.disable_calls: list[str] = []
        self.reload_calls: list[str] = []
        self.uninstall_calls: list[str] = []
        self.action_exception: Exception | None = None
        self.find_calls: list[str] = []
        self.find_result: list[SearchResult] = []
        self.pick_calls: list[tuple[int, int]] = []
        self.pick_result: int = 1
        self.pick_exception: Exception | None = None
        self.resolve_candidates_result: list[tuple[str, SearchResult, StreamCandidate]] = []
        self.pick_candidate_calls: list[tuple[str, int]] = []
        self.pick_candidate_result: tuple[str, int] = ("archive_org", 1)
        self.pick_candidate_exception: Exception | None = None
        self.last_metadata_result: TMDBMetadata | None = None

    def list_addons(self) -> list[AddonInfo]:
        return self.addons_list

    def addon_info(self, name: str) -> AddonInfo:
        if self.info_exception is not None:
            raise self.info_exception
        assert self.info_result is not None
        return self.info_result

    async def addon_health(self, name: str) -> AddonHealth:
        return self.health_result

    async def enable(self, name: str) -> None:
        if self.action_exception is not None:
            raise self.action_exception
        self.enable_calls.append(name)

    async def disable(self, name: str) -> None:
        if self.action_exception is not None:
            raise self.action_exception
        self.disable_calls.append(name)

    async def reload(self, name: str) -> None:
        if self.action_exception is not None:
            raise self.action_exception
        self.reload_calls.append(name)

    async def uninstall(self, name: str) -> None:
        if self.action_exception is not None:
            raise self.action_exception
        self.uninstall_calls.append(name)

    async def find(self, query: str) -> list[SearchResult]:
        self.find_calls.append(query)
        return self.find_result

    def last_metadata(self) -> TMDBMetadata | None:
        return self.last_metadata_result

    async def pick(self, index: int, requested_by: int) -> int:
        if self.pick_exception is not None:
            raise self.pick_exception
        self.pick_calls.append((index, requested_by))
        return self.pick_result

    async def resolve_top_candidates(self) -> list[tuple[str, SearchResult, StreamCandidate]]:
        return self.resolve_candidates_result

    async def pick_candidate(self, token: str, requested_by: int) -> tuple[str, int]:
        if self.pick_candidate_exception is not None:
            raise self.pick_candidate_exception
        self.pick_candidate_calls.append((token, requested_by))
        return self.pick_candidate_result


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


@pytest.fixture
def addon_wired_client(
    make_settings: Callable[..., Settings],
) -> tuple[FakeClient, _FakeAddonService, Settings]:
    settings = make_settings(authorized_user_ids=[111, 222], owner_user_id=111)
    client = FakeClient()
    addon_service = _FakeAddonService()
    authorized = build_authorized_filter(settings)
    owner = build_owner_filter(settings)

    addons.register_management(client, addon_service, authorized, owner)  # type: ignore[arg-type]
    addons.register_search(client, addon_service, authorized, buttons_enabled=True)  # type: ignore[arg-type]
    unauthorized.register(client)  # type: ignore[arg-type]

    return client, addon_service, settings


@pytest.fixture
def addon_wired_client_no_buttons(
    make_settings: Callable[..., Settings],
) -> tuple[FakeClient, _FakeAddonService, Settings]:
    settings = make_settings(authorized_user_ids=[111, 222], owner_user_id=111)
    client = FakeClient()
    addon_service = _FakeAddonService()
    authorized = build_authorized_filter(settings)
    owner = build_owner_filter(settings)

    addons.register_management(client, addon_service, authorized, owner)  # type: ignore[arg-type]
    addons.register_search(client, addon_service, authorized, buttons_enabled=False)  # type: ignore[arg-type]
    unauthorized.register(client)  # type: ignore[arg-type]

    return client, addon_service, settings


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


async def test_authorized_filter_true_for_stream_channel_admin(
    make_settings: Callable[..., Settings],
) -> None:
    from pyrogram.enums import ChatMemberStatus

    settings = make_settings(
        stream_chat_id=-1009876543210,
        authorized_user_ids=[],
    )
    authorized = build_authorized_filter(settings)
    client = _FakeMemberCheckClient(status=ChatMemberStatus.ADMINISTRATOR)
    message = FakeMessage(text="/status", user_id=555)

    assert await authorized(client, message) is True
    assert client.calls == [(-1009876543210, 555)]


async def test_stream_admin_filter_rejects_whitelisted_non_admin(
    make_settings: Callable[..., Settings],
) -> None:
    from pyrogram.enums import ChatMemberStatus

    settings = make_settings(stream_chat_id=-1009876543210, authorized_user_ids=[555])
    stream_admin = build_stream_admin_filter(settings)
    client = _FakeMemberCheckClient(status=ChatMemberStatus.MEMBER)

    assert await stream_admin(client, FakeMessage(text="/help", user_id=555)) is False


async def test_private_help_is_denied_to_non_admin(
    make_settings: Callable[..., Settings],
) -> None:
    from pyrogram.enums import ChatMemberStatus

    settings = make_settings(stream_chat_id=-1009876543210, authorized_user_ids=[555])
    client = FakeClient()
    client.get_chat_member = AsyncMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(status=ChatMemberStatus.MEMBER)
    )
    stream_admin = build_stream_admin_filter(settings)
    info.register(client, stream_admin)  # type: ignore[arg-type]
    unauthorized.register(client, stream_admin)  # type: ignore[arg-type]
    message = FakeMessage(text="/help", user_id=555)

    assert await dispatch(client, message) is True
    assert message.replies == ["Você não tem permissão para usar este comando."]


class _FakeMemberCheckClient:
    """Cliente falso só com `get_chat_member`, para exercitar `authorized_user_ids="all"`."""

    def __init__(self, status: Any = None, exception: Exception | None = None) -> None:
        self._status = status
        self._exception = exception
        self.calls: list[tuple[int, int]] = []

    async def get_chat_member(self, chat_id: int, user_id: int) -> Any:
        self.calls.append((chat_id, user_id))
        if self._exception is not None:
            raise self._exception
        return SimpleNamespace(status=self._status)


async def test_authorized_filter_all_true_for_current_group_member(
    make_settings: Callable[..., Settings],
) -> None:
    from pyrogram.enums import ChatMemberStatus

    settings = make_settings(authorized_user_ids="all")
    authorized = build_authorized_filter(settings)
    client = _FakeMemberCheckClient(status=ChatMemberStatus.MEMBER)
    message = FakeMessage(text="/status", user_id=555)

    assert await authorized(client, message) is True
    assert client.calls == [(settings.chat_id, 555)]


async def test_authorized_filter_all_false_for_left_member(
    make_settings: Callable[..., Settings],
) -> None:
    from pyrogram.enums import ChatMemberStatus

    settings = make_settings(authorized_user_ids="all")
    authorized = build_authorized_filter(settings)
    client = _FakeMemberCheckClient(status=ChatMemberStatus.LEFT)
    message = FakeMessage(text="/status", user_id=555)

    assert await authorized(client, message) is False


async def test_authorized_filter_all_false_for_banned_member(
    make_settings: Callable[..., Settings],
) -> None:
    from pyrogram.enums import ChatMemberStatus

    settings = make_settings(authorized_user_ids="all")
    authorized = build_authorized_filter(settings)
    client = _FakeMemberCheckClient(status=ChatMemberStatus.BANNED)
    message = FakeMessage(text="/status", user_id=555)

    assert await authorized(client, message) is False


async def test_authorized_filter_all_false_when_not_participant(
    make_settings: Callable[..., Settings],
) -> None:
    from pyrogram.errors import UserNotParticipant

    settings = make_settings(authorized_user_ids="all")
    authorized = build_authorized_filter(settings)
    client = _FakeMemberCheckClient(exception=UserNotParticipant(400))
    message = FakeMessage(text="/status", user_id=999)

    assert await authorized(client, message) is False


async def test_owner_filter_true_for_owner(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings(owner_user_id=111)
    owner = build_owner_filter(settings)
    message = FakeMessage(text="/addon enable x", user_id=111)
    assert await owner(None, message) is True


async def test_owner_filter_false_for_other_user(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings(owner_user_id=111)
    owner = build_owner_filter(settings)
    message = FakeMessage(text="/addon enable x", user_id=999)
    assert await owner(None, message) is False


async def test_owner_filter_false_when_unconfigured(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings()
    owner = build_owner_filter(settings)
    message = FakeMessage(text="/addon enable x", user_id=111)
    assert await owner(None, message) is False


async def test_owner_filter_false_when_no_from_user(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings(owner_user_id=111)
    owner = build_owner_filter(settings)
    message = FakeMessage(text="/addon enable x", user_id=None)
    assert await owner(None, message) is False


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


@pytest.mark.parametrize(
    "command",
    [
        "queue",
        "clear",
        "status",
        "pause",
        "resume",
        "stop",
        "skip",
        "volume",
        "restart",
        "remove",
        "loop",
        "nowplaying",
        "uptime",
    ],
)
async def test_all_controlled_commands_deny_unauthorized_users(
    wired_client: tuple[FakeClient, _FakeService, Settings], command: str
) -> None:
    client, _, _ = wired_client
    message = FakeMessage(text=f"/{command}", user_id=999)
    await dispatch(client, message)
    assert "não tem permissão" in message.replies[0]


# --- /volume /restart ---


async def test_volume_without_arguments_shows_usage(
    wired_client: tuple[FakeClient, _FakeService, Settings],
) -> None:
    client, service, _ = wired_client
    message = FakeMessage(text="/volume", user_id=111)
    await dispatch(client, message)
    assert service.volume_calls == []
    assert "Uso:" in message.replies[0]


async def test_volume_non_numeric_argument(
    wired_client: tuple[FakeClient, _FakeService, Settings],
) -> None:
    client, service, _ = wired_client
    message = FakeMessage(text="/volume alto", user_id=111)
    await dispatch(client, message)
    assert service.volume_calls == []
    assert "inválido" in message.replies[0]


async def test_volume_success(wired_client: tuple[FakeClient, _FakeService, Settings]) -> None:
    client, service, _ = wired_client
    message = FakeMessage(text="/volume 150", user_id=111)
    await dispatch(client, message)
    assert service.volume_calls == [150]
    assert "150" in message.replies[0]


async def test_volume_out_of_range_replies_with_reason(
    wired_client: tuple[FakeClient, _FakeService, Settings],
) -> None:
    client, service, _ = wired_client
    service.volume_exception = InvalidVolumeError("Volume deve estar entre 0 e 200.")
    message = FakeMessage(text="/volume 999", user_id=111)
    await dispatch(client, message)
    assert "entre 0 e 200" in message.replies[0]


async def test_volume_nothing_playing(
    wired_client: tuple[FakeClient, _FakeService, Settings],
) -> None:
    client, service, _ = wired_client
    service.volume_exception = NothingPlayingError("Nada está tocando no momento.")
    message = FakeMessage(text="/volume 50", user_id=111)
    await dispatch(client, message)
    assert "Nada está tocando" in message.replies[0]


async def test_restart_success(wired_client: tuple[FakeClient, _FakeService, Settings]) -> None:
    client, service, _ = wired_client
    message = FakeMessage(text="/restart", user_id=111)
    await dispatch(client, message)
    assert service.restart_called is True
    assert "reiniciado" in message.replies[0]


async def test_restart_nothing_playing(
    wired_client: tuple[FakeClient, _FakeService, Settings],
) -> None:
    client, service, _ = wired_client
    service.restart_exception = NothingPlayingError("Nada está tocando no momento.")
    message = FakeMessage(text="/restart", user_id=111)
    await dispatch(client, message)
    assert "Nada está tocando" in message.replies[0]


# --- /remove /loop ---


async def test_remove_without_arguments_shows_usage(
    wired_client: tuple[FakeClient, _FakeService, Settings],
) -> None:
    client, service, _ = wired_client
    message = FakeMessage(text="/remove", user_id=111)
    await dispatch(client, message)
    assert "Uso:" in message.replies[0]


async def test_remove_non_numeric_position(
    wired_client: tuple[FakeClient, _FakeService, Settings],
) -> None:
    client, service, _ = wired_client
    message = FakeMessage(text="/remove abc", user_id=111)
    await dispatch(client, message)
    assert "inválida" in message.replies[0]


async def test_remove_success(wired_client: tuple[FakeClient, _FakeService, Settings]) -> None:
    client, service, _ = wired_client
    service.remove_result = QueueItem(
        source=MediaSource(raw="/media/x.mp4", type=SourceType.LOCAL_FILE), requested_by=1
    )
    message = FakeMessage(text="/remove 1", user_id=111)
    await dispatch(client, message)
    assert "x.mp4" in message.replies[0]


async def test_remove_invalid_index_replies_with_reason(
    wired_client: tuple[FakeClient, _FakeService, Settings],
) -> None:
    client, service, _ = wired_client
    service.remove_exception = InvalidQueueIndexError("Posição inválida: 5.")
    message = FakeMessage(text="/remove 5", user_id=111)
    await dispatch(client, message)
    assert "Posição inválida" in message.replies[0]


async def test_loop_without_arguments_shows_usage(
    wired_client: tuple[FakeClient, _FakeService, Settings],
) -> None:
    client, service, _ = wired_client
    message = FakeMessage(text="/loop", user_id=111)
    await dispatch(client, message)
    assert service.loop_calls == []
    assert "Uso:" in message.replies[0]


async def test_loop_invalid_mode(wired_client: tuple[FakeClient, _FakeService, Settings]) -> None:
    client, service, _ = wired_client
    message = FakeMessage(text="/loop banana", user_id=111)
    await dispatch(client, message)
    assert service.loop_calls == []
    assert "inválido" in message.replies[0]


async def test_loop_success(wired_client: tuple[FakeClient, _FakeService, Settings]) -> None:
    client, service, _ = wired_client
    message = FakeMessage(text="/loop queue", user_id=111)
    await dispatch(client, message)
    assert service.loop_calls == [LoopMode.QUEUE]
    assert "queue" in message.replies[0]


# --- /nowplaying /uptime ---


async def test_now_playing_when_idle(
    wired_client: tuple[FakeClient, _FakeService, Settings],
) -> None:
    client, service, _ = wired_client
    service.now_playing_result = None
    message = FakeMessage(text="/nowplaying", user_id=111)
    await dispatch(client, message)
    assert "Nada está tocando" in message.replies[0]


async def test_now_playing_when_active(
    wired_client: tuple[FakeClient, _FakeService, Settings],
) -> None:
    client, service, _ = wired_client
    item = QueueItem(
        source=MediaSource(raw="/media/a.mp4", type=SourceType.LOCAL_FILE), requested_by=1
    )
    service.now_playing_result = (item, datetime.now(UTC) - timedelta(minutes=2))
    message = FakeMessage(text="/nowplaying", user_id=111)
    await dispatch(client, message)
    assert "a.mp4" in message.replies[0]


async def test_uptime_formats_duration(
    wired_client: tuple[FakeClient, _FakeService, Settings],
) -> None:
    client, service, _ = wired_client
    service.uptime_result = timedelta(hours=1, minutes=30)
    message = FakeMessage(text="/uptime", user_id=111)
    await dispatch(client, message)
    assert "1h" in message.replies[0]


# --- /addons /addon /find /pick ---


async def test_addons_authorized_lists_installed(
    addon_wired_client: tuple[FakeClient, _FakeAddonService, Settings],
) -> None:
    client, addon_service, _ = addon_wired_client
    addon_service.addons_list = [
        AddonInfo(name="archive_org", version="1.0.0", description="desc", enabled=True)
    ]
    message = FakeMessage(text="/addons", user_id=111)
    handled = await dispatch(client, message)
    assert handled is True
    assert "archive_org" in message.replies[0]


async def test_addons_unauthorized_is_rejected(
    addon_wired_client: tuple[FakeClient, _FakeAddonService, Settings],
) -> None:
    client, _, _ = addon_wired_client
    message = FakeMessage(text="/addons", user_id=999)
    await dispatch(client, message)
    assert "permissão" in message.replies[0]


async def test_addon_without_arguments_shows_usage(
    addon_wired_client: tuple[FakeClient, _FakeAddonService, Settings],
) -> None:
    client, _, _ = addon_wired_client
    message = FakeMessage(text="/addon", user_id=111)
    await dispatch(client, message)
    assert "Uso:" in message.replies[0]


async def test_addon_unknown_action(
    addon_wired_client: tuple[FakeClient, _FakeAddonService, Settings],
) -> None:
    client, _, _ = addon_wired_client
    message = FakeMessage(text="/addon frobnicate archive_org", user_id=111)
    await dispatch(client, message)
    assert "desconhecida" in message.replies[0]


async def test_addon_action_without_name_shows_usage(
    addon_wired_client: tuple[FakeClient, _FakeAddonService, Settings],
) -> None:
    client, _, _ = addon_wired_client
    message = FakeMessage(text="/addon enable", user_id=111)
    await dispatch(client, message)
    assert "Uso:" in message.replies[0]


async def test_addon_info_success(
    addon_wired_client: tuple[FakeClient, _FakeAddonService, Settings],
) -> None:
    client, addon_service, _ = addon_wired_client
    addon_service.info_result = AddonInfo(
        name="archive_org", version="1.0.0", description="desc", enabled=True
    )
    addon_service.health_result = AddonHealth(healthy=True)
    message = FakeMessage(text="/addon info archive_org", user_id=111)
    await dispatch(client, message)
    assert "archive_org" in message.replies[0]
    assert "saudável" in message.replies[0]


async def test_addon_info_unknown_addon(
    addon_wired_client: tuple[FakeClient, _FakeAddonService, Settings],
) -> None:
    client, addon_service, _ = addon_wired_client
    addon_service.info_exception = AddonNotFoundError("Addon não encontrado: 'nope'")
    message = FakeMessage(text="/addon info nope", user_id=111)
    await dispatch(client, message)
    assert "não encontrado" in message.replies[0]


async def test_addon_enable_success(
    addon_wired_client: tuple[FakeClient, _FakeAddonService, Settings],
) -> None:
    client, addon_service, _ = addon_wired_client
    message = FakeMessage(text="/addon enable archive_org", user_id=111)
    await dispatch(client, message)
    assert addon_service.enable_calls == ["archive_org"]
    assert "habilitado" in message.replies[0]


async def test_addon_disable_success(
    addon_wired_client: tuple[FakeClient, _FakeAddonService, Settings],
) -> None:
    client, addon_service, _ = addon_wired_client
    message = FakeMessage(text="/addon disable archive_org", user_id=111)
    await dispatch(client, message)
    assert addon_service.disable_calls == ["archive_org"]
    assert "desabilitado" in message.replies[0]


async def test_addon_reload_success(
    addon_wired_client: tuple[FakeClient, _FakeAddonService, Settings],
) -> None:
    client, addon_service, _ = addon_wired_client
    message = FakeMessage(text="/addon reload archive_org", user_id=111)
    await dispatch(client, message)
    assert addon_service.reload_calls == ["archive_org"]
    assert "recarregado" in message.replies[0]


async def test_addon_uninstall_success(
    addon_wired_client: tuple[FakeClient, _FakeAddonService, Settings],
) -> None:
    client, addon_service, _ = addon_wired_client
    message = FakeMessage(text="/addon uninstall archive_org", user_id=111)
    await dispatch(client, message)
    assert addon_service.uninstall_calls == ["archive_org"]
    assert "removido" in message.replies[0]


async def test_addon_action_error_is_reported(
    addon_wired_client: tuple[FakeClient, _FakeAddonService, Settings],
) -> None:
    client, addon_service, _ = addon_wired_client
    addon_service.action_exception = AddonNotFoundError("Addon não encontrado: 'nope'")
    message = FakeMessage(text="/addon enable nope", user_id=111)
    await dispatch(client, message)
    assert "não encontrado" in message.replies[0]


async def test_addon_info_allowed_for_authorized_non_owner(
    addon_wired_client: tuple[FakeClient, _FakeAddonService, Settings],
) -> None:
    client, addon_service, _ = addon_wired_client
    addon_service.info_result = AddonInfo(
        name="archive_org", version="1.0.0", description="desc", enabled=True
    )
    addon_service.health_result = AddonHealth(healthy=True)
    message = FakeMessage(text="/addon info archive_org", user_id=222)
    await dispatch(client, message)
    assert "archive_org" in message.replies[0]


async def test_addon_enable_rejected_for_authorized_non_owner(
    addon_wired_client: tuple[FakeClient, _FakeAddonService, Settings],
) -> None:
    client, addon_service, _ = addon_wired_client
    message = FakeMessage(text="/addon enable archive_org", user_id=222)
    await dispatch(client, message)
    assert addon_service.enable_calls == []
    assert "operador" in message.replies[0]


async def test_addon_disable_rejected_for_authorized_non_owner(
    addon_wired_client: tuple[FakeClient, _FakeAddonService, Settings],
) -> None:
    client, addon_service, _ = addon_wired_client
    message = FakeMessage(text="/addon disable archive_org", user_id=222)
    await dispatch(client, message)
    assert addon_service.disable_calls == []
    assert "operador" in message.replies[0]


async def test_addon_reload_rejected_for_authorized_non_owner(
    addon_wired_client: tuple[FakeClient, _FakeAddonService, Settings],
) -> None:
    client, addon_service, _ = addon_wired_client
    message = FakeMessage(text="/addon reload archive_org", user_id=222)
    await dispatch(client, message)
    assert addon_service.reload_calls == []
    assert "operador" in message.replies[0]


async def test_addon_uninstall_rejected_for_authorized_non_owner(
    addon_wired_client: tuple[FakeClient, _FakeAddonService, Settings],
) -> None:
    client, addon_service, _ = addon_wired_client
    message = FakeMessage(text="/addon uninstall archive_org", user_id=222)
    await dispatch(client, message)
    assert addon_service.uninstall_calls == []
    assert "operador" in message.replies[0]


async def test_addon_enable_rejected_without_owner_configured(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings(authorized_user_ids=[111])
    client = FakeClient()
    addon_service = _FakeAddonService()
    authorized = build_authorized_filter(settings)
    owner = build_owner_filter(settings)
    addons.register_management(client, addon_service, authorized, owner)  # type: ignore[arg-type]
    addons.register_search(client, addon_service, authorized, buttons_enabled=False)  # type: ignore[arg-type]
    unauthorized.register(client)  # type: ignore[arg-type]

    message = FakeMessage(text="/addon enable archive_org", user_id=111)
    await dispatch(client, message)
    assert addon_service.enable_calls == []
    assert "operador" in message.replies[0]


async def test_find_without_query_shows_usage(
    addon_wired_client: tuple[FakeClient, _FakeAddonService, Settings],
) -> None:
    client, addon_service, _ = addon_wired_client
    message = FakeMessage(text="/find", user_id=111)
    await dispatch(client, message)
    assert addon_service.find_calls == []
    assert "Uso:" in message.replies[0]


async def test_find_success(
    addon_wired_client: tuple[FakeClient, _FakeAddonService, Settings],
) -> None:
    client, addon_service, _ = addon_wired_client
    addon_service.find_result = [
        SearchResult(
            media_id="abc", title="Night of the Living Dead", year=1968, addon_name="archive_org"
        )
    ]
    message = FakeMessage(text="/find night of the living dead", user_id=111)
    await dispatch(client, message)
    assert addon_service.find_calls == ["night of the living dead"]
    assert "Night of the Living Dead" in message.replies[0]


async def test_find_no_results(
    addon_wired_client: tuple[FakeClient, _FakeAddonService, Settings],
) -> None:
    client, addon_service, _ = addon_wired_client
    addon_service.find_result = []
    message = FakeMessage(text="/find nonexistent", user_id=111)
    await dispatch(client, message)
    assert "Nenhum resultado" in message.replies[0]


async def test_find_sends_rich_message_when_tmdb_metadata_available(
    addon_wired_client: tuple[FakeClient, _FakeAddonService, Settings],
) -> None:
    client, addon_service, _ = addon_wired_client
    addon_service.last_metadata_result = TMDBMetadata(
        title="Night of the Living Dead",
        original_title="Night of the Living Dead",
        overview="Sinopse.",
        poster_url="https://image.tmdb.org/t/p/w500/poster.jpg",
        vote_average=7.8,
        genres=["Terror"],
        release_date="1968-10-01",
        cast=[],
        backdrop_urls=[],
    )
    addon_service.find_result = [
        SearchResult(
            media_id="abc", title="Night of the Living Dead", year=1968, addon_name="archive_org"
        )
    ]
    message = FakeMessage(text="/find night of the living dead", user_id=111)
    await dispatch(client, message)
    assert addon_service.find_calls == ["night of the living dead"]
    assert len(client.rich_messages) == 1
    chat_id, rich_message = client.rich_messages[0]
    assert chat_id == message.chat.id
    assert "Night of the Living Dead" in rich_message.html


async def test_find_also_sends_plain_list_when_tmdb_finds_movie(
    addon_wired_client: tuple[FakeClient, _FakeAddonService, Settings],
) -> None:
    """A Rich Message não substitui a lista de texto — as duas são enviadas."""
    client, addon_service, _ = addon_wired_client
    addon_service.last_metadata_result = TMDBMetadata(
        title="Night of the Living Dead",
        original_title="Night of the Living Dead",
        overview="Sinopse.",
        poster_url="https://image.tmdb.org/t/p/w500/poster.jpg",
        vote_average=7.8,
        genres=["Terror"],
        release_date="1968-10-01",
        cast=[],
        backdrop_urls=[],
    )
    addon_service.find_result = [
        SearchResult(
            media_id="abc", title="Night of the Living Dead", year=1968, addon_name="archive_org"
        )
    ]
    message = FakeMessage(text="/find night of the living dead", user_id=111)
    await dispatch(client, message)
    assert len(client.rich_messages) == 1
    assert len(message.replies) == 1
    assert "Night of the Living Dead" in message.replies[0]


async def test_find_no_rich_message_when_no_metadata(
    addon_wired_client: tuple[FakeClient, _FakeAddonService, Settings],
) -> None:
    client, addon_service, _ = addon_wired_client
    addon_service.last_metadata_result = None
    addon_service.find_result = [
        SearchResult(
            media_id="abc", title="Night of the Living Dead", year=1968, addon_name="archive_org"
        )
    ]
    message = FakeMessage(text="/find night of the living dead", user_id=111)
    await dispatch(client, message)
    assert client.rich_messages == []
    assert len(message.replies) == 1


async def test_find_no_rich_message_when_no_results(
    addon_wired_client: tuple[FakeClient, _FakeAddonService, Settings],
) -> None:
    client, addon_service, _ = addon_wired_client
    addon_service.last_metadata_result = TMDBMetadata(
        title="Nonexistent",
        original_title=None,
        overview=None,
        poster_url=None,
        vote_average=None,
        genres=[],
        release_date=None,
        cast=[],
        backdrop_urls=[],
    )
    addon_service.find_result = []
    message = FakeMessage(text="/find nonexistent", user_id=111)
    await dispatch(client, message)
    assert client.rich_messages == []


async def test_pick_without_arguments_shows_usage(
    addon_wired_client: tuple[FakeClient, _FakeAddonService, Settings],
) -> None:
    client, addon_service, _ = addon_wired_client
    message = FakeMessage(text="/pick", user_id=111)
    await dispatch(client, message)
    assert addon_service.pick_calls == []
    assert "Uso:" in message.replies[0]


async def test_pick_invalid_number(
    addon_wired_client: tuple[FakeClient, _FakeAddonService, Settings],
) -> None:
    client, addon_service, _ = addon_wired_client
    message = FakeMessage(text="/pick banana", user_id=111)
    await dispatch(client, message)
    assert addon_service.pick_calls == []
    assert "inválida" in message.replies[0]


async def test_pick_success(
    addon_wired_client: tuple[FakeClient, _FakeAddonService, Settings],
) -> None:
    client, addon_service, _ = addon_wired_client
    addon_service.pick_result = 3
    message = FakeMessage(text="/pick 1", user_id=111)
    await dispatch(client, message)
    assert addon_service.pick_calls == [(1, 111)]
    assert "posição 3" in message.replies[0]


async def test_pick_success_stops_propagation(
    addon_wired_client: tuple[FakeClient, _FakeAddonService, Settings],
) -> None:
    """Regressão: sem `stop_propagation()`, o fallback `_unauthorized` (group=1)
    dispararia depois e responderia "sem permissão" mesmo com /pick bem-sucedido."""
    client, addon_service, _ = addon_wired_client
    addon_service.pick_result = 1
    message = FakeMessage(text="/pick 1", user_id=111)
    await dispatch(client, message)
    assert message.propagated is False


async def test_pick_failure_stops_propagation(
    addon_wired_client: tuple[FakeClient, _FakeAddonService, Settings],
) -> None:
    client, addon_service, _ = addon_wired_client
    addon_service.pick_exception = InvalidSearchIndexError("Posição inválida: 5.")
    message = FakeMessage(text="/pick 5", user_id=111)
    await dispatch(client, message)
    assert message.propagated is False


async def test_pick_invalid_search_index(
    addon_wired_client: tuple[FakeClient, _FakeAddonService, Settings],
) -> None:
    client, addon_service, _ = addon_wired_client
    addon_service.pick_exception = InvalidSearchIndexError("Posição inválida: 5.")
    message = FakeMessage(text="/pick 5", user_id=111)
    await dispatch(client, message)
    assert "Posição inválida" in message.replies[0]


async def test_pick_no_streams_available(
    addon_wired_client: tuple[FakeClient, _FakeAddonService, Settings],
) -> None:
    client, addon_service, _ = addon_wired_client
    addon_service.pick_exception = NoStreamsAvailableError("Nenhuma fonte reproduzível encontrada.")
    message = FakeMessage(text="/pick 1", user_id=111)
    await dispatch(client, message)
    assert "Nenhuma fonte reproduzível" in message.replies[0]


async def test_pick_invalid_source(
    addon_wired_client: tuple[FakeClient, _FakeAddonService, Settings],
) -> None:
    client, addon_service, _ = addon_wired_client
    addon_service.pick_exception = InvalidSourceError("fonte inválida")
    message = FakeMessage(text="/pick 1", user_id=111)
    await dispatch(client, message)
    assert "Fonte inválida" in message.replies[0]


async def test_pick_queue_full(
    addon_wired_client: tuple[FakeClient, _FakeAddonService, Settings],
) -> None:
    client, addon_service, _ = addon_wired_client
    addon_service.pick_exception = QueueFullError("fila cheia")
    message = FakeMessage(text="/pick 1", user_id=111)
    await dispatch(client, message)
    assert "Não foi possível adicionar" in message.replies[0]


async def test_find_unauthorized_is_rejected(
    addon_wired_client: tuple[FakeClient, _FakeAddonService, Settings],
) -> None:
    client, _, _ = addon_wired_client
    message = FakeMessage(text="/find something", user_id=999)
    await dispatch(client, message)
    assert "permissão" in message.replies[0]


async def test_pick_unauthorized_is_rejected(
    addon_wired_client: tuple[FakeClient, _FakeAddonService, Settings],
) -> None:
    client, _, _ = addon_wired_client
    message = FakeMessage(text="/pick 1", user_id=999)
    await dispatch(client, message)
    assert "permissão" in message.replies[0]


async def test_addon_unauthorized_is_rejected(
    addon_wired_client: tuple[FakeClient, _FakeAddonService, Settings],
) -> None:
    client, _, _ = addon_wired_client
    message = FakeMessage(text="/addon enable archive_org", user_id=999)
    await dispatch(client, message)
    assert "permissão" in message.replies[0]


# --- /find com botões inline (callback query "play:token") ---


async def test_find_sends_reply_markup_with_one_button_per_addon(
    addon_wired_client: tuple[FakeClient, _FakeAddonService, Settings],
) -> None:
    client, addon_service, _ = addon_wired_client
    addon_service.last_metadata_result = TMDBMetadata(
        title="Night of the Living Dead",
        original_title="Night of the Living Dead",
        overview="Sinopse.",
        poster_url=None,
        vote_average=7.8,
        genres=["Terror"],
        release_date="1968-10-01",
        cast=[],
        backdrop_urls=[],
    )
    addon_service.find_result = [
        SearchResult(
            media_id="abc", title="Night of the Living Dead", year=1968, addon_name="archive_org"
        )
    ]
    addon_service.resolve_candidates_result = [
        (
            "0",
            addon_service.find_result[0],
            StreamCandidate(url="https://a.example/1.mp4", title="a"),
        ),
    ]
    message = FakeMessage(text="/find night of the living dead", user_id=111)
    await dispatch(client, message)

    assert len(client.rich_messages) == 1
    reply_markup = message.reply_markups[-1]
    assert reply_markup is not None
    assert len(reply_markup.inline_keyboard) == 1
    assert reply_markup.inline_keyboard[0][0].callback_data == "play:0"


async def test_find_no_reply_markup_when_no_candidates_resolved(
    addon_wired_client: tuple[FakeClient, _FakeAddonService, Settings],
) -> None:
    client, addon_service, _ = addon_wired_client
    addon_service.last_metadata_result = TMDBMetadata(
        title="Night of the Living Dead",
        original_title="Night of the Living Dead",
        overview="Sinopse.",
        poster_url=None,
        vote_average=7.8,
        genres=["Terror"],
        release_date="1968-10-01",
        cast=[],
        backdrop_urls=[],
    )
    addon_service.find_result = [
        SearchResult(
            media_id="abc", title="Night of the Living Dead", year=1968, addon_name="archive_org"
        )
    ]
    addon_service.resolve_candidates_result = []
    message = FakeMessage(text="/find night of the living dead", user_id=111)
    await dispatch(client, message)

    assert len(client.rich_messages) == 1
    assert message.reply_markups[-1] is None


async def test_find_no_reply_markup_when_buttons_disabled(
    addon_wired_client_no_buttons: tuple[FakeClient, _FakeAddonService, Settings],
) -> None:
    """Sem `BOT_TOKEN` (client de sessão), a Rich Message nunca carrega botão."""
    client, addon_service, _ = addon_wired_client_no_buttons
    addon_service.last_metadata_result = TMDBMetadata(
        title="Night of the Living Dead",
        original_title="Night of the Living Dead",
        overview="Sinopse.",
        poster_url=None,
        vote_average=7.8,
        genres=["Terror"],
        release_date="1968-10-01",
        cast=[],
        backdrop_urls=[],
    )
    addon_service.find_result = [
        SearchResult(
            media_id="abc", title="Night of the Living Dead", year=1968, addon_name="archive_org"
        )
    ]
    addon_service.resolve_candidates_result = [
        (
            "0",
            addon_service.find_result[0],
            StreamCandidate(url="https://a.example/1.mp4", title="a"),
        ),
    ]
    message = FakeMessage(text="/find night of the living dead", user_id=111)
    await dispatch(client, message)

    assert len(client.rich_messages) == 1
    assert message.reply_markups[-1] is None


async def test_find_sends_buttons_without_tmdb_metadata(
    addon_wired_client: tuple[FakeClient, _FakeAddonService, Settings],
) -> None:
    client, addon_service, _ = addon_wired_client
    addon_service.last_metadata_result = None
    addon_service.find_result = [
        SearchResult(media_id="abc", title="Movie", year=2024, addon_name="stremio")
    ]
    addon_service.resolve_candidates_result = [
        (
            "0",
            addon_service.find_result[0],
            StreamCandidate(url="https://example.com/movie.mp4", title="Movie"),
        )
    ]
    message = FakeMessage(text="/find movie", user_id=111)

    await dispatch(client, message)

    assert client.rich_messages == []
    assert message.reply_markups[-1].inline_keyboard[0][0].callback_data == "play:0"


async def test_play_candidate_success_answers_and_clears_markup(
    addon_wired_client: tuple[FakeClient, _FakeAddonService, Settings],
) -> None:
    client, addon_service, _ = addon_wired_client
    addon_service.pick_candidate_result = ("archive_org", 3)
    callback_query = FakeCallbackQuery(data="play:0", user_id=111)

    handled = await dispatch_callback(client, callback_query)

    assert handled is True
    assert addon_service.pick_candidate_calls == [("0", 111)]
    assert callback_query.answers[-1] == ("Adicionado à fila (archive_org), posição 3.", False)
    assert callback_query.edited_reply_markup == [None]


async def test_play_candidate_expired_token_answers_alert(
    addon_wired_client: tuple[FakeClient, _FakeAddonService, Settings],
) -> None:
    client, addon_service, _ = addon_wired_client
    addon_service.pick_candidate_exception = InvalidSearchIndexError(
        "Esse botão expirou. Use /find de novo."
    )
    callback_query = FakeCallbackQuery(data="play:0", user_id=111)

    await dispatch_callback(client, callback_query)

    assert callback_query.answers[-1] == ("Esse botão expirou. Use /find de novo.", True)
    assert callback_query.edited_reply_markup == []


async def test_play_candidate_invalid_source_answers_alert(
    addon_wired_client: tuple[FakeClient, _FakeAddonService, Settings],
) -> None:
    client, addon_service, _ = addon_wired_client
    addon_service.pick_candidate_exception = InvalidSourceError("fonte inválida")
    callback_query = FakeCallbackQuery(data="play:0", user_id=111)

    await dispatch_callback(client, callback_query)

    assert "Fonte inválida" in callback_query.answers[-1][0]
    assert callback_query.answers[-1][1] is True


async def test_play_candidate_queue_full_answers_alert(
    addon_wired_client: tuple[FakeClient, _FakeAddonService, Settings],
) -> None:
    client, addon_service, _ = addon_wired_client
    addon_service.pick_candidate_exception = QueueFullError("fila cheia")
    callback_query = FakeCallbackQuery(data="play:0", user_id=111)

    await dispatch_callback(client, callback_query)

    assert "Não foi possível adicionar" in callback_query.answers[-1][0]
    assert callback_query.answers[-1][1] is True


async def test_play_candidate_unauthorized_falls_back_to_denial(
    addon_wired_client: tuple[FakeClient, _FakeAddonService, Settings],
) -> None:
    client, addon_service, _ = addon_wired_client
    callback_query = FakeCallbackQuery(data="play:0", user_id=999)

    handled = await dispatch_callback(client, callback_query)

    assert handled is True
    assert addon_service.pick_candidate_calls == []
    assert callback_query.answers[-1] == ("Você não tem permissão para usar este comando.", True)
