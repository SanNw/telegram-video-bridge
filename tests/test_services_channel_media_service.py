from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import app.services.channel_media_service as channel_module
from app.config.settings import Settings
from app.services.channel_media_service import ChannelMediaService


class _Message:
    def __init__(self) -> None:
        self.id = 42
        self.caption = "Filme de teste"
        self.video = SimpleNamespace(file_name="filme.mp4", file_size=1024)
        self.document = None


class _Client:
    def __init__(self, message: _Message) -> None:
        self.message = message

    async def get_messages(self, _chat_id: int, _message_id: int) -> _Message:
        return self.message

    async def stream_media(self, _message: _Message) -> AsyncIterator[bytes]:
        yield b"x" * 2048


class _Playback:
    async def play(self, *_args: Any) -> int:
        raise RuntimeError("fila indisponível")


class _TMDB:
    async def find_imdb_id(self, _title: str) -> None:
        return None


async def test_playback_failure_removes_channel_download(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    make_settings: Callable[..., Settings],
) -> None:
    monkeypatch.setattr(channel_module, "Message", _Message)
    message = _Message()
    service = ChannelMediaService(
        make_settings(
            stream_chat_id=-1001,
            media_path=tmp_path,
            qbittorrent_local_path=tmp_path,
            torrent_buffer_mb=0.001,
        ),
        _Client(message),  # type: ignore[arg-type]
        _Playback(),  # type: ignore[arg-type]
        SimpleNamespace(),  # type: ignore[arg-type]
        _TMDB(),  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="fila indisponível"):
        await service.play(42, requested_by=1)

    assert list((tmp_path / "channel").glob("*")) == []
    assert service._downloads == {}  # noqa: SLF001
