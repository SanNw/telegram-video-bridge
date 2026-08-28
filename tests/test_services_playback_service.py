"""Testes de `app/services/playback_service.py`.

`QueueManager`, `FFmpegStreamer` e `TelegramCallManager` são substituídos por
dublês leves — o objetivo aqui é testar a orquestração (quem chama o quê, em
que ordem, e como reage a conclusão/falha), não redundar com os testes de
cada camada individual (já cobertos em outros arquivos).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import app.services.playback_service as service_module
from app.config.settings import Settings
from app.player.exceptions import InvalidQueueIndexError
from app.player.models import LoopMode, PlaybackState, QueueItem
from app.services.exceptions import InvalidVolumeError, NothingPlayingError
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

    async def discard_current(self) -> None:
        self.current = None

    async def set_subtitle_delay(self, delay_ms: int) -> None:
        if self.current is not None:
            self.current.subtitle_delay_ms = delay_ms

    async def set_subtitle_path(self, path: str | None) -> None:
        if self.current is not None:
            self.current.subtitle_path = path
            self.current.subtitles_enabled = path is not None

    async def set_subtitles_enabled(self, enabled: bool) -> None:
        if self.current is not None:
            self.current.subtitles_enabled = enabled

    async def remove(self, position: int) -> QueueItem:
        index = position - 1
        if index < 0 or index >= len(self.items):
            raise InvalidQueueIndexError(f"Posição inválida: {position}.")
        return self.items.pop(index)

    async def set_loop_mode(self, mode: LoopMode) -> None:
        self.loop_mode = mode

    def snapshot(self) -> PlaybackState:
        return PlaybackState(items=list(self.items), current=self.current, loop_mode=self.loop_mode)


class _FakeStreamer:
    def __init__(self, settings: Settings) -> None:
        self.video_pipe_path = Path("/fake/video.pipe")
        self.audio_pipe_path = Path("/fake/audio.pipe")
        self.started_sources: list[MediaSource] = []
        self.stopped = False
        self.restart_count = 0
        self.state = FFmpegProcessState.IDLE
        self._on_completion: Callable[[], object] | None = None
        self._on_permanent_failure: Callable[[], object] | None = None
        self._on_source_released: Callable[[MediaSource], object] | None = None
        self.change_calls: list[tuple[object, ...]] = []
        self.change_exception: Exception | None = None

    def set_completion_callback(self, callback: Callable[[], object]) -> None:
        self._on_completion = callback

    def set_permanent_failure_callback(self, callback: Callable[[], object]) -> None:
        self._on_permanent_failure = callback

    def set_source_released_callback(self, callback: Callable[[MediaSource], object]) -> None:
        self._on_source_released = callback

    async def change_source(self, source: MediaSource, *args: object) -> None:
        if self.change_exception is not None:
            raise self.change_exception
        self.started_sources.append(source)
        self.change_calls.append((source, *args))
        self.stopped = False
        self.state = FFmpegProcessState.RUNNING

    async def stop(self, *, notify_release: bool = True) -> None:
        self.stopped = True
        self.state = FFmpegProcessState.STOPPED

    async def restart(self) -> None:
        self.restart_count += 1

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
        self.ended = False
        self.volume: int | None = None
        self.client = MagicMock()
        self._on_permanent_failure: Callable[[], object] | None = None
        self.rtmp_active = False
        self.rtmp_url = "rtmps://telegram/live/key"

    def set_permanent_failure_callback(self, callback: Callable[[], object]) -> None:
        self._on_permanent_failure = callback

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def send_media(self, video_pipe: Path, audio_pipe: Path) -> None:
        self.joined.append((video_pipe, audio_pipe))
        self.left = False

    async def join_call(self, video_pipe: Path, audio_pipe: Path) -> None:
        self.joined.append((video_pipe, audio_pipe))
        self.left = False

    async def prepare_rtmp(self) -> str:
        if not self.rtmp_active:
            raise RuntimeError("RTMP indisponível")
        return self.rtmp_url

    async def pause_call(self) -> None:
        self.paused = True

    async def resume_call(self) -> None:
        self.paused = False

    async def leave_call(self) -> None:
        self.left = True

    async def end_call(self) -> None:
        self.left = True
        self.ended = True

    async def change_volume(self, volume: int) -> None:
        self.volume = volume

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


async def test_play_persists_movie_context(
    make_service: Callable[..., PlaybackService], tmp_path: Path
) -> None:
    service = make_service()
    video = tmp_path / "media" / "movie.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"x")

    await service.play(
        "movie.mp4",
        1,
        media_id="tt0133093",
        display_title="The Matrix",
    )

    queue, _, _ = _fakes(service)
    current = queue.snapshot().current
    assert current is not None
    assert (current.media_id, current.display_title) == ("tt0133093", "The Matrix")


async def test_set_subtitle_path_restarts_at_elapsed_position(
    make_service: Callable[..., PlaybackService], tmp_path: Path
) -> None:
    service = make_service()
    video = tmp_path / "media" / "movie.mp4"
    subtitle = tmp_path / "media" / ".subtitles" / "matrix-alt.srt"
    video.parent.mkdir(parents=True, exist_ok=True)
    subtitle.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"x")
    subtitle.write_text("subtitle", encoding="utf-8")
    await service.play("movie.mp4", requested_by=1)
    queue, streamer, call = _fakes(service)
    call.rtmp_active = True
    service._current_started_at = datetime.now(UTC) - timedelta(seconds=42)  # noqa: SLF001

    await service.set_subtitle_path(str(subtitle))

    current = queue.snapshot().current
    assert current is not None
    assert current.subtitle_path == str(subtitle)
    assert streamer.change_calls[-1][2] == str(subtitle)
    assert 41 <= streamer.change_calls[-1][4] <= 43


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


async def test_pause_resume_rtmp_stops_and_restarts_from_elapsed_position(
    make_service: Callable[..., PlaybackService], tmp_path: Path
) -> None:
    service = make_service()
    _, streamer, call = _fakes(service)
    call.rtmp_active = True
    await _play_one(service, tmp_path)

    await service.pause()
    assert streamer.stopped is True

    await service.resume()
    assert streamer.stopped is False
    assert streamer.change_calls[-1][1] == call.rtmp_url
    assert streamer.change_calls[-1][4] >= 0


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


async def test_exit_and_delete_ends_call_and_deletes_current_source(
    make_service: Callable[..., PlaybackService], tmp_path: Path
) -> None:
    service = make_service()
    deleted: list[str] = []

    async def delete_source(source: MediaSource) -> None:
        deleted.append(source.raw)

    service.set_source_deleted_callback(delete_source)
    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    movie = media_dir / "a.mp4"
    movie.write_bytes(b"x")
    await service.play("a.mp4", requested_by=1)

    await service.exit_and_delete()

    _, streamer, call = _fakes(service)
    assert streamer.stopped is True
    assert call.ended is True
    assert deleted == [str(movie.resolve())]
    assert service.queue_snapshot().current is None


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


async def test_start_resumes_persisted_current_item(
    make_service: Callable[..., PlaybackService], tmp_path: Path
) -> None:
    service = make_service()
    queue, streamer, call = _fakes(service)
    movie = tmp_path / "movie.mkv"
    movie.write_bytes(b"x")
    source = MediaSource(str(movie), SourceType.LOCAL_FILE)
    queue.current = QueueItem(source=source, requested_by=1)

    await service.start()

    assert streamer.started_sources == [source]
    assert call.joined == [(streamer.video_pipe_path, streamer.audio_pipe_path)]


async def test_start_survives_failure_resuming_current_item(
    make_service: Callable[..., PlaybackService], tmp_path: Path
) -> None:
    service = make_service()
    queue, streamer, call = _fakes(service)
    movie = tmp_path / "movie.mkv"
    movie.write_bytes(b"x")
    queue.current = QueueItem(source=MediaSource(str(movie), SourceType.LOCAL_FILE), requested_by=1)
    call.join_call = MagicMock(side_effect=RuntimeError("sem chamada ativa"))

    await service.start()

    assert streamer.stopped is True
    assert service.status().degraded is True


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


async def _play_one(service: PlaybackService, tmp_path: Path, name: str = "a.mp4") -> None:
    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    (media_dir / name).write_bytes(b"x")
    await service.play(name, requested_by=1)


async def test_remove_from_queue_delegates_to_queue_manager(
    make_service: Callable[..., PlaybackService], tmp_path: Path
) -> None:
    service = make_service()
    await _play_one(service, tmp_path, "a.mp4")  # vira current
    await _play_one(service, tmp_path, "b.mp4")  # pendente, posição 1

    removed = await service.remove_from_queue(1)

    assert removed.source.raw.endswith("b.mp4")
    queue, _, _ = _fakes(service)
    assert queue.items == []


async def test_set_loop_mode_delegates_to_queue_manager(
    make_service: Callable[..., PlaybackService],
) -> None:
    service = make_service()
    await service.set_loop_mode(LoopMode.QUEUE)
    queue, _, _ = _fakes(service)
    assert queue.loop_mode is LoopMode.QUEUE


async def test_set_volume_raises_when_nothing_playing(
    make_service: Callable[..., PlaybackService],
) -> None:
    service = make_service()
    with pytest.raises(NothingPlayingError):
        await service.set_volume(100)


@pytest.mark.parametrize("volume", [-1, 201])
async def test_set_volume_raises_when_out_of_range(
    make_service: Callable[..., PlaybackService], tmp_path: Path, volume: int
) -> None:
    service = make_service()
    await _play_one(service, tmp_path)
    with pytest.raises(InvalidVolumeError):
        await service.set_volume(volume)


async def test_set_volume_success(
    make_service: Callable[..., PlaybackService], tmp_path: Path
) -> None:
    service = make_service()
    await _play_one(service, tmp_path)
    await service.set_volume(150)
    _, _, call = _fakes(service)
    assert call.volume == 150


async def test_set_volume_restarts_rtmp_with_ffmpeg_volume(
    make_service: Callable[..., PlaybackService], tmp_path: Path
) -> None:
    service = make_service()
    _, streamer, call = _fakes(service)
    call.rtmp_active = True
    await _play_one(service, tmp_path)

    await service.set_volume(150)

    assert streamer.change_calls[-1][-1] == 150
    assert call.volume is None


async def test_failed_initial_start_discards_current_item(
    make_service: Callable[..., PlaybackService], tmp_path: Path
) -> None:
    service = make_service()
    queue_manager, streamer, _ = _fakes(service)
    streamer.change_exception = RuntimeError("falha no FFmpeg")
    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    (media_dir / "a.mp4").write_bytes(b"x")

    with pytest.raises(RuntimeError, match="falha no FFmpeg"):
        await service.play("a.mp4", requested_by=1)

    assert queue_manager.current is None
    assert streamer.stopped is True


async def test_source_cleanup_continues_when_one_callback_fails(
    make_service: Callable[..., PlaybackService], tmp_path: Path
) -> None:
    service = make_service()
    cleaned: list[str] = []

    async def fail(_source: MediaSource) -> None:
        raise RuntimeError("falha de limpeza")

    async def succeed(source: MediaSource) -> None:
        cleaned.append(source.raw)

    service.set_source_released_callback(fail)
    service.set_source_released_callback(succeed)
    source = MediaSource(str(tmp_path / "a.mp4"), SourceType.LOCAL_FILE)

    await service._handle_source_released(source)  # noqa: SLF001

    assert cleaned == [source.raw]


async def test_restart_current_raises_when_nothing_playing(
    make_service: Callable[..., PlaybackService],
) -> None:
    service = make_service()
    with pytest.raises(NothingPlayingError):
        await service.restart_current()


async def test_restart_current_calls_streamer_restart(
    make_service: Callable[..., PlaybackService], tmp_path: Path
) -> None:
    service = make_service()
    await _play_one(service, tmp_path)
    await service.restart_current()
    _, streamer, _ = _fakes(service)
    assert streamer.restart_count == 1


async def test_now_playing_none_when_idle(make_service: Callable[..., PlaybackService]) -> None:
    service = make_service()
    assert service.now_playing() is None


async def test_now_playing_returns_current_item_and_start_time(
    make_service: Callable[..., PlaybackService], tmp_path: Path
) -> None:
    service = make_service()
    await _play_one(service, tmp_path)
    result = service.now_playing()
    assert result is not None
    item, started_at = result
    assert item.source.raw.endswith("a.mp4")
    assert started_at is not None


async def test_uptime_is_zero_before_start(make_service: Callable[..., PlaybackService]) -> None:
    service = make_service()
    assert service.uptime().total_seconds() == 0


async def test_uptime_increases_after_start(
    make_service: Callable[..., PlaybackService],
) -> None:
    service = make_service()
    await service.start()
    assert service.uptime().total_seconds() >= 0
