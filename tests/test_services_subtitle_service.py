"""Testes do catálogo local e online de legendas."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.services.opensubtitles_client import SubtitleOption
from app.services.subtitle_service import SubtitleService


def test_list_local_returns_only_direct_srt_files(tmp_path: Path) -> None:
    root = tmp_path / ".subtitles"
    root.mkdir()
    (root / "a.srt").write_text("ok", encoding="utf-8")
    (root / "ignore.txt").write_text("no", encoding="utf-8")
    (root / "nested").mkdir()
    (root / "nested" / "b.srt").write_text("no", encoding="utf-8")
    service = SubtitleService(root, client=None)

    assert [item.name for item in service.list_local()] == ["a.srt"]


def test_resolve_local_rejects_unknown_token(tmp_path: Path) -> None:
    service = SubtitleService(tmp_path, client=None)
    service.list_local()

    with pytest.raises(ValueError, match="expirou"):
        service.resolve_local("../../outside.srt")


async def test_download_writes_safe_srt_name(tmp_path: Path) -> None:
    client = AsyncMock()
    client.download.return_value = b"1\n00:00:00,000 --> 00:00:01,000\nOi\n"
    service = SubtitleService(tmp_path, client)

    path = await service.download(SubtitleOption(7, "pt-BR", "../../Matrix", 10), "tt0133093")

    assert path.parent == tmp_path.resolve()
    assert path.suffix == ".srt"
    assert ".." not in path.name
    assert path.read_bytes() == client.download.return_value
