"""Testes de `app/bot/formatting.py` (funções puras)."""

from __future__ import annotations

import json
import unicodedata
from datetime import UTC, datetime, timedelta

from app.addon_system.base import SearchResult, StreamCandidate
from app.addon_system.manager import AddonInfo
from app.bot import formatting
from app.bot.formatting import (
    format_addons_screen,
    format_candidate_page,
    format_catalog_buttons,
    format_catalog_page,
    format_controls_screen,
    format_main_menu,
    format_movie_card,
    format_movie_details,
    format_movie_results,
    format_now_playing,
    format_now_playing_screen,
    format_playback_panel,
    format_queue,
    format_queue_screen,
    format_search_results,
    format_status,
    format_stream_buttons,
    format_subtitle_options,
    format_subtitle_panel,
    format_timedelta,
    format_tmdb_rich_message,
    format_uptime,
    format_volume_panel,
    rich_callback_actions,
)
from app.player.models import LoopMode, PlaybackState, QueueItem
from app.services.models import ServiceStatus
from app.services.tmdb_service import TMDBCastMember, TMDBMetadata, TMDBMovie
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
        original_title="Night of the Living Dead",
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
        original_title="Night of the Living Dead",
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
        original_title="Night of the Living Dead",
        overview='<b>Perigo</b> & "terror"',
        poster_url=None,
        vote_average=None,
        genres=[],
        release_date=None,
        cast=[],
        backdrop_urls=[],
    )
    html = format_tmdb_rich_message(_search_result(), metadata)
    assert "<b>Perigo</b>" not in html
    assert '&lt;b&gt;Perigo&lt;/b&gt; &amp; "terror"' in html


def test_format_tmdb_rich_message_includes_backdrop_slideshow() -> None:
    metadata = TMDBMetadata(
        title="Night of the Living Dead",
        original_title="Night of the Living Dead",
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
        original_title="Night of the Living Dead",
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
        original_title="Night of the Living Dead",
        overview=None,
        poster_url=None,
        vote_average=None,
        genres=[],
        release_date=None,
        cast=[
            TMDBCastMember(
                name="Duane Jones", profile_url="https://image.tmdb.org/t/p/w185/duane.jpg"
            ),
            TMDBCastMember(
                name="Judith O'Dea", profile_url="https://image.tmdb.org/t/p/w185/judith.jpg"
            ),
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
    assert (
        "<p>Elenco: Duane Jones, Judith O&#x27;Dea</p>" in html
        or "<p>Elenco: Duane Jones, Judith O'Dea</p>" in html
    )


def test_format_tmdb_rich_message_cast_names_without_photos_skip_slideshow() -> None:
    metadata = TMDBMetadata(
        title="Night of the Living Dead",
        original_title="Night of the Living Dead",
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
        original_title="Night of the Living Dead",
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


def _search_result_titled(title: str) -> SearchResult:
    return SearchResult(media_id="abc", title=title, year=1968, addon_name="archive_org")


def test_format_search_results_includes_dubbed_flag() -> None:
    text = format_search_results([_search_result_titled("Filme Dublado")])
    assert "🇧🇷 Filme Dublado" in text


def test_format_search_results_includes_subtitled_flag() -> None:
    text = format_search_results([_search_result_titled("Movie Legendado")])
    assert "🇺🇸 Movie Legendado" in text


def test_format_search_results_omits_flag_when_no_marker() -> None:
    text = format_search_results([_search_result_titled("Movie")])
    assert "1. Movie" in text


def test_format_stream_buttons_single_candidate_without_quality() -> None:
    candidates = [
        ("0", _search_result(), StreamCandidate(url="https://a.example/1.mp4", title="a")),
    ]
    markup = format_stream_buttons(candidates)
    assert len(markup.inline_keyboard) == 1
    button = markup.inline_keyboard[0][0]
    assert button.text == "archive_org"
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
    assert markup.inline_keyboard[0][0].text == "archive_org"
    assert markup.inline_keyboard[1][0].text == "1080p"
    assert markup.inline_keyboard[1][0].callback_data == "play:1"


def test_format_stream_button_keeps_torrentio_quality_on_one_short_line() -> None:
    candidates = [
        (
            "0",
            _search_result(),
            StreamCandidate(
                title="The Matrix 1999 Dublado",
                quality="Torrentio\n1080p",
                seeds=2081,
                size_bytes=2 * 1024**3,
            ),
        )
    ]

    button = format_stream_buttons(candidates).inline_keyboard[0][0]

    assert button.text == "1080p · 2081"
    assert "\n" not in button.text


def test_format_stream_buttons_omits_language_emoji_from_title() -> None:
    candidates = [
        (
            "0",
            _search_result(),
            StreamCandidate(url="https://a.example/1.mp4", title="Filme Dublado"),
        ),
    ]
    markup = format_stream_buttons(candidates)
    assert markup.inline_keyboard[0][0].text == "archive_org"


def test_format_stream_buttons_omits_language_emoji_from_quality() -> None:
    candidates = [
        (
            "0",
            _search_result(),
            StreamCandidate(url="https://a.example/1.mp4", title="a", quality="1080p Legendado"),
        ),
    ]
    markup = format_stream_buttons(candidates)
    assert markup.inline_keyboard[0][0].text == "1080p"


def test_main_menu_has_expected_actions() -> None:
    message = format_main_menu("Rafael", "Cinema")

    assert rich_callback_actions(message) == {
        "menu:find",
        "menu:channel",
        "menu:now",
        "menu:queue",
        "menu:controls",
        "menu:help",
    }
    assert "Rafael" in json.dumps(message, ensure_ascii=False)
    assert "Cinema" in json.dumps(message, ensure_ascii=False)


def test_rich_button_labels_are_mobile_safe() -> None:
    movie = TMDBMovie(
        1, "The Matrix Reloaded With A Very Long Title", None, None, None, 7.0, "2003-05-15"
    )
    metadata = TMDBMetadata(
        title=movie.title,
        original_title=movie.title,
        overview="Sinopse",
        poster_url=None,
        vote_average=7.0,
        genres=["Ação"],
        release_date="2003-05-15",
        cast=[],
        backdrop_urls=[],
    )
    candidate = (
        "0",
        SearchResult("tt0234215", movie.title, 2003, "stremio"),
        StreamCandidate(title=f"{movie.title} 1080p", quality="Torrentio\n1080p", seeds=2081),
    )
    messages = [
        format_main_menu("San", "Cinema"),
        formatting.format_help_screen(),
        format_movie_results([movie], 0),
        format_movie_details(movie, metadata),
        format_candidate_page([candidate], 0),
    ]

    labels = [
        button["text"]
        for message in messages
        for block in message["blocks"]
        if block.get("type") == "buttons"
        for button in block["buttons"]
    ]

    assert labels
    assert all("\n" not in label and len(label) <= 16 for label in labels)
    assert all(not any(unicodedata.category(char) == "So" for char in label) for label in labels)


def test_help_home_groups_commands_into_navigable_topics() -> None:
    message = formatting.format_help_screen()

    assert rich_callback_actions(message) == {
        "help:movies",
        "help:playback",
        "help:queue",
        "help:subtitles",
        "help:system",
        "help:admin",
        "menu:home",
    }


def test_help_topic_keeps_navigation_in_the_same_message() -> None:
    message = formatting.format_help_topic("movies")
    payload = json.dumps(message, ensure_ascii=False)

    assert "/find <título>" in payload
    assert rich_callback_actions(message) == {"menu:help", "menu:home"}


def test_movie_results_exposes_selection_and_pagination() -> None:
    movies = [
        TMDBMovie(
            id=index,
            title=f"Movie {index}",
            original_title=None,
            overview=None,
            poster_url=None,
            vote_average=None,
            release_date="2020-01-01",
        )
        for index in range(7)
    ]

    message = format_movie_results(movies, page=0, page_size=5)

    assert rich_callback_actions(message) == {
        "movie:0",
        "movie:1",
        "movie:2",
        "movie:3",
        "movie:4",
        "catalog:1",
        "flow:cancel",
    }


def test_movie_details_exposes_source_search_and_back() -> None:
    movie = TMDBMovie(
        id=603,
        title="The Matrix",
        original_title="The Matrix",
        overview=None,
        poster_url=None,
        vote_average=8.2,
        release_date="1999-03-31",
    )
    metadata = TMDBMetadata(
        title="The Matrix",
        original_title="The Matrix",
        overview="A realidade não é o que parece.",
        poster_url=None,
        vote_average=8.2,
        genres=["Ação"],
        release_date="1999-03-31",
        cast=[],
        backdrop_urls=[],
    )

    message = format_movie_details(movie, metadata)

    assert rich_callback_actions(message) == {"sources:0", "flow:back", "flow:cancel"}
    assert "Assistir" in json.dumps(message, ensure_ascii=False)
    assert "Ver fontes" not in json.dumps(message, ensure_ascii=False)
    assert "The Matrix" in json.dumps(message, ensure_ascii=False)


def test_movie_details_uses_supported_photo_and_slideshow_blocks() -> None:
    movie = TMDBMovie(603, "The Matrix", "The Matrix", None, None, 8.2, "1999-03-31")
    metadata = TMDBMetadata(
        title="The Matrix",
        original_title="The Matrix",
        overview="A realidade não é o que parece.",
        poster_url="https://image.tmdb.org/poster.jpg",
        vote_average=8.2,
        genres=["Ação"],
        release_date="1999-03-31",
        cast=[
            TMDBCastMember("Keanu Reeves", "https://image.tmdb.org/keanu.jpg"),
            TMDBCastMember("Carrie-Anne Moss", "https://image.tmdb.org/carrie.jpg"),
        ],
        backdrop_urls=[
            "https://image.tmdb.org/backdrop-1.jpg",
            "https://image.tmdb.org/backdrop-2.jpg",
        ],
    )

    message = format_movie_details(movie, metadata)
    blocks = message["blocks"]

    assert not any(block.get("type") == "image" for block in blocks)
    assert blocks[1] == {
        "type": "photo",
        "photo": {"type": "photo", "media": metadata.poster_url},
    }
    slideshows = [block for block in blocks if block.get("type") == "slideshow"]
    assert len(slideshows) == 2
    assert all(
        photo["type"] == "photo" and photo["photo"]["type"] == "photo"
        for slideshow in slideshows
        for photo in slideshow["blocks"]
    )
    assert "Keanu Reeves, Carrie-Anne Moss" in json.dumps(message, ensure_ascii=False)


def test_rich_buttons_are_left_aligned() -> None:
    message = format_main_menu("Neo", None)

    button_blocks = [block for block in message["blocks"] if block.get("type") == "buttons"]

    assert button_blocks
    assert all(block["align"] == "left" for block in button_blocks)


def test_candidate_page_shows_ranked_metadata_and_navigation() -> None:
    candidates = [
        (
            str(index),
            SearchResult("tt0133093", "The Matrix", 1999, "stremio"),
            StreamCandidate(
                title="The Matrix 1999 Dublado",
                quality="1080p",
                seeds=200 - index,
                size_bytes=2 * 1024**3,
            ),
        )
        for index in range(6)
    ]

    message = format_candidate_page(candidates, page=0, page_size=5)
    text = json.dumps(message, ensure_ascii=False)

    assert "1080p" in text
    assert "1080p · 200" in text
    assert "stremio · 2.0 GB" in text
    assert rich_callback_actions(message) == {
        "source:0",
        "source:1",
        "source:2",
        "source:3",
        "source:4",
        "sources:1",
        "flow:back",
        "flow:refresh",
        "flow:cancel",
    }
    assert '"type": "buttons"' in text
    assert "Atualizar" in text
    assert "Voltar" in text
    assert "Fechar" in text


def test_controls_screen_exposes_every_playback_action() -> None:
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
        queue_length=1,
        loop_mode=LoopMode.OFF,
        degraded=False,
        degraded_reason=None,
    )

    actions = rich_callback_actions(format_controls_screen(status))

    assert {
        "control:pause",
        "control:resume",
        "control:stop",
        "control:exit",
        "control:skip",
        "control:restart",
        "control:loop:off",
        "control:loop:track",
        "control:loop:queue",
        "control:volume:50",
        "control:volume:100",
        "control:volume:150",
        "control:volume:200",
        "menu:home",
    } <= actions


def test_playback_panel_exposes_mobile_safe_controls() -> None:
    status = ServiceStatus(
        streaming=HealthStatus(FFmpegProcessState.RUNNING, 123, "/media/a.mp4", 0, None),
        call=CallHealth(CallState.CONNECTED, -100, 0, None),
        queue_length=1,
        loop_mode=LoopMode.OFF,
        degraded=False,
        degraded_reason=None,
    )
    state = PlaybackState(current=_item("a.mp4"))

    message = format_playback_panel(status, state)

    assert {
        "control:pause",
        "control:resume",
        "control:stop",
        "control:skip",
        "control:restart",
        "menu:volume",
        "subtitle:menu",
        "menu:queue",
    } <= rich_callback_actions(message)
    _assert_mobile_safe(message)


def test_subtitle_panels_expose_selection_and_navigation() -> None:
    message = format_subtitle_panel(_item("a.mp4"))
    options = format_subtitle_options(
        "Legendas locais",
        ["Matrix release longa.srt", "Matrix alternativa.srt"],
        0,
        "subtitle:local-pick",
    )

    assert {
        "subtitle:toggle",
        "subtitle:local:0",
        "subtitle:search:0",
        "subtitle:delay",
        "menu:controls",
    } <= rich_callback_actions(message)
    assert {
        "subtitle:local-pick:0",
        "subtitle:local-pick:1",
        "subtitle:menu",
    } <= rich_callback_actions(options)
    _assert_mobile_safe(message)
    _assert_mobile_safe(options)


def test_volume_panel_exposes_presets_and_back() -> None:
    status = ServiceStatus(
        streaming=HealthStatus(FFmpegProcessState.RUNNING, 123, None, 0, None),
        call=CallHealth(CallState.CONNECTED, -100, 0, None),
        queue_length=0,
        loop_mode=LoopMode.OFF,
        degraded=False,
        degraded_reason=None,
    )
    actions = rich_callback_actions(format_volume_panel(status))
    assert {
        "control:volume:50",
        "control:volume:100",
        "control:volume:150",
        "menu:controls",
    } <= actions


def _assert_mobile_safe(message: dict[str, object]) -> None:
    labels = [
        button["text"]
        for block in message["blocks"]  # type: ignore[index]
        if block.get("type") == "buttons"
        for button in block["buttons"]
    ]
    assert all("\n" not in label and len(label) <= 16 for label in labels)
    assert all(not any(unicodedata.category(char) == "So" for char in label) for label in labels)


def test_queue_and_addon_screens_return_home() -> None:
    state = PlaybackState(items=[_item("b.mp4")], current=_item("a.mp4"), loop_mode=LoopMode.OFF)
    addons = [AddonInfo(name="stremio", version="1", description="", enabled=True)]

    assert rich_callback_actions(format_queue_screen(state)) == {"menu:home"}
    assert rich_callback_actions(format_addons_screen(addons)) == {"menu:home"}


def test_catalog_legacy_formatters_cover_both_navigation_directions() -> None:
    movies = [
        TMDBMovie(index, f"Movie {index}", None, None, None, 7.0, "2020-01-01")
        for index in range(7)
    ]

    assert "página 1/2" in format_catalog_page(movies, 0)
    first = format_catalog_buttons(movies, 0)
    second = format_catalog_buttons(movies, 1)

    assert first.inline_keyboard[-1][0].callback_data == "catalog:1"
    assert second.inline_keyboard[-1][0].callback_data == "catalog:0"


def test_movie_card_truncates_long_overview_and_handles_missing_values() -> None:
    movie = TMDBMovie(1, "Movie", None, None, None, None, None)
    metadata = TMDBMetadata(
        title="Movie",
        original_title=None,
        overview="x" * 700,
        poster_url=None,
        vote_average=None,
        genres=[],
        release_date=None,
        cast=[],
        backdrop_urls=[],
    )

    text = format_movie_card(movie, metadata)

    assert f"{'x' * 647}..." in text


def test_rich_status_screens_cover_empty_and_current_states() -> None:
    empty = PlaybackState(items=[], current=None, loop_mode=LoopMode.OFF)
    current = PlaybackState(items=[], current=_item("a.mp4"), loop_mode=LoopMode.OFF)

    assert "Nada está tocando" in str(format_now_playing_screen(empty))
    assert "a.mp4" in str(format_now_playing_screen(current))
    assert "Fila vazia" in str(format_queue_screen(empty))
    assert "Nenhum addon" in str(format_addons_screen([]))


def test_candidate_second_page_has_previous_navigation() -> None:
    candidates = [
        (
            str(index),
            _search_result(),
            StreamCandidate(title="Movie", quality="1080p", seeds=index),
        )
        for index in range(6)
    ]

    actions = rich_callback_actions(format_candidate_page(candidates, page=1))

    assert "sources:0" in actions
    assert "source:5" in actions


def test_rich_callback_actions_ignores_malformed_blocks() -> None:
    assert rich_callback_actions({"blocks": [None, {"type": "buttons", "buttons": None}]}) == set()
    assert rich_callback_actions({"blocks": "invalid"}) == set()
