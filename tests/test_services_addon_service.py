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
from app.services.exceptions import InvalidSearchIndexError, NoStreamsAvailableError


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
        return self.stream_results


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
def service(fake_manager: _FakeManager, playback: MagicMock, settings: object) -> AddonService:
    return AddonService(settings, playback)  # type: ignore[arg-type]


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
