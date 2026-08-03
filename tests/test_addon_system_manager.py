"""Testes de `app/addon_system/manager.py` (AddonManager).

Usa um `AddonLoader` falso (mesma interface: `discover_names`/`load`) para
isolar a lógica de orquestração (habilitar/desabilitar/recarregar/remover,
agregação de busca com isolamento de falha/timeout) da importação dinâmica
real, já coberta em `test_addon_system_loader.py`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest

from app.addon_system.base import BaseAddon, Metadata, SearchResult, StreamCandidate
from app.addon_system.exceptions import (
    AddonError,
    AddonLoadError,
    AddonNotFoundError,
    AddonTimeoutError,
)
from app.addon_system.loader import LoadedAddon
from app.addon_system.manager import AddonManager
from app.addon_system.manifest import AddonManifest
from app.config.settings import Settings


class _FakeAddonInstance(BaseAddon):
    name = "fake"
    version = "1.0.0"

    def __init__(self, config: dict[str, object] | None = None) -> None:
        super().__init__(config)
        self.search_results: list[SearchResult] = []
        self.search_exception: Exception | None = None
        self.search_delay: float = 0.0
        self.stream_results: list[StreamCandidate] = []
        self.closed = False

    async def search(self, query: str) -> list[SearchResult]:
        if self.search_delay:
            await asyncio.sleep(self.search_delay)
        if self.search_exception is not None:
            raise self.search_exception
        return self.search_results

    async def get_metadata(self, media_id: str) -> Metadata:
        return Metadata(media_id=media_id, title="t")

    async def get_streams(self, media_id: str) -> list[StreamCandidate]:
        return self.stream_results

    async def close(self) -> None:
        self.closed = True


class _FakeLoader:
    def __init__(self) -> None:
        self.instances: dict[str, _FakeAddonInstance] = {}
        self.load_exception: dict[str, Exception] = {}
        self.load_count: dict[str, int] = {}

    def discover_names(self) -> list[str]:
        return sorted(self.instances)

    def load(self, name: str) -> LoadedAddon:
        self.load_count[name] = self.load_count.get(name, 0) + 1
        if name in self.load_exception:
            raise self.load_exception[name]
        instance = self.instances[name]
        manifest = AddonManifest(name=name, version=instance.version, description=f"{name} desc")
        return LoadedAddon(instance=instance, manifest=manifest, path=Path(f"/fake/{name}"))


@pytest.fixture
def loader() -> _FakeLoader:
    return _FakeLoader()


@pytest.fixture
def manager(
    make_settings: Callable[..., Settings], loader: _FakeLoader, tmp_path: Path
) -> AddonManager:
    settings = make_settings(
        addons_state_path=tmp_path / "addons_state.json",
        addon_search_timeout_seconds=0.05,
        addon_streams_timeout_seconds=0.05,
        addon_search_cache_ttl_seconds=60.0,
    )
    return AddonManager(settings, loader=loader)  # type: ignore[arg-type]


async def test_discover_loads_all_addons_enabled_by_default(
    manager: AddonManager, loader: _FakeLoader
) -> None:
    loader.instances["alpha"] = _FakeAddonInstance()
    loader.instances["beta"] = _FakeAddonInstance()

    await manager.discover()

    infos = manager.list_addons()
    assert [info.name for info in infos] == ["alpha", "beta"]
    assert all(info.enabled for info in infos)


async def test_discover_skips_addon_that_fails_to_load(
    manager: AddonManager, loader: _FakeLoader
) -> None:
    loader.instances["good"] = _FakeAddonInstance()
    loader.load_exception["bad"] = AddonLoadError("boom")
    loader.instances["bad"] = _FakeAddonInstance()  # discover_names sees both

    await manager.discover()

    assert [info.name for info in manager.list_addons()] == ["good"]


async def test_addon_info_raises_for_unknown_addon(manager: AddonManager) -> None:
    with pytest.raises(AddonNotFoundError):
        manager.addon_info("nope")


async def test_enable_disable_roundtrip_persists(
    manager: AddonManager, loader: _FakeLoader, tmp_path: Path
) -> None:
    loader.instances["alpha"] = _FakeAddonInstance()
    await manager.discover()

    await manager.disable("alpha")
    assert manager.addon_info("alpha").enabled is False

    await manager.enable("alpha")
    assert manager.addon_info("alpha").enabled is True
    assert (tmp_path / "addons_state.json").is_file()


async def test_enable_unknown_addon_raises(manager: AddonManager) -> None:
    with pytest.raises(AddonNotFoundError):
        await manager.enable("nope")


async def test_health_returns_unhealthy_on_addon_exception(
    manager: AddonManager, loader: _FakeLoader
) -> None:
    instance = _FakeAddonInstance()

    async def _broken_health() -> object:
        raise RuntimeError("down")

    instance.health = _broken_health  # type: ignore[method-assign]
    loader.instances["alpha"] = instance
    await manager.discover()

    health = await manager.health("alpha")

    assert health.healthy is False
    assert "down" in (health.detail or "")


async def test_reload_replaces_instance_and_closes_old_one(
    manager: AddonManager, loader: _FakeLoader
) -> None:
    old_instance = _FakeAddonInstance()
    loader.instances["alpha"] = old_instance
    await manager.discover()
    await manager.disable("alpha")

    new_instance = _FakeAddonInstance()
    loader.instances["alpha"] = new_instance

    await manager.reload("alpha")

    assert old_instance.closed is True
    assert manager.addon_info("alpha").enabled is False  # preserva estado


async def test_reload_keeps_old_addon_if_reload_fails(
    manager: AddonManager, loader: _FakeLoader
) -> None:
    old_instance = _FakeAddonInstance()
    old_instance.search_results = [SearchResult(media_id="1", title="old")]
    loader.instances["alpha"] = old_instance
    await manager.discover()

    loader.load_exception["alpha"] = AddonLoadError("boom")

    with pytest.raises(AddonLoadError):
        await manager.reload("alpha")

    assert old_instance.closed is False
    results = await manager.search("q")
    assert results[0].title == "old"


async def test_reload_unknown_addon_raises(manager: AddonManager) -> None:
    with pytest.raises(AddonNotFoundError):
        await manager.reload("nope")


async def test_uninstall_removes_addon_and_closes_instance(
    manager: AddonManager, loader: _FakeLoader, tmp_path: Path
) -> None:
    instance = _FakeAddonInstance()
    loader.instances["alpha"] = instance
    await manager.discover()

    addon_dir = tmp_path / "addons" / "alpha"
    addon_dir.mkdir(parents=True)
    (addon_dir / "marker.txt").write_text("x", encoding="utf-8")
    # patch caminho real usado pelo manager
    manager._settings = manager._settings.model_copy(update={"addons_path": tmp_path / "addons"})

    await manager.uninstall("alpha")

    assert instance.closed is True
    with pytest.raises(AddonNotFoundError):
        manager.addon_info("alpha")
    assert not addon_dir.is_dir()


async def test_search_aggregates_only_enabled_addons(
    manager: AddonManager, loader: _FakeLoader
) -> None:
    enabled = _FakeAddonInstance()
    enabled.search_results = [SearchResult(media_id="1", title="Enabled Result")]
    disabled = _FakeAddonInstance()
    disabled.search_results = [SearchResult(media_id="2", title="Disabled Result")]
    loader.instances["enabled_addon"] = enabled
    loader.instances["disabled_addon"] = disabled
    await manager.discover()
    await manager.disable("disabled_addon")

    results = await manager.search("query")

    titles = [r.title for r in results]
    assert titles == ["Enabled Result"]
    assert results[0].addon_name == "enabled_addon"


async def test_search_is_cached(manager: AddonManager, loader: _FakeLoader) -> None:
    instance = _FakeAddonInstance()
    instance.search_results = [SearchResult(media_id="1", title="First")]
    loader.instances["alpha"] = instance
    await manager.discover()

    first = await manager.search("query")
    instance.search_results = [SearchResult(media_id="2", title="Second")]
    second = await manager.search("query")

    assert first == second


async def test_search_isolates_addon_that_raises(
    manager: AddonManager, loader: _FakeLoader
) -> None:
    broken = _FakeAddonInstance()
    broken.search_exception = RuntimeError("boom")
    healthy = _FakeAddonInstance()
    healthy.search_results = [SearchResult(media_id="1", title="OK")]
    loader.instances["broken_addon"] = broken
    loader.instances["healthy_addon"] = healthy
    await manager.discover()

    results = await manager.search("query")

    assert [r.title for r in results] == ["OK"]


async def test_search_isolates_addon_that_times_out(
    manager: AddonManager, loader: _FakeLoader
) -> None:
    slow = _FakeAddonInstance()
    slow.search_delay = 1.0  # bem maior que addon_search_timeout_seconds=0.05
    loader.instances["slow_addon"] = slow
    await manager.discover()

    results = await manager.search("query")

    assert results == []


async def test_get_streams_returns_candidates(manager: AddonManager, loader: _FakeLoader) -> None:
    instance = _FakeAddonInstance()
    instance.stream_results = [StreamCandidate(url="https://example.com/a.mp4", title="a")]
    loader.instances["alpha"] = instance
    await manager.discover()

    streams = await manager.get_streams("alpha", "media-1")

    assert streams[0].url == "https://example.com/a.mp4"


async def test_get_streams_unknown_addon_raises(manager: AddonManager) -> None:
    with pytest.raises(AddonNotFoundError):
        await manager.get_streams("nope", "media-1")


async def test_get_streams_timeout_raises(manager: AddonManager, loader: _FakeLoader) -> None:
    instance = _FakeAddonInstance()

    async def _slow_get_streams(media_id: str) -> list[StreamCandidate]:
        await asyncio.sleep(1.0)
        return []

    instance.get_streams = _slow_get_streams  # type: ignore[method-assign]
    loader.instances["alpha"] = instance
    await manager.discover()

    with pytest.raises(AddonTimeoutError):
        await manager.get_streams("alpha", "media-1")


async def test_addon_error_is_base_of_specific_exceptions() -> None:
    assert issubclass(AddonNotFoundError, AddonError)
    assert issubclass(AddonLoadError, AddonError)
    assert issubclass(AddonTimeoutError, AddonError)
