"""Testes de `app/services/stremio_client.py`.

Nada disso bate na rede real: o `httpx.AsyncClient` interno (`instance._client`)
é substituído por um `AsyncMock`, e as respostas são simuladas por `_FakeResponse`,
mesmo padrão de `test_addon_archive_org.py`.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.services.stremio_client import StremioAddonClient, StremioAddonRegistry


class _FakeResponse:
    """Dublê mínimo de `httpx.Response`: `raise_for_status()` + `json()`."""

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
def client() -> StremioAddonClient:
    instance = StremioAddonClient("http://localhost:7000/")
    instance._client = AsyncMock()
    return instance


async def test_manifest_returns_full_dict(client: StremioAddonClient) -> None:
    client._client.get.return_value = _FakeResponse(
        {"id": "test-addon", "name": "Test", "types": ["movie"], "resources": ["stream"]}
    )

    manifest = await client.get_manifest()

    assert manifest["id"] == "test-addon"
    assert manifest["name"] == "Test"


async def test_get_manifest_returns_empty_dict_on_network_error(
    client: StremioAddonClient,
) -> None:
    async def _raise(*args: Any, **kwargs: Any) -> Any:
        raise httpx.ConnectError("unreachable")

    client._client.get = _raise

    result = await client.get_manifest()

    assert result == {}


async def test_get_catalog_returns_metas_list(client: StremioAddonClient) -> None:
    client._client.get.return_value = _FakeResponse(
        {"metas": [{"id": "1", "title": "Movie"}, {"id": "2", "title": "Movie 2"}]}
    )

    metas = await client.get_catalog("movie", "top")

    assert len(metas) == 2
    assert metas[0]["id"] == "1"
    assert metas[1]["title"] == "Movie 2"


async def test_get_catalog_includes_extra_filters_in_url(
    client: StremioAddonClient,
) -> None:
    client._client.get.return_value = _FakeResponse({"metas": []})

    await client.get_catalog("movie", "top", extra={"genre": "Horror", "skip": 20})

    requested_url = client._client.get.call_args.args[0]
    assert requested_url == "http://localhost:7000/catalog/movie/top/genre=Horror&skip=20.json"


async def test_get_catalog_returns_empty_on_non_list_metas(
    client: StremioAddonClient,
) -> None:
    client._client.get.return_value = _FakeResponse({"metas": "not a list"})

    metas = await client.get_catalog("movie", "top")

    assert metas == []


async def test_get_catalog_returns_empty_on_404(client: StremioAddonClient) -> None:
    client._client.get.return_value = _FakeResponse({}, status_error=True)

    metas = await client.get_catalog("movie", "top")

    assert metas == []


async def test_get_streams_returns_streams_list(client: StremioAddonClient) -> None:
    client._client.get.return_value = _FakeResponse(
        {"streams": [{"url": "http://cdn/file.mp4", "title": "720p"}]}
    )

    streams = await client.get_streams("movie", "tt1234567")

    assert len(streams) == 1
    assert streams[0]["url"] == "http://cdn/file.mp4"
    assert streams[0]["title"] == "720p"


async def test_get_streams_returns_empty_on_http_error(
    client: StremioAddonClient,
) -> None:
    client._client.get.return_value = _FakeResponse({}, status_error=True)

    streams = await client.get_streams("movie", "tt1234567")

    assert streams == []


async def test_get_streams_returns_empty_on_timeout(
    client: StremioAddonClient,
) -> None:
    async def _raise(*args: Any, **kwargs: Any) -> Any:
        raise httpx.TimeoutException("slow")

    client._client.get = _raise

    streams = await client.get_streams("movie", "tt1234567")

    assert streams == []


async def test_get_meta_returns_meta_dict(client: StremioAddonClient) -> None:
    client._client.get.return_value = _FakeResponse(
        {"meta": {"id": "tt1234567", "name": "Inception", "year": 2010}}
    )

    meta = await client.get_meta("movie", "tt1234567")

    assert meta["id"] == "tt1234567"
    assert meta["name"] == "Inception"
    assert meta["year"] == 2010


async def test_get_meta_returns_empty_on_malformed_json(
    client: StremioAddonClient,
) -> None:
    def _raise(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("no json")

    client._client.get.return_value = _FakeResponse(None)
    client._client.get.return_value.json = _raise  # type: ignore[method-assign]

    meta = await client.get_meta("movie", "tt1234567")

    assert meta == {}


async def test_get_subtitles_returns_subtitles_list(client: StremioAddonClient) -> None:
    client._client.get.return_value = _FakeResponse(
        {"subtitles": [{"id": "en", "url": "http://cdn/sub.en.vtt"}]}
    )

    subs = await client.get_subtitles("movie", "tt1234567")

    assert len(subs) == 1
    assert subs[0]["id"] == "en"


async def test_get_subtitles_returns_empty_when_unsupported(
    client: StremioAddonClient,
) -> None:
    client._client.get.return_value = _FakeResponse({}, status_error=True)

    subs = await client.get_subtitles("movie", "tt1234567")

    assert subs == []


async def test_get_subtitles_returns_empty_on_malformed_json(
    client: StremioAddonClient,
) -> None:
    def _raise(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("no json")

    client._client.get.return_value = _FakeResponse(None)
    client._client.get.return_value.json = _raise  # type: ignore[method-assign]

    subs = await client.get_subtitles("movie", "tt1234567")

    assert subs == []


async def test_base_url_strips_trailing_slash() -> None:
    instance = StremioAddonClient("http://localhost:7000/")
    assert instance.base_url == "http://localhost:7000"

    instance2 = StremioAddonClient("http://example.com/addon")
    assert instance2.base_url == "http://example.com/addon"


async def test_close_closes_http_client(client: StremioAddonClient) -> None:
    await client.close()
    client._client.aclose.assert_awaited_once()


async def test_get_json_returns_empty_on_non_dict_payload(
    client: StremioAddonClient,
) -> None:
    client._client.get.return_value = _FakeResponse(["not", "a", "dict"])

    manifest = await client.get_manifest()

    assert manifest == {}


async def test_registry_register_and_get() -> None:
    registry = StremioAddonRegistry()
    client = registry.register("cinemeta", "https://v3-cinemeta.strem.io")

    assert registry.get("cinemeta") is client
    assert registry.get("nonexistent") is None


async def test_registry_all_returns_copy() -> None:
    registry = StremioAddonRegistry()
    client_a = registry.register("a", "http://a:7000")
    client_b = registry.register("b", "http://b:7000")

    all_clients = registry.all()
    assert all_clients == {"a": client_a, "b": client_b}

    # Garantir que a cópia não afeta o registro original.
    all_clients["c"] = MagicMock()
    assert "c" not in registry.all()


async def test_registry_close_all_closes_every_client() -> None:
    registry = StremioAddonRegistry()

    client_a = registry.register("a", "http://a:7000")
    client_b = registry.register("b", "http://b:7000")
    client_a._client = AsyncMock()
    client_b._client = AsyncMock()

    await registry.close_all()

    client_a._client.aclose.assert_awaited_once()
    client_b._client.aclose.assert_awaited_once()
