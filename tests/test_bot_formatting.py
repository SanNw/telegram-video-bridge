"""Testes de `app/bot/formatting.py` (funções puras)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.addon_system.base import SearchResult, StreamCandidate
from app.bot.formatting import (
    format_now_playing,
    format_queue,
    format_status,
    format_stream_buttons,
    format_timedelta,
    format_tmdb_rich_message,
    format_uptime,
)
from app.player.models import LoopMode, PlaybackState, QueueItem
from app.services.models import ServiceStatus
from app.services.tmdb_service import TMDBCastMember, TMDBMetadata
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


def _search_result(year: int | None = 1968) -> SearchResult:
    return SearchResult(
        media_id="abc", title="Night of the Living Dead", year=year, addon_name="archive_org"
    )


def test_format_tmdb_rich_message_includes_all_fields() -> None:
    metadata = TMDBMetadata(
        title="Night of the Living Dead",
        overview="Um grupo se refugia numa casa cercada por mortos-vivos.",
        poster_url="https://image.tmdb.org/t/p/w500/poster.jpg",
        vote_average=7.8,
        genres=["Terror", "Ficção científica"],
        release_date="1968-10-01",
        cast=[],
        backdrop_urls=[],
    )
    html = format_tmdb_rich_message(_search_result(), metadata)
    assert "<h2>Night of the Living Dead (1968)</h2>" in html
    assert '<img src="https://image.tmdb.org/t/p/w500/poster.jpg"/>' in html
    assert "Um grupo se refugia numa casa cercada por mortos-vivos." in html
    assert "7.8" in html
    assert "Terror, Ficção científica" in html
    assert "1968-10-01" in html


def test_format_tmdb_rich_message_omits_photo_when_no_poster() -> None:
    metadata = TMDBMetadata(
        title="Night of the Living Dead",
        overview=None,
        poster_url=None,
        vote_average=None,
        genres=[],
        release_date=None,
        cast=[],
        backdrop_urls=[],
    )
    html = format_tmdb_rich_message(_search_result(year=None), metadata)
    assert "<img" not in html
    assert "<photo" not in html
    assert "<h2>Night of the Living Dead</h2>" in html
    assert ">—<" in html


def test_format_tmdb_rich_message_escapes_html_in_overview() -> None:
    metadata = TMDBMetadata(
        title="Night of the Living Dead",
        overview="<b>Perigo</b> & \"terror\"",
        poster_url=None,
        vote_average=None,
        genres=[],
        release_date=None,
        cast=[],
        backdrop_urls=[],
    )
    html = format_tmdb_rich_message(_search_result(), metadata)
    assert "<b>Perigo</b>" not in html
    assert "&lt;b&gt;Perigo&lt;/b&gt; &amp; \"terror\"" in html


def test_format_tmdb_rich_message_includes_backdrop_slideshow() -> None:
    metadata = TMDBMetadata(
        title="Night of the Living Dead",
        overview=None,
        poster_url=None,
        vote_average=None,
        genres=[],
        release_date=None,
        cast=[],
        backdrop_urls=[
            "https://image.tmdb.org/t/p/w780/back1.jpg",
            "https://image.tmdb.org/t/p/w780/back2.jpg",
        ],
    )
    html = format_tmdb_rich_message(_search_result(), metadata)
    assert (
        "<tg-slideshow>"
        '<img src="https://image.tmdb.org/t/p/w780/back1.jpg"/>'
        '<img src="https://image.tmdb.org/t/p/w780/back2.jpg"/>'
        "</tg-slideshow>"
    ) in html


def test_format_tmdb_rich_message_omits_backdrop_slideshow_when_empty() -> None:
    metadata = TMDBMetadata(
        title="Night of the Living Dead",
        overview=None,
        poster_url=None,
        vote_average=None,
        genres=[],
        release_date=None,
        cast=[],
        backdrop_urls=[],
    )
    html = format_tmdb_rich_message(_search_result(), metadata)
    assert "<tg-slideshow>" not in html


def test_format_tmdb_rich_message_includes_cast_slideshow_and_names() -> None:
    metadata = TMDBMetadata(
        title="Night of the Living Dead",
        overview=None,
        poster_url=None,
        vote_average=None,
        genres=[],
        release_date=None,
        cast=[
            TMDBCastMember(name="Duane Jones", profile_url="https://image.tmdb.org/t/p/w185/duane.jpg"),
            TMDBCastMember(name="Judith O'Dea", profile_url="https://image.tmdb.org/t/p/w185/judith.jpg"),
        ],
        backdrop_urls=[],
    )
    html = format_tmdb_rich_message(_search_result(), metadata)
    assert (
        "<tg-slideshow>"
        '<img src="https://image.tmdb.org/t/p/w185/duane.jpg"/>'
        '<img src="https://image.tmdb.org/t/p/w185/judith.jpg"/>'
        "</tg-slideshow>"
    ) in html
    assert "<p>Elenco: Duane Jones, Judith O&#x27;Dea</p>" in html or "<p>Elenco: Duane Jones, Judith O'Dea</p>" in html


def test_format_tmdb_rich_message_cast_names_without_photos_skip_slideshow() -> None:
    metadata = TMDBMetadata(
        title="Night of the Living Dead",
        overview=None,
        poster_url=None,
        vote_average=None,
        genres=[],
        release_date=None,
        cast=[TMDBCastMember(name="Sem Foto", profile_url=None)],
        backdrop_urls=[],
    )
    html = format_tmdb_rich_message(_search_result(), metadata)
    assert "<tg-slideshow>" not in html
    assert "<p>Elenco: Sem Foto</p>" in html


def test_format_tmdb_rich_message_escapes_html_in_cast_name() -> None:
    metadata = TMDBMetadata(
        title="Night of the Living Dead",
        overview=None,
        poster_url=None,
        vote_average=None,
        genres=[],
        release_date=None,
        cast=[TMDBCastMember(name="<b>Ator</b>", profile_url=None)],
        backdrop_urls=[],
    )
    html = format_tmdb_rich_message(_search_result(), metadata)
    assert "<b>Ator</b>" not in html
    assert "&lt;b&gt;Ator&lt;/b&gt;" in html


def test_format_stream_buttons_single_candidate_without_quality() -> None:
    candidates = [
        ("0", _search_result(), StreamCandidate(url="https://a.example/1.mp4", title="a")),
    ]
    markup = format_stream_buttons(candidates)
    assert len(markup.inline_keyboard) == 1
    button = markup.inline_keyboard[0][0]
    assert button.text == "▶️ archive_org"
    assert button.callback_data == "play:0"


def test_format_stream_buttons_multiple_candidates_with_quality() -> None:
    candidates = [
        ("0", _search_result(), StreamCandidate(url="https://a.example/1.mp4", title="a")),
        (
            "1",
            _search_result(),
            StreamCandidate(url="https://s.example/1.mp4", title="s", quality="1080p"),
        ),
    ]
    markup = format_stream_buttons(candidates)
    assert len(markup.inline_keyboard) == 2
    assert markup.inline_keyboard[0][0].text == "▶️ archive_org"
    assert markup.inline_keyboard[1][0].text == "▶️ archive_org (1080p)"
    assert markup.inline_keyboard[1][0].callback_data == "play:1"
