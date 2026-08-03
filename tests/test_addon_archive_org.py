"""Testes do addon `archive_org` (`addons/archive_org/plugin.py`).

Carrega o módulo do mesmo jeito que `AddonLoader` faria (via `importlib`,
sem depender de `addons/` estar no `sys.path`), e substitui o `httpx.AsyncClient`
interno por um dublê — nenhum destes testes bate na rede real. A verificação
end-to-end contra a API real do archive.org já foi feita manualmente (curl)
antes de escrever o addon; aqui o objetivo é travar o comportamento do código.
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

_PLUGIN_PATH = Path(__file__).resolve().parents[1] / "addons" / "archive_org" / "plugin.py"


def _load_plugin_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_test_archive_org_plugin", _PLUGIN_PATH)
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

    def raise_for_status(self) -> None:
        if self._status_error:
            raise httpx.HTTPStatusError("error", request=MagicMock(), response=MagicMock())

    def json(self) -> Any:
        return self._json_data


@pytest.fixture
def addon(plugin_module: ModuleType) -> Any:
    instance = plugin_module.Addon()
    instance._client = AsyncMock()
    return instance


async def test_search_filters_and_maps_docs(addon: Any) -> None:
    addon._client.get.return_value = _FakeResponse(
        {
            "response": {
                "docs": [
                    {"identifier": "night-of-the-living-dead-1968", "title": "Night", "year": 1968},
                    {"identifier": "no-title-doc"},
                    {"title": "missing identifier, should be skipped"},
                ]
            }
        }
    )

    results = await addon.search("night of the living dead")

    assert len(results) == 2
    assert results[0].media_id == "night-of-the-living-dead-1968"
    assert results[0].title == "Night"
    assert results[0].year == 1968
    assert results[1].media_id == "no-title-doc"
    assert results[1].title == "no-title-doc"  # cai pro identifier quando falta title


async def test_search_sends_public_domain_license_filter(addon: Any) -> None:
    addon._client.get.return_value = _FakeResponse({"response": {"docs": []}})

    await addon.search("dracula")

    _, kwargs = addon._client.get.call_args
    query = kwargs["params"]["q"]
    assert "licenseurl:(*publicdomain* OR *creativecommons*)" in query
    assert "mediatype:(movies)" in query


async def test_get_metadata_maps_fields(addon: Any) -> None:
    addon._client.get.return_value = _FakeResponse(
        {
            "metadata": {
                "title": "Night of the Living Dead",
                "description": "A classic.",
                "year": "1968",
            }
        }
    )

    metadata = await addon.get_metadata("night-of-the-living-dead-1968")

    assert metadata.media_id == "night-of-the-living-dead-1968"
    assert metadata.title == "Night of the Living Dead"
    assert metadata.description == "A classic."


async def test_get_metadata_falls_back_to_media_id_when_no_title(addon: Any) -> None:
    addon._client.get.return_value = _FakeResponse({"metadata": {}})

    metadata = await addon.get_metadata("some-id")

    assert metadata.title == "some-id"


async def test_get_streams_filters_by_format_and_extension(addon: Any) -> None:
    addon._client.get.return_value = _FakeResponse(
        {
            "files": [
                {"name": "movie.mp4", "format": "h.264"},
                {"name": "movie_512kb.mp4", "format": "512Kb MPEG4"},
                {"name": "movie.ogv", "format": "Ogg Video"},
                {"name": "movie.mkv", "format": "Matroska"},
                {"name": "movie_thumbs.jpg", "format": "JPEG"},
            ]
        }
    )

    streams = await addon.get_streams("some-id")

    urls = {c.url for c in streams}
    assert any(u.endswith("movie.mp4") for u in urls)
    assert any(u.endswith("movie_512kb.mp4") for u in urls)
    assert not any(u.endswith("movie.ogv") for u in urls)
    assert not any(u.endswith("movie.mkv") for u in urls)  # .mkv não bate .mp4/.m4v
    assert not any(u.endswith(".jpg") for u in urls)


async def test_get_streams_prioritizes_h264(addon: Any) -> None:
    addon._client.get.return_value = _FakeResponse(
        {
            "files": [
                {"name": "movie_512kb.mp4", "format": "512Kb MPEG4"},
                {"name": "movie.mp4", "format": "h.264"},
            ]
        }
    )

    streams = await addon.get_streams("some-id")

    assert streams[0].quality == "h.264"


async def test_get_streams_empty_when_no_video_files(addon: Any) -> None:
    addon._client.get.return_value = _FakeResponse(
        {"files": [{"name": "cover.jpg", "format": "JPEG"}]}
    )

    streams = await addon.get_streams("some-id")

    assert streams == []


async def test_health_returns_healthy_on_success(addon: Any) -> None:
    addon._client.get.return_value = _FakeResponse()

    health = await addon.health()

    assert health.healthy is True


async def test_health_returns_unhealthy_on_http_error(addon: Any) -> None:
    async def _raise(*args: Any, **kwargs: Any) -> Any:
        raise httpx.ConnectError("unreachable")

    addon._client.get = _raise

    health = await addon.health()

    assert health.healthy is False
    assert health.detail is not None


async def test_close_closes_http_client(addon: Any) -> None:
    await addon.close()
    addon._client.aclose.assert_awaited_once()


def test_addon_reads_timeout_from_config(plugin_module: ModuleType) -> None:
    instance = plugin_module.Addon(config={"timeout_seconds": 3.0})
    assert instance.config["timeout_seconds"] == 3.0
