"""Testes de `app/bot/formatting.py` (funções puras)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.bot.formatting import (
    format_now_playing,
    format_queue,
    format_status,
    format_timedelta,
    format_uptime,
)
from app.player.models import LoopMode, PlaybackState, QueueItem
from app.services.models import ServiceStatus
from app.streaming.models import FFmpegProcessState, HealthStatus
from app.telegram.models import CallHealth, CallState
from app.utils.sanitize import MediaSource, SourceType


def _item(name: str) -> QueueItem:
    return QueueItem(
        source=MediaSource(raw=f"/media/{name}", type=SourceType.LOCAL_FILE), requested_by=1
    )


def test_format_queue_empty() -> None:
    state = PlaybackState(items=[], current=None, loop_mode=LoopMode.OFF)
    assert format_queue(state) == "Fila vazia."


def test_format_queue_with_current_only() -> None:
    state = PlaybackState(items=[], current=_item("a.mp4"), loop_mode=LoopMode.OFF)
    text = format_queue(state)
    assert "Tocando agora" in text
    assert "a.mp4" in text


def test_format_queue_with_pending_items() -> None:
    state = PlaybackState(
        items=[_item("b.mp4"), _item("c.mp4")], current=_item("a.mp4"), loop_mode=LoopMode.OFF
    )
    text = format_queue(state)
    assert "1. `/media/b.mp4`" in text
    assert "2. `/media/c.mp4`" in text


def test_format_status_healthy() -> None:
    status = ServiceStatus(
        streaming=HealthStatus(
            state=FFmpegProcessState.RUNNING,
            pid=123,
            current_source="/media/a.mp4",
            restart_count=0,
            last_error=None,
        ),
        call=CallHealth(
            state=CallState.CONNECTED, chat_id=-100, reconnect_count=0, last_error=None
        ),
        queue_length=2,
        loop_mode=LoopMode.QUEUE,
        degraded=False,
        degraded_reason=None,
    )
    text = format_status(status)
    assert "running" in text
    assert "connected" in text
    assert "2 item" in text
    assert "Degradado" not in text


def test_format_status_degraded_includes_reason() -> None:
    status = ServiceStatus(
        streaming=HealthStatus(
            state=FFmpegProcessState.FAILED,
            pid=None,
            current_source=None,
            restart_count=8,
            last_error="boom",
        ),
        call=CallHealth(
            state=CallState.CONNECTED, chat_id=-100, reconnect_count=0, last_error=None
        ),
        queue_length=0,
        loop_mode=LoopMode.OFF,
        degraded=True,
        degraded_reason="FFmpeg falhou permanentemente.",
    )
    text = format_status(status)
    assert "Degradado" in text
    assert "FFmpeg falhou permanentemente." in text


def test_format_timedelta_seconds_only() -> None:
    assert format_timedelta(timedelta(seconds=42)) == "42s"


def test_format_timedelta_minutes_and_seconds() -> None:
    assert format_timedelta(timedelta(minutes=5, seconds=3)) == "5m 03s"


def test_format_timedelta_hours_minutes_seconds() -> None:
    assert format_timedelta(timedelta(hours=1, minutes=2, seconds=3)) == "1h 02m 03s"


def test_format_now_playing_includes_source_and_requester() -> None:
    item = _item("filme.mp4")
    started_at = datetime.now(UTC) - timedelta(minutes=1)
    text = format_now_playing(item, started_at)
    assert "filme.mp4" in text
    assert "1" in text  # requested_by


def test_format_uptime_includes_duration() -> None:
    text = format_uptime(timedelta(hours=2))
    assert "2h" in text
