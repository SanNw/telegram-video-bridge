"""Testes de `app/utils/sanitize.py`."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.utils.sanitize import InvalidSourceError, SourceType, resolve_source


def test_local_file_resolves_when_inside_media_root(tmp_path: Path) -> None:
    media_root = tmp_path / "media"
    media_root.mkdir()
    video = media_root / "movie.mp4"
    video.write_bytes(b"fake")

    result = resolve_source("movie.mp4", media_root)

    assert result.type is SourceType.LOCAL_FILE
    assert result.raw == str(video.resolve())


@pytest.mark.parametrize("extension", [".mp4", ".mkv", ".avi", ".mov"])
def test_local_file_all_allowed_extensions(tmp_path: Path, extension: str) -> None:
    media_root = tmp_path / "media"
    media_root.mkdir()
    video = media_root / f"movie{extension}"
    video.write_bytes(b"fake")

    result = resolve_source(f"movie{extension}", media_root)
    assert result.type is SourceType.LOCAL_FILE


def test_local_file_missing_raises(tmp_path: Path) -> None:
    media_root = tmp_path / "media"
    media_root.mkdir()
    with pytest.raises(InvalidSourceError, match="não encontrado"):
        resolve_source("missing.mp4", media_root)


def test_local_file_disallowed_extension_raises(tmp_path: Path) -> None:
    media_root = tmp_path / "media"
    media_root.mkdir()
    (media_root / "script.sh").write_bytes(b"x")
    with pytest.raises(InvalidSourceError, match="Extensão"):
        resolve_source("script.sh", media_root)


def test_local_file_path_traversal_escaping_media_root_raises(tmp_path: Path) -> None:
    media_root = tmp_path / "media"
    media_root.mkdir()
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"x")

    with pytest.raises(InvalidSourceError, match="fora do diretório"):
        resolve_source("../outside.mp4", media_root)


def test_empty_source_raises(tmp_path: Path) -> None:
    with pytest.raises(InvalidSourceError, match="vazia"):
        resolve_source("   ", tmp_path)


def test_source_starting_with_dash_raises(tmp_path: Path) -> None:
    with pytest.raises(InvalidSourceError, match="injeção de flags"):
        resolve_source("-rf", tmp_path)


@pytest.mark.parametrize("control_char", ["\n", "\r", "\x00"])
def test_source_with_control_chars_raises(tmp_path: Path, control_char: str) -> None:
    with pytest.raises(InvalidSourceError, match="controle"):
        resolve_source(f"http://example.com/a{control_char}b", tmp_path)


@pytest.mark.parametrize(
    ("url", "expected_type"),
    [
        ("http://example.com/video.mp4", SourceType.HTTP),
        ("https://example.com/video.mp4", SourceType.HTTP),
        ("https://example.com/stream.m3u8", SourceType.HLS),
        ("https://example.com/stream.m3u8?token=abc", SourceType.HLS),
        ("rtmp://example.com/live/stream", SourceType.RTMP),
        ("rtmps://example.com/live/stream", SourceType.RTMP),
        ("rtsp://example.com/stream", SourceType.RTSP),
    ],
)
def test_remote_source_classification(tmp_path: Path, url: str, expected_type: SourceType) -> None:
    result = resolve_source(url, tmp_path)
    assert result.type is expected_type
    assert result.raw == url


def test_unsupported_scheme_raises(tmp_path: Path) -> None:
    with pytest.raises(InvalidSourceError, match="Esquema"):
        resolve_source("ftp://example.com/video.mp4", tmp_path)


def test_url_without_host_raises(tmp_path: Path) -> None:
    with pytest.raises(InvalidSourceError, match="host"):
        resolve_source("http:///video.mp4", tmp_path)
