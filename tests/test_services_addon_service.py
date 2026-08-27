"""Testes de `app/services/addon_service.py`.

`AddonManager` é substituído por um dublê — o objetivo é testar a orquestração
(guardar resultados de `/find` para `/pick`, validar índice, delegar a
reprodução ao `PlaybackService`), não redundar com `test_addon_system_manager.py`.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import app.services.addon_service as addon_service_module
from app.addon_system.base import AddonHealth, SearchResult, StreamCandidate
from app.addon_system.manager import AddonInfo
from app.services.addon_service import AddonService
from app.services.exceptions import (
    InvalidSearchIndexError,
    NoStreamsAvailableError,
    TorrentTimeoutError,
)
from app.services.tmdb_service import TMDBMetadata


class _FakeManager:
    def __init__(self) -> None:
        self.discovered = False
        self.addons: list[AddonInfo] = []
        self.info_result: AddonInfo | None = None
        self.health_result = AddonHealth(healthy=True)
        self.enable_calls: list[str] = []
        self.disable_calls: list[str] = []
        self.reload_calls: list[str] = []
        self.uninstall_calls: list[str] = []
        self.search_results: list[SearchResult] = []
        self.stream_results: list[StreamCandidate] = []
        self.stream_results_by_addon: dict[str, list[StreamCandidate] | Exception] = {}
        self.get_streams_calls: list[tuple[str, str]] = []

    async def discover(self) -> None:
        self.discovered = True

    def list_addons(self) -> list[AddonInfo]:
        return self.addons

    def addon_info(self, name: str) -> AddonInfo:
        assert self.info_result is not None
        return self.info_result

    async def health(self, name: str) -> AddonHealth:
        return self.health_result

    async def enable(self, name: str) -> None:
        self.enable_calls.append(name)

    async def disable(self, name: str) -> None:
        self.disable_calls.append(name)

    async def reload(self, name: str) -> None:
        self.reload_calls.append(name)

    async def uninstall(self, name: str) -> None:
        self.uninstall_calls.append(name)

    async def search(self, query: str) -> list[SearchResult]:
        return self.search_results

    async def get_streams(self, addon_name: str, media_id: str) -> list[StreamCandidate]:
        self.get_streams_calls.append((addon_name, media_id))
        if addon_name in self.stream_results_by_addon:
            outcome = self.stream_results_by_addon[addon_name]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        return self.stream_results


class _FakeTorrentService:
    def __init__(self) -> None:
        self.resolve_calls: list[StreamCandidate] = []
        self.resolve_result: str | Exception = "/media/torrents/movie.mkv"

    async def resolve(self, candidate: StreamCandidate) -> str:
        self.resolve_calls.append(candidate)
        if isinstance(self.resolve_result, Exception):
            raise self.resolve_result
        return self.resolve_result


class _FakeTMDBService:
    def __init__(self, *, enabled: bool = False) -> None:
        self._enabled = enabled
        self.enrich_calls: list[tuple[str, int | None]] = []
        self.enrich_result: TMDBMetadata | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def enrich(self, title: str, year: int | None) -> TMDBMetadata | None:
        self.enrich_calls.append((title, year))
        return self.enrich_result


@pytest.fixture
def fake_manager(monkeypatch: pytest.MonkeyPatch) -> _FakeManager:
    manager = _FakeManager()
    monkeypatch.setattr(addon_service_module, "AddonManager", MagicMock(return_value=manager))
    return manager


@pytest.fixture
def playback() -> MagicMock:
    fake = MagicMock()
    fake.play = AsyncMock(return_value=3)
    return fake


@pytest.fixture
def torrent_service() -> _FakeTorrentService:
    return _FakeTorrentService()


@pytest.fixture
def tmdb_service() -> _FakeTMDBService:
    return _FakeTMDBService(enabled=False)


@pytest.fixture
def service(
    fake_manager: _FakeManager,
    playback: MagicMock,
    torrent_service: _FakeTorrentService,
    tmdb_service: _FakeTMDBService,
    settings: object,
) -> AddonService:
    return AddonService(settings, playback, torrent_service, tmdb_service)  # type: ignore[arg-type]


async def test_start_discovers_addons(service: AddonService, fake_manager: _FakeManager) -> None:
    await service.start()
    assert fake_manager.discovered is True


def test_list_addons_delegates_to_manager(
    service: AddonService, fake_manager: _FakeManager
) -> None:
    fake_manager.addons = [
        AddonInfo(name="archive_org", version="1.0.0", description="d", enabled=True)
    ]
    assert service.list_addons() == fake_manager.addons


async def test_enable_disable_reload_uninstall_delegate(
    service: AddonService, fake_manager: _FakeManager
) -> None:
    await service.enable("archive_org")
    await service.disable("archive_org")
    await service.reload("archive_org")
    await service.uninstall("archive_org")

    assert fake_manager.enable_calls == ["archive_org"]
    assert fake_manager.disable_calls == ["archive_org"]
    assert fake_manager.reload_calls == ["archive_org"]
    assert fake_manager.uninstall_calls == ["archive_org"]


async def test_find_stores_results_for_later_pick(
    service: AddonService, fake_manager: _FakeManager
) -> None:
    fake_manager.search_results = [
        SearchResult(media_id="1", title="Movie", addon_name="archive_org")
    ]
    results = await service.find("movie")
    assert results == fake_manager.search_results


# --- filtro TMDB-first / fuzzy-fallback (ver `AddonService._filter_results`) ---


def _metadata(title: str, original_title: str | None = None) -> TMDBMetadata:
    return TMDBMetadata(
        title=title,
        original_title=original_title,
        overview=None,
        poster_url=None,
        vote_average=None,
        genres=[],
        release_date=None,
        cast=[],
        backdrop_urls=[],
    )


async def test_find_filters_by_tmdb_title_when_enabled_and_matched(
    service: AddonService, fake_manager: _FakeManager, tmdb_service: _FakeTMDBService
) -> None:
    tmdb_service._enabled = True
    tmdb_service.enrich_result = _metadata("O Espetacular Homem-Aranha")
    fake_manager.search_results = [
        SearchResult(
            media_id="1",
            title="O Espetacular Homem-Aranha (2008) 1080p Dublado",
            addon_name="archive_org",
        ),
        SearchResult(
            media_id="2", title="Doblajes de Clásicos de la Diversión", addon_name="archive_org"
        ),
    ]

    results = await service.find("homem aranha")

    assert [r.media_id for r in results] == ["1"]
    assert tmdb_service.enrich_calls == [("homem aranha", None)]


async def test_find_falls_back_to_fuzzy_when_tmdb_disabled(
    service: AddonService, fake_manager: _FakeManager, tmdb_service: _FakeTMDBService
) -> None:
    assert tmdb_service.enabled is False
    fake_manager.search_results = [
        SearchResult(media_id="1", title="Homem Aranha Dublado", addon_name="archive_org"),
        SearchResult(
            media_id="2", title="Doblajes de Clásicos de la Diversión", addon_name="archive_org"
        ),
    ]

    results = await service.find("homem aranha")

    assert [r.media_id for r in results] == ["1"]
    assert tmdb_service.enrich_calls == []


async def test_find_falls_back_to_fuzzy_when_tmdb_finds_nothing(
    service: AddonService, fake_manager: _FakeManager, tmdb_service: _FakeTMDBService
) -> None:
    tmdb_service._enabled = True
    tmdb_service.enrich_result = None
    fake_manager.search_results = [
        SearchResult(media_id="1", title="Homem Aranha Dublado", addon_name="archive_org"),
        SearchResult(
            media_id="2", title="Doblajes de Clásicos de la Diversión", addon_name="archive_org"
        ),
    ]

    results = await service.find("homem aranha")

    assert [r.media_id for r in results] == ["1"]


async def test_find_tmdb_zero_matches_falls_back_to_fuzzy_vs_query(
    service: AddonService, fake_manager: _FakeManager, tmdb_service: _FakeTMDBService
) -> None:
    """TMDB confirma o filme, mas nenhum resultado de addon bate com o título
    do TMDB — cai pro fuzzy-vs-query, que ainda pode achar algo relevante."""
    tmdb_service._enabled = True
    tmdb_service.enrich_result = _metadata("Totalmente Diferente")
    fake_manager.search_results = [
        SearchResult(media_id="1", title="Homem-Aranha 2 1080p", addon_name="archive_org"),
    ]

    results = await service.find("homem aranha")

    assert [r.media_id for r in results] == ["1"]


async def test_find_safety_net_returns_raw_results_when_all_filters_empty(
    service: AddonService, fake_manager: _FakeManager, tmdb_service: _FakeTMDBService
) -> None:
    tmdb_service._enabled = True
    tmdb_service.enrich_result = _metadata("Totalmente Diferente")
    fake_manager.search_results = [
        SearchResult(media_id="1", title="Nada a ver com nada", addon_name="archive_org"),
    ]

    results = await service.find("outra busca qualquer")

    assert results == fake_manager.search_results


async def test_find_empty_raw_results_short_circuits(
    service: AddonService, fake_manager: _FakeManager, tmdb_service: _FakeTMDBService
) -> None:
    tmdb_service._enabled = True
    fake_manager.search_results = []

    results = await service.find("nada")

    assert results == []
    assert tmdb_service.enrich_calls == [("nada", None)]


async def test_last_metadata_reflects_last_find(
    service: AddonService, fake_manager: _FakeManager, tmdb_service: _FakeTMDBService
) -> None:
    assert service.last_metadata() is None

    tmdb_service._enabled = True
    tmdb_service.enrich_result = _metadata("Movie")
    fake_manager.search_results = [
        SearchResult(media_id="1", title="Movie", addon_name="archive_org")
    ]
    await service.find("movie")
    assert service.last_metadata() == tmdb_service.enrich_result

    tmdb_service.enrich_result = None
    await service.find("movie again")
    assert service.last_metadata() is None


async def test_pick_without_prior_find_raises(service: AddonService) -> None:
    with pytest.raises(InvalidSearchIndexError):
        await service.pick(1, requested_by=111)


async def test_pick_index_out_of_range_raises(
    service: AddonService, fake_manager: _FakeManager
) -> None:
    fake_manager.search_results = [
        SearchResult(media_id="1", title="Movie", addon_name="archive_org")
    ]
    await service.find("movie")

    with pytest.raises(InvalidSearchIndexError):
        await service.pick(5, requested_by=111)

    with pytest.raises(InvalidSearchIndexError):
        await service.pick(0, requested_by=111)


async def test_pick_no_streams_available_raises(
    service: AddonService, fake_manager: _FakeManager
) -> None:
    fake_manager.search_results = [
        SearchResult(media_id="1", title="Movie", addon_name="archive_org")
    ]
    await service.find("movie")
    fake_manager.stream_results = []

    with pytest.raises(NoStreamsAvailableError):
        await service.pick(1, requested_by=111)


async def test_pick_success_plays_best_stream(
    service: AddonService, fake_manager: _FakeManager, playback: MagicMock
) -> None:
    fake_manager.search_results = [
        SearchResult(media_id="1", title="Movie", addon_name="archive_org")
    ]
    await service.find("movie")
    fake_manager.stream_results = [
        StreamCandidate(url="https://example.com/best.mp4", title="best"),
        StreamCandidate(url="https://example.com/worse.mp4", title="worse"),
    ]

    position = await service.pick(1, requested_by=111)

    assert position == 3
    playback.play.assert_awaited_once_with("https://example.com/best.mp4", 111)


async def test_resolve_candidates_filters_deduplicates_and_ranks(
    service: AddonService, fake_manager: _FakeManager
) -> None:
    service._last_metadata = TMDBMetadata(
        title="The Matrix",
        original_title="The Matrix",
        overview=None,
        poster_url=None,
        vote_average=8.2,
        genres=["Action"],
        release_date="1999-03-31",
        cast=[],
        backdrop_urls=[],
    )
    service._last_results = [
        SearchResult(media_id="tt0133093", title="The Matrix", year=1999, addon_name="stremio")
    ]
    fake_manager.stream_results_by_addon = {
        "stremio": [
            StreamCandidate(title="The Matrix 1999 4K", quality="2160p", seeds=500),
            StreamCandidate(title="The Matrix Reloaded 2003", quality="1080p", seeds=900),
            StreamCandidate(title="The Matrix 1999", quality="720p", seeds=300),
            StreamCandidate(title="The Matrix 1999", quality="1080p", seeds=None),
            StreamCandidate(title="The Matrix 1999 Dublado", quality="1080p", seeds=20),
            StreamCandidate(title="The Matrix 1999", quality="1080p", seeds=200),
            StreamCandidate(title="The Matrix 1999", quality="1080p", seeds=200),
            StreamCandidate(
                title="The Matrix 1999",
                quality="1080p",
                seeds=999,
                size_bytes=4 * 1024**3 + 1,
            ),
            StreamCandidate(title="The Matrix 1999", quality=None, seeds=1000),
            StreamCandidate(title="The Matrix 1999 2K", quality=None, seeds=1000),
        ]
    }

    candidates = await service.resolve_candidates()

    assert [(candidate.quality, candidate.seeds) for _, _, candidate in candidates] == [
        ("720p", 300),
        ("1080p", 200),
        ("1080p", 20),
        ("1080p", None),
    ]


async def test_resolve_candidates_uses_quality_and_language_as_seed_tiebreakers(
    service: AddonService, fake_manager: _FakeManager
) -> None:
    service._last_metadata = TMDBMetadata(
        title="The Matrix",
        original_title="The Matrix",
        overview=None,
        poster_url=None,
        vote_average=8.2,
        genres=["Action"],
        release_date="1999-03-31",
        cast=[],
        backdrop_urls=[],
    )
    service._last_results = [
        SearchResult(media_id="tt0133093", title="The Matrix", year=1999, addon_name="stremio")
    ]
    fake_manager.stream_results_by_addon = {
        "stremio": [
            StreamCandidate(title="The Matrix 1999 Legendado", quality="1080p", seeds=100),
            StreamCandidate(title="The Matrix 1999 Dual Audio", quality="1080p", seeds=100),
            StreamCandidate(title="The Matrix 1999 Dublado", quality="1080p", seeds=100),
            StreamCandidate(title="The Matrix 1999 Dublado", quality="720p", seeds=100),
        ]
    }

    candidates = await service.resolve_candidates()

    assert [(candidate.quality, candidate.title) for _, _, candidate in candidates] == [
        ("1080p", "The Matrix 1999 Dublado"),
        ("1080p", "The Matrix 1999 Dual Audio"),
        ("1080p", "The Matrix 1999 Legendado"),
        ("720p", "The Matrix 1999 Dublado"),
    ]


async def test_resolve_candidates_deduplicates_equivalent_sources(
    service: AddonService, fake_manager: _FakeManager
) -> None:
    fake_manager.search_results = [
        SearchResult(media_id="1", title="Movie A", addon_name="archive_org"),
        SearchResult(media_id="2", title="Movie A", addon_name="archive_org"),
        SearchResult(media_id="3", title="Movie A", addon_name="stremio"),
    ]
    await service.find("movie")
    fake_manager.stream_results_by_addon = {
        "archive_org": [
            StreamCandidate(url="https://a.example/1.mp4", title="Movie A", quality="720p")
        ],
        "stremio": [
            StreamCandidate(url="https://s.example/1.mp4", title="Movie A", quality="1080p")
        ],
    }

    candidates = await service.resolve_candidates()

    assert fake_manager.get_streams_calls == [
        ("archive_org", "1"),
        ("archive_org", "2"),
        ("stremio", "3"),
    ]
    assert [result.addon_name for _, result, _ in candidates] == ["stremio", "archive_org"]


async def test_resolve_candidates_isolates_addon_failure(
    service: AddonService, fake_manager: _FakeManager
) -> None:
    fake_manager.search_results = [
        SearchResult(media_id="1", title="Movie A", addon_name="archive_org"),
        SearchResult(media_id="3", title="Movie A", addon_name="stremio"),
    ]
    await service.find("movie")
    fake_manager.stream_results_by_addon = {
        "archive_org": RuntimeError("boom"),
        "stremio": [
            StreamCandidate(url="https://s.example/1.mp4", title="Movie A", quality="1080p")
        ],
    }

    candidates = await service.resolve_candidates()

    assert [result.addon_name for _, result, _ in candidates] == ["stremio"]


async def test_resolve_candidates_skips_addon_with_no_streams(
    service: AddonService, fake_manager: _FakeManager
) -> None:
    fake_manager.search_results = [
        SearchResult(media_id="1", title="Movie A", addon_name="archive_org"),
        SearchResult(media_id="3", title="Movie A", addon_name="stremio"),
    ]
    await service.find("movie")
    fake_manager.stream_results_by_addon = {
        "archive_org": [],
        "stremio": [
            StreamCandidate(url="https://s.example/1.mp4", title="Movie A", quality="1080p")
        ],
    }

    candidates = await service.resolve_candidates()

    assert [result.addon_name for _, result, _ in candidates] == ["stremio"]


async def test_pick_candidate_success_plays_and_returns_addon_name(
    service: AddonService, fake_manager: _FakeManager, playback: MagicMock
) -> None:
    fake_manager.search_results = [
        SearchResult(media_id="1", title="Movie A", addon_name="archive_org"),
    ]
    await service.find("movie")
    fake_manager.stream_results_by_addon = {
        "archive_org": [
            StreamCandidate(url="https://a.example/1.mp4", title="Movie A", quality="1080p")
        ],
    }
    candidates = await service.resolve_candidates()
    token = candidates[0][0]

    addon_name, position = await service.pick_candidate(token, requested_by=111)

    assert addon_name == "archive_org"
    assert position == 3
    playback.play.assert_awaited_once_with("https://a.example/1.mp4", 111)


async def test_play_resolved_candidate_does_not_depend_on_global_tokens(
    service: AddonService, playback: MagicMock
) -> None:
    result = SearchResult(media_id="1", title="Movie A", addon_name="archive_org")
    candidate = StreamCandidate(url="https://a.example/1.mp4", title="Movie A", quality="1080p")

    addon_name, position = await service.play_resolved_candidate(result, candidate, 222)

    assert (addon_name, position) == ("archive_org", 3)
    playback.play.assert_awaited_once_with("https://a.example/1.mp4", 222)


async def test_pick_candidate_unknown_token_raises(service: AddonService) -> None:
    with pytest.raises(InvalidSearchIndexError):
        await service.pick_candidate("0", requested_by=111)


async def test_pick_candidate_token_invalidated_by_new_find(
    service: AddonService, fake_manager: _FakeManager
) -> None:
    fake_manager.search_results = [
        SearchResult(media_id="1", title="Movie A", addon_name="archive_org"),
    ]
    await service.find("movie")
    fake_manager.stream_results_by_addon = {
        "archive_org": [
            StreamCandidate(url="https://a.example/1.mp4", title="Movie A", quality="1080p")
        ],
    }
    candidates = await service.resolve_candidates()
    token = candidates[0][0]

    await service.find("movie again")

    with pytest.raises(InvalidSearchIndexError):
        await service.pick_candidate(token, requested_by=111)


# ---------------------------------------------------------------------------
# Resolução via torrent (candidatos sem url, só infoHash)
# ---------------------------------------------------------------------------


async def test_pick_resolves_torrent_candidate_and_plays_path(
    service: AddonService,
    fake_manager: _FakeManager,
    playback: MagicMock,
    torrent_service: _FakeTorrentService,
) -> None:
    fake_manager.search_results = [
        SearchResult(media_id="1", title="Movie", addon_name="archive_org")
    ]
    await service.find("movie")
    torrent_candidate = StreamCandidate(
        url=None, info_hash="abc123", title="Movie A", quality="1080p"
    )
    fake_manager.stream_results = [torrent_candidate]
    torrent_service.resolve_result = "/media/torrents/movie.mkv"

    position = await service.pick(1, requested_by=111)

    assert position == 3
    assert torrent_service.resolve_calls == [torrent_candidate]
    playback.play.assert_awaited_once_with("/media/torrents/movie.mkv", 111)


async def test_pick_falls_back_to_next_candidate_on_torrent_timeout(
    service: AddonService,
    fake_manager: _FakeManager,
    playback: MagicMock,
    torrent_service: _FakeTorrentService,
) -> None:
    fake_manager.search_results = [
        SearchResult(media_id="1", title="Movie", addon_name="archive_org")
    ]
    await service.find("movie")
    timed_out = StreamCandidate(url=None, info_hash="timeout-hash", title="slow")
    fallback = StreamCandidate(url="https://example.com/fallback.mp4", title="fallback")
    fake_manager.stream_results = [timed_out, fallback]

    calls = {"count": 0}

    async def resolve(candidate: StreamCandidate) -> str:
        calls["count"] += 1
        if candidate is timed_out:
            raise TorrentTimeoutError("timeout")
        return "/media/torrents/should-not-happen.mkv"

    torrent_service.resolve = resolve  # type: ignore[method-assign]

    position = await service.pick(1, requested_by=111)

    assert position == 3
    assert calls["count"] == 1
    playback.play.assert_awaited_once_with("https://example.com/fallback.mp4", 111)


async def test_pick_raises_when_all_torrent_candidates_timeout(
    service: AddonService,
    fake_manager: _FakeManager,
    torrent_service: _FakeTorrentService,
) -> None:
    fake_manager.search_results = [
        SearchResult(media_id="1", title="Movie", addon_name="archive_org")
    ]
    await service.find("movie")
    fake_manager.stream_results = [
        StreamCandidate(url=None, info_hash="hash-1", title="a"),
        StreamCandidate(url=None, info_hash="hash-2", title="b"),
    ]
    torrent_service.resolve_result = TorrentTimeoutError("timeout")

    with pytest.raises(NoStreamsAvailableError):
        await service.pick(1, requested_by=111)


async def test_pick_candidate_resolves_torrent_and_plays_path(
    service: AddonService,
    fake_manager: _FakeManager,
    playback: MagicMock,
    torrent_service: _FakeTorrentService,
) -> None:
    fake_manager.search_results = [
        SearchResult(media_id="1", title="Movie A", addon_name="archive_org"),
    ]
    await service.find("movie")
    torrent_candidate = StreamCandidate(
        url=None, info_hash="abc123", title="Movie A", quality="1080p"
    )
    fake_manager.stream_results_by_addon = {"archive_org": [torrent_candidate]}
    candidates = await service.resolve_candidates()
    token = candidates[0][0]
    torrent_service.resolve_result = "/media/torrents/movie.mkv"

    addon_name, position = await service.pick_candidate(token, requested_by=111)

    assert addon_name == "archive_org"
    assert position == 3
    playback.play.assert_awaited_once_with("/media/torrents/movie.mkv", 111)


async def test_pick_candidate_does_not_fall_back_on_torrent_timeout(
    service: AddonService,
    fake_manager: _FakeManager,
    torrent_service: _FakeTorrentService,
) -> None:
    fake_manager.search_results = [
        SearchResult(media_id="1", title="Movie A", addon_name="archive_org"),
    ]
    await service.find("movie")
    torrent_candidate = StreamCandidate(
        url=None, info_hash="abc123", title="Movie A", quality="1080p"
    )
    fake_manager.stream_results_by_addon = {"archive_org": [torrent_candidate]}
    candidates = await service.resolve_candidates()
    token = candidates[0][0]
    torrent_service.resolve_result = TorrentTimeoutError("timeout")

    with pytest.raises(TorrentTimeoutError):
        await service.pick_candidate(token, requested_by=111)
