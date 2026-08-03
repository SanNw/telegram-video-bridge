"""Testes de `app/services/playback_service.py`.

`QueueManager`, `FFmpegStreamer` e `TelegramCallManager` são substituídos por
dublês leves — o objetivo aqui é testar a orquestração (quem chama o quê, em
que ordem, e como reage a conclusão/falha), não redundar com os testes de
cada camada individual (já cobertos em outros arquivos).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import app.services.playback_service as service_module
from app.config.settings import Settings
from app.player.models import LoopMode, PlaybackState, QueueItem
from app.services.exceptions import NothingPlayingError
from app.services.playback_service import PlaybackService
from app.streaming.models import FFmpegProcessState, HealthStatus
from app.telegram.models import CallHealth, CallState
from app.utils.sanitize import MediaSource, SourceType


class _FakeQueueManager:
    def __init__(self, settings: Settings) -> None:
        self.items: list[QueueItem] = []
        self.current: QueueItem | None = None
        self.loop_mode = LoopMode.OFF
        self.loaded = False

    async def load(self) -> None:
        self.loaded = True

    async def add(self, item: QueueItem) -> int:
        self.items.append(item)
        return len(self.items)

    async def advance(self) -> QueueItem | None:
        if not self.items:
            self.current = None
            return None
        self.current = self.items.pop(0)
        return self.current

    async def skip(self) -> QueueItem | None:
        return await self.advance()

    async def clear(self) -> None:
        self.items = []

    def snapshot(self) -> PlaybackState:
        return PlaybackState(items=list(self.items), current=self.current, loop_mode=self.loop_mode)


class _FakeStreamer:
    def __init__(self, settings: Settings) -> None:
        self.video_pipe_path = Path("/fake/video.pipe")
        self.audio_pipe_path = Path("/fake/audio.pipe")
        self.started_sources: list[MediaSource] = []
        self.stopped = False
        self.state = FFmpegProcessState.IDLE
        self._on_completion: Callable[[], object] | None = None
        self._on_permanent_failure: Callable[[], object] | None = None

    def set_completion_callback(self, callback: Callable[[], object]) -> None:
        self._on_completion = callback

    def set_permanent_failure_callback(self, callback: Callable[[], object]) -> None:
        self._on_permanent_failure = callback

    async def change_source(self, source: MediaSource) -> None:
        self.started_sources.append(source)
        self.stopped = False
        self.state = FFmpegProcessState.RUNNING

    async def stop(self) -> None:
        self.stopped = True
        self.state = FFmpegProcessState.STOPPED

    def healthcheck(self) -> HealthStatus:
        return HealthStatus(
            state=self.state, pid=1234, current_source=None, restart_count=0, last_error=None
        )

    async def trigger_completion(self) -> None:
        assert self._on_completion is not None
        await self._on_completion()  # type: ignore[misc]

    async def trigger_permanent_failure(self) -> None:
        assert self._on_permanent_failure is not None
        await self._on_permanent_failure()  # type: ignore[misc]


class _FakeCallManager:
    def __init__(self, settings: Settings) -> None:
        self.joined: list[tuple[Path, Path]] = []
        self.paused = False
        self.left = False
        self.client = MagicMock()
        self._on_permanent_failure: Callable[[], object] | None = None

    def set_permanent_failure_callback(self, callback: Callable[[], object]) -> None:
        self._on_permanent_failure = callback

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def send_media(self, video_pipe: Path, audio_pipe: Path) -> None:
        self.joined.append((video_pipe, audio_pipe))
        self.left = False

    async def pause_call(self) -> None:
        self.paused = True

    async def resume_call(self) -> None:
        self.paused = False

    async def leave_call(self) -> None:
        self.left = True

    def healthcheck(self) -> CallHealth:
        return CallHealth(state=CallState.CONNECTED, chat_id=1, reconnect_count=0, last_error=None)

    async def trigger_permanent_failure(self) -> None:
        assert self._on_permanent_failure is not None
        await self._on_permanent_failure()  # type: ignore[misc]


@pytest.fixture
def make_service(
    monkeypatch: pytest.MonkeyPatch, make_settings: Callable[..., Settings]
) -> Callable[..., PlaybackService]:
    def _make(**overrides: object) -> PlaybackService:
        monkeypatch.setattr(service_module, "QueueManager", _FakeQueueManager)
        monkeypatch.setattr(service_module, "FFmpegStreamer", _FakeStreamer)
        monkeypatch.setattr(service_module, "TelegramCallManager", _FakeCallManager)
        settings = make_settings(**overrides)
        return PlaybackService(settings)

    return _make


def _fakes(service: PlaybackService) -> tuple[_FakeQueueManager, _FakeStreamer, _FakeCallManager]:
    return service._queue, service._streamer, service._call_manager  # type: ignore[return-value]  # noqa: SLF001


async def test_play_starts_immediately_when_idle(
    make_service: Callable[..., PlaybackService], tmp_path: Path
) -> None:
    service = make_service()
    video = tmp_path / "media" / "movie.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"x")

    position = await service.play("movie.mp4", requested_by=1)

    queue, streamer, call = _fakes(service)
    assert position == 1
    assert len(streamer.started_sources) == 1
    assert len(call.joined) == 1


async def test_play_only_enqueues_when_already_active(
    make_service: Callable[..., PlaybackService], tmp_path: Path
) -> None:
    service = make_service()
    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    (media_dir / "a.mp4").write_bytes(b"x")
    (media_dir / "b.mp4").write_bytes(b"x")

    await service.play("a.mp4", requested_by=1)
    position = await service.play("b.mp4", requested_by=1)

    queue, streamer, _ = _fakes(service)
    assert position == 1  # b.mp4 é o único item pendente na fila (a.mp4 já virou current)
    assert len(streamer.started_sources) == 1  # não iniciou de novo


async def test_pause_resume_raise_when_nothing_playing(
    make_service: Callable[..., PlaybackService],
) -> None:
    service = make_service()
    with pytest.raises(NothingPlayingError):
        await service.pause()
    with pytest.raises(NothingPlayingError):
        await service.resume()


async def test_pause_resume_delegate_to_call_manager(
    make_service: Callable[..., PlaybackService], tmp_path: Path
) -> None:
    service = make_service()
    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    (media_dir / "a.mp4").write_bytes(b"x")
    await service.play("a.mp4", requested_by=1)

    _, _, call = _fakes(service)
    await service.pause()
    assert call.paused is True
    await service.resume()
    assert call.paused is False


async def test_stop_playback_raises_when_nothing_playing(
    make_service: Callable[..., PlaybackService],
) -> None:
    service = make_service()
    with pytest.raises(NothingPlayingError):
        await service.stop_playback()


async def test_stop_playback_stops_streamer_and_leaves_call(
    make_service: Callable[..., PlaybackService], tmp_path: Path
) -> None:
    service = make_service()
    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    (media_dir / "a.mp4").write_bytes(b"x")
    await service.play("a.mp4", requested_by=1)

    await service.stop_playback()

    _, streamer, call = _fakes(service)
    assert streamer.stopped is True
    assert call.left is True


async def test_skip_raises_when_nothing_playing(
    make_service: Callable[..., PlaybackService],
) -> None:
    service = make_service()
    with pytest.raises(NothingPlayingError):
        await service.skip()


async def test_skip_switches_to_next_item(
    make_service: Callable[..., PlaybackService], tmp_path: Path
) -> None:
    service = make_service()
    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    (media_dir / "a.mp4").write_bytes(b"x")
    (media_dir / "b.mp4").write_bytes(b"x")
    await service.play("a.mp4", requested_by=1)
    await service.play("b.mp4", requested_by=1)

    next_item = await service.skip()

    assert next_item is not None and next_item.source.raw.endswith("b.mp4")


async def test_skip_stops_when_queue_empties(
    make_service: Callable[..., PlaybackService], tmp_path: Path
) -> None:
    service = make_service()
    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    (media_dir / "a.mp4").write_bytes(b"x")
    await service.play("a.mp4", requested_by=1)

    next_item = await service.skip()

    _, streamer, call = _fakes(service)
    assert next_item is None
    assert streamer.stopped is True
    assert call.left is True


async def test_clear_queue_empties_pending_items(
    make_service: Callable[..., PlaybackService],
) -> None:
    service = make_service()
    queue, _, _ = _fakes(service)
    queue.items = [
        QueueItem(source=MediaSource(raw="x.mp4", type=SourceType.LOCAL_FILE), requested_by=1)
    ]

    await service.clear_queue()

    assert queue.items == []


async def test_status_reflects_queue_and_health(
    make_service: Callable[..., PlaybackService], tmp_path: Path
) -> None:
    service = make_service()
    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    (media_dir / "a.mp4").write_bytes(b"x")
    (media_dir / "b.mp4").write_bytes(b"x")
    await service.play("a.mp4", requested_by=1)
    await service.play("b.mp4", requested_by=1)

    status = service.status()

    assert status.queue_length == 1
    assert status.call.state is CallState.CONNECTED
    assert status.degraded is False


async def test_item_completion_advances_queue_automatically(
    make_service: Callable[..., PlaybackService], tmp_path: Path
) -> None:
    service = make_service()
    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    (media_dir / "a.mp4").write_bytes(b"x")
    (media_dir / "b.mp4").write_bytes(b"x")
    await service.play("a.mp4", requested_by=1)
    await service.play("b.mp4", requested_by=1)

    _, streamer, _ = _fakes(service)
    await streamer.trigger_completion()

    assert len(streamer.started_sources) == 2
    assert streamer.started_sources[1].raw.endswith("b.mp4")


async def test_item_completion_stops_when_queue_empty(
    make_service: Callable[..., PlaybackService], tmp_path: Path
) -> None:
    service = make_service()
    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    (media_dir / "a.mp4").write_bytes(b"x")
    await service.play("a.mp4", requested_by=1)

    _, streamer, call = _fakes(service)
    await streamer.trigger_completion()

    assert streamer.stopped is True
    assert call.left is True


async def test_streamer_permanent_failure_sets_degraded_and_pauses_call(
    make_service: Callable[..., PlaybackService], tmp_path: Path
) -> None:
    service = make_service()
    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    (media_dir / "a.mp4").write_bytes(b"x")
    await service.play("a.mp4", requested_by=1)

    _, streamer, call = _fakes(service)
    await streamer.trigger_permanent_failure()

    status = service.status()
    assert status.degraded is True
    assert status.degraded_reason is not None
    assert call.paused is True


async def test_call_permanent_failure_sets_degraded(
    make_service: Callable[..., PlaybackService], tmp_path: Path
) -> None:
    service = make_service()
    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    (media_dir / "a.mp4").write_bytes(b"x")
    await service.play("a.mp4", requested_by=1)

    _, _, call = _fakes(service)
    await call.trigger_permanent_failure()

    status = service.status()
    assert status.degraded is True


async def test_client_property_delegates_to_call_manager(
    make_service: Callable[..., PlaybackService],
) -> None:
    service = make_service()
    _, _, call = _fakes(service)
    assert service.client is call.client


async def test_start_loads_queue_and_starts_call_manager(
    make_service: Callable[..., PlaybackService],
) -> None:
    service = make_service()
    await service.start()
    queue, _, _ = _fakes(service)
    assert queue.loaded is True


async def test_shutdown_stops_active_playback(
    make_service: Callable[..., PlaybackService], tmp_path: Path
) -> None:
    service = make_service()
    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    (media_dir / "a.mp4").write_bytes(b"x")
    await service.play("a.mp4", requested_by=1)

    await service.shutdown()

    _, streamer, call = _fakes(service)
    assert streamer.stopped is True
    assert call.left is True
