from __future__ import annotations

from pathlib import Path
from typing import Any

from app.services.addon_service import AddonService


class _Settings:
    def __init__(self, media_path: Path) -> None:
        self.qbittorrent_local_path = media_path
        self.addons_path = media_path / "addons"
        self.addons_config_path = media_path / "addons-config"
        self.addons_state_path = media_path / "addons-state.json"
        self.addon_search_timeout_seconds = 1
        self.addon_streams_timeout_seconds = 1
        self.addon_search_cache_ttl_seconds = 1


def _build_service(tmp_path: Path) -> AddonService:
    return AddonService(_Settings(tmp_path), None, None, None)  # type: ignore[arg-type]


async def test_pinned_subtitle_wins_for_ran(tmp_path: Any) -> None:
    service = _build_service(tmp_path)
    delayed_track = {
        "id": "134344",
        "lang": "pob",
        "url": "https://example.com/ran-delayed.srt",
    }
    correct_track = {
        "id": "273567",
        "lang": "pob",
        "url": "https://example.com/ran-correct.srt",
        "files": [{"file_id": 292268}],
    }
    subtitles = [delayed_track, correct_track]

    picked = service._pick_portuguese_subtitle(subtitles, "tt0089881")

    assert picked is correct_track


async def test_pinned_fallback_matches_file_id_inside_entry(tmp_path: Any) -> None:
    service = _build_service(tmp_path)
    other_language = {"id": "999", "lang": "eng", "url": "https://example.com/eng.srt"}
    pinned_via_files = {
        "id": "888",
        "lang": "pob",
        "url": "https://example.com/pob.srt",
        "files": [{"file_id": 292268, "cd_number": 1}],
    }
    subtitles = [other_language, pinned_via_files]

    picked = service._pick_portuguese_subtitle(subtitles, "tt0089881")

    assert picked is pinned_via_files


async def test_non_pinned_movie_uses_language_priority_and_skips_hi(
    tmp_path: Any,
) -> None:
    service = _build_service(tmp_path)
    hi_first = {
        "id": "1",
        "lang": "pob",
        "hearing_impaired": True,
        "url": "https://example.com/hi.srt",
    }
    clean_second = {"id": "2", "lang": "pob", "url": "https://example.com/clean.srt"}
    subtitles = [hi_first, clean_second]

    picked = service._pick_portuguese_subtitle(subtitles, "tt1234567")

    assert picked is clean_second


async def test_language_fallback_accepts_hi_when_no_clean_track(
    tmp_path: Any,
) -> None:
    service = _build_service(tmp_path)
    hi_only = {
        "id": "1",
        "lang": "pob",
        "hearing_impaired": True,
        "url": "https://example.com/hi.srt",
    }
    subtitles = [hi_only]

    picked = service._pick_portuguese_subtitle(subtitles, "tt1234567")

    assert picked is hi_only


async def test_pick_returns_none_without_portuguese(tmp_path: Any) -> None:
    service = _build_service(tmp_path)
    english_only = [{"id": "1", "lang": "eng", "url": "https://example.com/eng.srt"}]

    picked = service._pick_portuguese_subtitle(english_only, "tt1234567")

    assert picked is None


async def test_prepare_subtitle_downloads_selected_track(tmp_path: Any, monkeypatch: Any) -> None:
    service = _build_service(tmp_path)
    correct_track = {
        "id": "273567",
        "lang": "pob",
        "url": "https://example.com/ran-correct.srt",
        "files": [{"file_id": 292268}],
    }

    class _StubClient:
        async def get_subtitles(self, _type: str, _imdb_id: str) -> list[dict[str, Any]]:
            return [
                {
                    "id": "134344",
                    "lang": "pob",
                    "url": "https://example.com/ran-delayed.srt",
                },
                correct_track,
            ]

        async def download_subtitle(self, url: str) -> bytes | None:
            assert url == "https://example.com/ran-correct.srt"
            return b"1\n00:00:01,000 --> 00:00:02,000\nok\n"

    monkeypatch.setattr(service, "_subtitles", _StubClient())

    path = await service.prepare_subtitle("tt0089881", "Ran")

    assert path is not None
    written = Path(path)
    assert written.name == "tt0089881-pt.srt"
    assert written.read_bytes().startswith(b"1\n")


async def test_prepare_subtitle_returns_none_when_nothing_available(
    tmp_path: Any, monkeypatch: Any
) -> None:
    service = _build_service(tmp_path)

    class _EmptyClient:
        async def get_subtitles(self, _type: str, _imdb_id: str) -> list[dict[str, Any]]:
            return []

        async def download_subtitle(self, url: str) -> bytes | None:
            raise AssertionError("não deveria baixar nada")

    monkeypatch.setattr(service, "_subtitles", _EmptyClient())

    path = await service.prepare_subtitle("tt0000000", "Desconhecido")

    assert path is None
