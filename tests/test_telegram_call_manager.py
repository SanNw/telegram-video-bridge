"""Testes de `app/telegram/call_manager.py`.

Pyrogram `Client` e `PyTgCalls` são substituídos por dublês de teste — nenhuma
conexão MTProto real acontece aqui. `MediaStream`/`InputDevice` (construção
pura, sem I/O) seguem sendo os objetos reais da lib.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytgcalls.exceptions import NoActiveGroupCall, NotInCallError

import app.telegram.call_manager as call_manager_module
from app.config.settings import Settings
from app.telegram.call_manager import TelegramCallManager
from app.telegram.exceptions import CallPermanentFailureError
from app.telegram.models import CallState


class _FakePyTgCalls:
    """Dublê mínimo de `pytgcalls.PyTgCalls`: registra filtros e imita a API async usada."""

    def __init__(self, client: object) -> None:
        self.client = client
        self.play = AsyncMock()
        self.leave_call = AsyncMock()
        self.pause = AsyncMock()
        self.resume = AsyncMock()
        self.change_volume_call = AsyncMock()
        self.start = AsyncMock()
        self.registered_handlers: list[tuple[object, Callable[..., object]]] = []

    def on_update(self, flt: object) -> Callable[[Callable[..., object]], Callable[..., object]]:
        def _decorator(func: Callable[..., object]) -> Callable[..., object]:
            self.registered_handlers.append((flt, func))
            return func

        return _decorator


@pytest.fixture
def make_call_manager(
    monkeypatch: pytest.MonkeyPatch, make_settings: Callable[..., Settings]
) -> Callable[..., tuple[TelegramCallManager, _FakePyTgCalls]]:
    def _make(**settings_overrides: object) -> tuple[TelegramCallManager, _FakePyTgCalls]:
        fake_client = MagicMock()
        fake_client.stop = AsyncMock()
        monkeypatch.setattr(call_manager_module, "Client", MagicMock(return_value=fake_client))
        monkeypatch.setattr(call_manager_module, "PyTgCalls", _FakePyTgCalls)

        settings = make_settings(**settings_overrides)
        manager = TelegramCallManager(settings)
        fake_call_py: _FakePyTgCalls = manager._call_py  # type: ignore[assignment]  # noqa: SLF001
        return manager, fake_call_py

    return _make


async def test_register_handlers_registers_two_filters(
    make_call_manager: Callable[..., tuple[TelegramCallManager, _FakePyTgCalls]],
) -> None:
    _, fake_call_py = make_call_manager()
    assert len(fake_call_py.registered_handlers) == 2


async def test_start_calls_pytgcalls_start_once(
    make_call_manager: Callable[..., tuple[TelegramCallManager, _FakePyTgCalls]],
) -> None:
    manager, fake_call_py = make_call_manager()
    await manager.start()
    await manager.start()  # idempotente
    assert fake_call_py.start.await_count == 1


async def test_join_call_invokes_play_with_correct_chat_id(
    make_call_manager: Callable[..., tuple[TelegramCallManager, _FakePyTgCalls]], tmp_path: Path
) -> None:
    manager, fake_call_py = make_call_manager(chat_id=-100999)
    video_pipe = tmp_path / "video.pipe"
    audio_pipe = tmp_path / "audio.pipe"

    await manager.join_call(video_pipe, audio_pipe)

    fake_call_py.play.assert_awaited_once()
    call_args = fake_call_py.play.await_args
    assert call_args.args[0] == -100999
    assert manager.healthcheck().state is CallState.CONNECTED


async def test_send_media_reuses_play(
    make_call_manager: Callable[..., tuple[TelegramCallManager, _FakePyTgCalls]], tmp_path: Path
) -> None:
    manager, fake_call_py = make_call_manager()
    await manager.join_call(tmp_path / "v1.pipe", tmp_path / "a1.pipe")
    await manager.send_media(tmp_path / "v2.pipe", tmp_path / "a2.pipe")
    assert fake_call_py.play.await_count == 2


async def test_leave_call_swallows_not_in_call_error(
    make_call_manager: Callable[..., tuple[TelegramCallManager, _FakePyTgCalls]],
) -> None:
    manager, fake_call_py = make_call_manager()
    fake_call_py.leave_call.side_effect = NotInCallError()
    await manager.leave_call()  # não deve levantar
    assert manager.healthcheck().state is CallState.DISCONNECTED


async def test_leave_call_swallows_no_active_group_call(
    make_call_manager: Callable[..., tuple[TelegramCallManager, _FakePyTgCalls]],
) -> None:
    manager, fake_call_py = make_call_manager()
    fake_call_py.leave_call.side_effect = NoActiveGroupCall()
    await manager.leave_call()


async def test_pause_and_resume_swallow_not_in_call_error(
    make_call_manager: Callable[..., tuple[TelegramCallManager, _FakePyTgCalls]],
) -> None:
    manager, fake_call_py = make_call_manager()
    fake_call_py.pause.side_effect = NotInCallError()
    fake_call_py.resume.side_effect = NotInCallError()
    await manager.pause_call()
    await manager.resume_call()


async def test_stop_leaves_call_and_stops_client(
    make_call_manager: Callable[..., tuple[TelegramCallManager, _FakePyTgCalls]],
) -> None:
    manager, fake_call_py = make_call_manager()
    fake_client = manager.client
    await manager.start()
    await manager.stop()
    fake_call_py.leave_call.assert_awaited_once()
    fake_client.stop.assert_awaited_once()  # type: ignore[attr-defined]


async def test_reconnect_without_prior_join_marks_failed_without_callback(
    make_call_manager: Callable[..., tuple[TelegramCallManager, _FakePyTgCalls]],
) -> None:
    manager, _ = make_call_manager()
    callback = AsyncMock()
    manager.set_permanent_failure_callback(callback)

    await manager.reconnect()

    assert manager.healthcheck().state is CallState.FAILED
    callback.assert_not_awaited()


async def test_reconnect_succeeds_after_transient_failures(
    make_call_manager: Callable[..., tuple[TelegramCallManager, _FakePyTgCalls]], tmp_path: Path
) -> None:
    manager, fake_call_py = make_call_manager(
        retry_base_delay_seconds=0.001,
        retry_max_delay_seconds=0.001,
        retry_max_attempts=5,
        retry_jitter_seconds=0.0,
    )
    await manager.join_call(tmp_path / "v.pipe", tmp_path / "a.pipe")

    fake_call_py.play.side_effect = [RuntimeError("falha transitória"), None]
    await manager.reconnect()

    assert manager.healthcheck().state is CallState.CONNECTED
    # 2 tentativas contadas: a que falhou + a que teve sucesso.
    assert manager.healthcheck().reconnect_count == 2


async def test_reconnect_exhausts_and_raises_permanent_failure(
    make_call_manager: Callable[..., tuple[TelegramCallManager, _FakePyTgCalls]], tmp_path: Path
) -> None:
    manager, fake_call_py = make_call_manager(
        retry_base_delay_seconds=0.001,
        retry_max_delay_seconds=0.001,
        retry_max_attempts=2,
        retry_jitter_seconds=0.0,
    )
    await manager.join_call(tmp_path / "v.pipe", tmp_path / "a.pipe")
    fake_call_py.play.side_effect = RuntimeError("sempre falha")

    callback = AsyncMock()
    manager.set_permanent_failure_callback(callback)

    with pytest.raises(CallPermanentFailureError):
        await manager.reconnect()

    assert manager.healthcheck().state is CallState.FAILED
    assert manager.healthcheck().last_error is not None
    callback.assert_awaited_once()


async def test_on_disconnected_triggers_reconnect_via_registered_handler(
    make_call_manager: Callable[..., tuple[TelegramCallManager, _FakePyTgCalls]], tmp_path: Path
) -> None:
    manager, fake_call_py = make_call_manager()
    await manager.join_call(tmp_path / "v.pipe", tmp_path / "a.pipe")
    fake_call_py.play.reset_mock()

    _, left_handler = fake_call_py.registered_handlers[0]
    await left_handler(fake_call_py, MagicMock(chat_id=manager.healthcheck().chat_id))

    fake_call_py.play.assert_awaited_once()
    assert manager.healthcheck().state is CallState.CONNECTED


async def test_on_disconnected_is_reentrant_safe(
    make_call_manager: Callable[..., tuple[TelegramCallManager, _FakePyTgCalls]], tmp_path: Path
) -> None:
    manager, fake_call_py = make_call_manager()
    await manager.join_call(tmp_path / "v.pipe", tmp_path / "a.pipe")
    manager._state = CallState.RECONNECTING  # noqa: SLF001 - simula reconexão já em andamento
    fake_call_py.play.reset_mock()

    await manager._on_disconnected(MagicMock())  # noqa: SLF001

    fake_call_py.play.assert_not_awaited()


async def test_stop_without_prior_start_is_a_noop(
    make_call_manager: Callable[..., tuple[TelegramCallManager, _FakePyTgCalls]],
) -> None:
    manager, fake_call_py = make_call_manager()
    await manager.stop()  # nunca chamou start(); não deve chamar leave_call/client.stop
    fake_call_py.leave_call.assert_not_awaited()


async def test_stream_end_handler_triggers_reconnect(
    make_call_manager: Callable[..., tuple[TelegramCallManager, _FakePyTgCalls]], tmp_path: Path
) -> None:
    from pytgcalls.types import StreamEnded

    manager, fake_call_py = make_call_manager()
    await manager.join_call(tmp_path / "v.pipe", tmp_path / "a.pipe")
    fake_call_py.play.reset_mock()

    _, stream_end_handler = fake_call_py.registered_handlers[1]
    fake_update = MagicMock(spec=StreamEnded)
    fake_update.stream_type = StreamEnded.Type.VIDEO
    fake_update.chat_id = manager.healthcheck().chat_id

    await stream_end_handler(fake_call_py, fake_update)

    fake_call_py.play.assert_awaited_once()


async def test_change_volume_invokes_change_volume_call(
    make_call_manager: Callable[..., tuple[TelegramCallManager, _FakePyTgCalls]],
) -> None:
    manager, fake_call_py = make_call_manager(chat_id=-100999)
    await manager.change_volume(150)
    fake_call_py.change_volume_call.assert_awaited_once_with(-100999, 150)


async def test_change_volume_swallows_not_in_call_error(
    make_call_manager: Callable[..., tuple[TelegramCallManager, _FakePyTgCalls]],
) -> None:
    manager, fake_call_py = make_call_manager()
    fake_call_py.change_volume_call.side_effect = NotInCallError()
    await manager.change_volume(50)  # não deve levantar


async def test_change_volume_swallows_no_active_group_call(
    make_call_manager: Callable[..., tuple[TelegramCallManager, _FakePyTgCalls]],
) -> None:
    manager, fake_call_py = make_call_manager()
    fake_call_py.change_volume_call.side_effect = NoActiveGroupCall()
    await manager.change_volume(50)  # não deve levantar
