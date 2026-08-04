"""Testes do addon `stremio` (`addons/stremio/plugin.py`).

Carrega o módulo do mesmo jeito que `AddonLoader` faria (via `importlib`, sem
depender de `addons/` estar no `sys.path`) e substitui o `httpx.AsyncClient`
interno de cada `StremioAddonClient` registrado por um dublê — nenhum destes
testes bate na rede real. Mesmo padrão de `test_addon_archive_org.py`.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

_PLUGIN_PATH = Path(__file__).resolve().parents[1] / "addons" / "stremio" / "plugin.py"

_CONFIG = {
    "upstreams": [
        {
            "name": "cinemeta",
            "base_url": "https://v3-cinemeta.strem.io",
            "catalogs": [{"type": "movie", "id": "top"}],
        }
    ]
}


def _load_plugin_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_test_stremio_plugin", _PLUGIN_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def plugin_module() -> ModuleType:
    return _load_plugin_module()


class _FakeResponse:
    def __init__(self, json_data: Any = None, status_error: bool = False) -> None:
        self._json_data = json_data
        self._status_error = status_error
        self.status_code = 500 if status_error else 200

    def raise_for_status(self) -> None:
        if self._status_error:
            raise httpx.HTTPStatusError(
                "error", request=MagicMock(), response=MagicMock(status_code=self.status_code)
            )

    def json(self) -> Any:
        return self._json_data


@pytest.fixture
def addon(plugin_module: ModuleType) -> Any:
    instance = plugin_module.Addon(config=_CONFIG)
    client = instance._registry.get("cinemeta")
    assert client is not None
    client._client = AsyncMock()
    return instance


def _fake_client(addon: Any) -> Any:
    client = addon._registry.get("cinemeta")
    assert client is not None
    return client


async def test_addon_without_config_has_no_upstreams(plugin_module: ModuleType) -> None:
    instance = plugin_module.Addon()

    assert await instance.search("anything") == []


async def test_search_aggregates_metas_from_configured_catalogs(addon: Any) -> None:
    client = _fake_client(addon)
    client._client.get.return_value = _FakeResponse(
        {
            "metas": [
                {"id": "tt1254207", "name": "Big Buck Bunny", "releaseInfo": "2008"},
                {"id": "tt0111161", "name": "The Shawshank Redemption", "year": 1994},
            ]
        }
    )

    results = await addon.search("bunny")

    assert len(results) == 2
    assert results[0].media_id == "cinemeta:movie:tt1254207"
    assert results[0].title == "Big Buck Bunny"
    assert results[0].year == 2008
    assert results[1].media_id == "cinemeta:movie:tt0111161"
    assert results[1].year == 1994


async def test_search_skips_metas_without_id(addon: Any) -> None:
    client = _fake_client(addon)
    client._client.get.return_value = _FakeResponse({"metas": [{"name": "No id here"}]})

    results = await addon.search("bunny")

    assert results == []


async def test_search_sends_query_in_catalog_url(addon: Any) -> None:
    client = _fake_client(addon)
    client._client.get.return_value = _FakeResponse({"metas": []})

    await addon.search("the matrix")

    requested_url = client._client.get.call_args.args[0]
    assert requested_url == (
        "https://v3-cinemeta.strem.io/catalog/movie/top/search=the%20matrix.json"
    )


async def test_get_metadata_maps_fields(addon: Any) -> None:
    client = _fake_client(addon)
    client._client.get.return_value = _FakeResponse(
        {"meta": {"name": "Inception", "description": "A thief.", "releaseInfo": "2010"}}
    )

    metadata = await addon.get_metadata("cinemeta:movie:tt1375666")

    assert metadata.media_id == "cinemeta:movie:tt1375666"
    assert metadata.title == "Inception"
    assert metadata.description == "A thief."
    assert metadata.year == 2010


async def test_get_metadata_unknown_upstream_falls_back_to_media_id(addon: Any) -> None:
    metadata = await addon.get_metadata("unknown:movie:tt1234567")

    assert metadata.title == "unknown:movie:tt1234567"


async def test_get_streams_maps_and_skips_entries_without_url(addon: Any) -> None:
    client = _fake_client(addon)
    client._client.get.return_value = _FakeResponse(
        {
            "streams": [
                {"url": "https://cdn/file.mp4", "title": "720p", "name": "CDN"},
                {"infoHash": "abc123", "title": "torrent only"},
            ]
        }
    )

    streams = await addon.get_streams("cinemeta:movie:tt1254207")

    assert len(streams) == 1
    assert streams[0].url == "https://cdn/file.mp4"
    assert streams[0].title == "720p"
    assert streams[0].quality == "CDN"


async def test_get_streams_unknown_upstream_returns_empty(addon: Any) -> None:
    streams = await addon.get_streams("unknown:movie:tt1234567")

    assert streams == []


async def test_health_unhealthy_without_upstreams(plugin_module: ModuleType) -> None:
    instance = plugin_module.Addon()

    health = await instance.health()

    assert health.healthy is False
    assert health.detail is not None


async def test_health_healthy_when_all_upstreams_respond(addon: Any) -> None:
    client = _fake_client(addon)
    client._client.get.return_value = _FakeResponse({"id": "cinemeta", "name": "Cinemeta"})

    health = await addon.health()

    assert health.healthy is True


async def test_health_unhealthy_when_upstream_manifest_unreachable(addon: Any) -> None:
    client = _fake_client(addon)

    async def _raise(*args: Any, **kwargs: Any) -> Any:
        raise httpx.ConnectError("unreachable")

    client._client.get = _raise

    health = await addon.health()

    assert health.healthy is False
    assert "cinemeta" in (health.detail or "")


async def test_close_closes_all_registered_clients(addon: Any) -> None:
    client = _fake_client(addon)

    await addon.close()

    client._client.aclose.assert_awaited_once()
