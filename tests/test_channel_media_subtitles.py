from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import app.services.channel_media_service as channel_module
from app.services.channel_media_service import ChannelMediaService


class _Message:
    def __init__(self, caption: str) -> None:
        self.id = 42
        self.caption = caption
        self.video = SimpleNamespace(file_name="filme.mp4", file_size=1024)
        self.document = None


class _Client:
    def __init__(self, message: _Message) -> None:
        self.message = message

    async def get_messages(self, _chat_id: int, _message_id: int) -> _Message:
        return self.message

    async def stream_media(self, _message: _Message):  # type: ignore[no-untyped-def]
        yield b"x" * 2048


class _Playback:
    def __init__(self) -> None:
        self.played_subtitle_path: str | None = None

    async def play(
        self, _source_raw: str, _requested_by: int, subtitle_path: str | None = None
    ) -> int:
        self.played_subtitle_path = subtitle_path
        return 1


class _TMDB:
    def __init__(self, imdb_id: str | None) -> None:
        self.imdb_id = imdb_id
        self.searched = False

    async def find_imdb_id(self, _title: str) -> str | None:
        self.searched = True
        return self.imdb_id


class _Addons:
    def __init__(self, subtitle_path: str | None) -> None:
        self.subtitle_path = subtitle_path
        self.prepared = False

    async def prepare_subtitle(self, _imdb_id: str, _title: str) -> str | None:
        self.prepared = True
        return self.subtitle_path


def _build_service(
    monkeypatch: Any,
    settings: Any,
    caption: str,
    playback: _Playback,
    tmdb: _TMDB,
    addons: _Addons,
) -> ChannelMediaService:
    message = _Message(caption)
    monkeypatch.setattr(channel_module, "Message", _Message)
    return ChannelMediaService(
        settings,
        _Client(message),  # type: ignore[arg-type]
        playback,  # type: ignore[arg-type]
        addons,  # type: ignore[arg-type]
        tmdb,  # type: ignore[arg-type]
    )


async def test_portuguese_audio_skips_subtitle_lookup(
    monkeypatch: Any, tmp_path: Any, make_settings: Any
) -> None:
    playback = _Playback()
    tmdb = _TMDB("tt0089881")
    addons = _Addons(None)
    service = _build_service(
        monkeypatch,
        make_settings(
            stream_chat_id=-1001,
            media_path=tmp_path,
            qbittorrent_local_path=tmp_path,
            torrent_buffer_mb=0.001,
        ),
        "Ran (1985) Dublado",
        playback,
        tmdb,
        addons,
    )

    position = await service.play(42, requested_by=1)

    assert position == 1
    assert tmdb.searched is False
    assert addons.prepared is False
    assert playback.played_subtitle_path is None


async def test_non_portuguese_audio_prepares_subtitle(
    monkeypatch: Any, tmp_path: Any, make_settings: Any
) -> None:
    playback = _Playback()
    tmdb = _TMDB("tt0089881")
    addons = _Addons("/tmp/.subtitles/tt0089881-pt.srt")
    service = _build_service(
        monkeypatch,
        make_settings(
            stream_chat_id=-1001,
            media_path=tmp_path,
            qbittorrent_local_path=tmp_path,
            torrent_buffer_mb=0.001,
        ),
        "Ran (1985)",
        playback,
        tmdb,
        addons,
    )

    position = await service.play(42, requested_by=1)

    assert position == 1
    assert tmdb.searched is True
    assert addons.prepared is True
    assert playback.played_subtitle_path == "/tmp/.subtitles/tt0089881-pt.srt"


async def test_missing_imdb_id_plays_without_subtitle(
    monkeypatch: Any, tmp_path: Any, make_settings: Any
) -> None:
    playback = _Playback()
    tmdb = _TMDB(None)
    addons = _Addons("/tmp/.subtitles/nunca-chamado.srt")
    service = _build_service(
        monkeypatch,
        make_settings(
            stream_chat_id=-1001,
            media_path=tmp_path,
            qbittorrent_local_path=tmp_path,
            torrent_buffer_mb=0.001,
        ),
        "Ran (1985)",
        playback,
        tmdb,
        addons,
    )

    position = await service.play(42, requested_by=1)

    assert position == 1
    assert tmdb.searched is True
    assert addons.prepared is False
    assert playback.played_subtitle_path is None
