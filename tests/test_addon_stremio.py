"""Testes do addon `stremio` (`addons/stremio/plugin.py`).

Carrega o módulo do mesmo jeito que `AddonLoader` faria (via `importlib`, sem
depender de `addons/` estar no `sys.path`) e substitui o `httpx.AsyncClient`
interno de cada `StremioAddonClient` registrado por um dublê — nenhum destes
testes bate na rede real. Mesmo padrão de `test_addon_archive_org.py`.

Cobre dois esquemas de configuração:
- legado `upstreams` (cada upstream é catálogo + stream);
- novo `catalogs`/`streams` separados (provedor de catálogo e de stream
  distintos — que é o caso Torrentio).
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

# Esquema novo: catálogo vem do Cinemeta, streams do Torrentio — provedores
# distintos, como no ecossistema Stremio real.
_CONFIG_SEPARATED = {
    "catalogs": [
        {
            "name": "cinemeta",
            "base_url": "https://v3-cinemeta.strem.io",
            "catalogs": [{"type": "movie", "id": "top"}],
        }
    ],
    "streams": [
        {"name": "torrentio", "base_url": "https://torrentio.strem.fun"},
    ],
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


def _make_addon(plugin_module: ModuleType, config: dict[str, Any]) -> Any:
    """Instancia o addon com `config` e substitui o httpx client de cada
    StremioAddonClient registrado por um AsyncMock pronto pra ser programado."""
    instance = plugin_module.Addon(config=config)
    for client in instance._registry.all().values():
        client._client = AsyncMock()
    return instance


def _client(addon: Any, name: str) -> Any:
    client = addon._registry.get(name)
    assert client is not None, f"cliente '{name}' não registrado"
    return client


@pytest.fixture
def addon(plugin_module: ModuleType) -> Any:
    return _make_addon(plugin_module, _CONFIG)


@pytest.fixture
def separated(plugin_module: ModuleType) -> Any:
    return _make_addon(plugin_module, _CONFIG_SEPARATED)


def _fake_client(addon: Any) -> Any:
    client = addon._registry.get("cinemeta")
    assert client is not None
    return client


# ---------------------------------------------------------------------------
# Configuração sem provedores
# ---------------------------------------------------------------------------


async def test_addon_without_config_has_no_upstreams(plugin_module: ModuleType) -> None:
    instance = plugin_module.Addon()

    assert await instance.search("anything") == []


async def test_search_returns_empty_when_only_stream_providers_configured(
    plugin_module: ModuleType,
) -> None:
    # Torrentio sozinho: sem catálogo, a busca fica inerte.
    instance = plugin_module.Addon(config={"streams": [{"name": "torrentio", "base_url": "https://x"}]})

    assert await instance.search("anything") == []


# ---------------------------------------------------------------------------
# search()
# ---------------------------------------------------------------------------


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


async def test_search_ignores_stream_providers(separated: Any) -> None:
    # A busca deve consultar só o Cinemeta — o Torrentio (stream) não participa.
    cinemeta = _client(separated, "cinemeta")
    torrentio = _client(separated, "torrentio")
    cinemeta._client.get.return_value = _FakeResponse(
        {"metas": [{"id": "tt0816692", "name": "Interstellar", "releaseInfo": "2014"}]}
    )

    results = await separated.search("interstellar")

    assert len(results) == 1
    assert results[0].media_id == "cinemeta:movie:tt0816692"
    # O client do Torrentio nunca foi chamado durante a busca.
    torrentio._client.get.assert_not_called()


# ---------------------------------------------------------------------------
# get_metadata()
# ---------------------------------------------------------------------------


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


async def test_get_metadata_uses_catalog_provider_not_stream(separated: Any) -> None:
    # get_metadata volta ao provedor de catálogo de origem — nunca ao Torrentio.
    cinemeta = _client(separated, "cinemeta")
    torrentio = _client(separated, "torrentio")
    cinemeta._client.get.return_value = _FakeResponse(
        {"meta": {"name": "Interstellar", "releaseInfo": "2014"}}
    )

    metadata = await separated.get_metadata("cinemeta:movie:tt0816692")

    assert metadata.title == "Interstellar"
    torrentio._client.get.assert_not_called()
    requested_url = cinemeta._client.get.call_args.args[0]
    assert requested_url == "https://v3-cinemeta.strem.io/meta/movie/tt0816692.json"


# ---------------------------------------------------------------------------
# get_streams()
# ---------------------------------------------------------------------------


async def test_get_streams_maps_url_and_infohash_entries(addon: Any) -> None:
    client = _fake_client(addon)
    client._client.get.return_value = _FakeResponse(
        {
            "streams": [
                {"url": "https://cdn/file.mp4", "title": "720p", "name": "CDN"},
                {"infoHash": "abc123", "title": "torrent only", "fileIdx": 2},
            ]
        }
    )

    streams = await addon.get_streams("cinemeta:movie:tt1254207")

    assert len(streams) == 2
    assert streams[0].url == "https://cdn/file.mp4"
    assert streams[0].title == "720p"
    assert streams[0].quality == "CDN"
    assert streams[0].info_hash is None

    torrent_only = streams[1]
    assert torrent_only.url is None
    assert torrent_only.info_hash == "abc123"
    assert torrent_only.file_index == 2
    assert torrent_only.title == "torrent only"


async def test_get_streams_discards_entries_without_url_or_infohash(addon: Any) -> None:
    client = _fake_client(addon)
    client._client.get.return_value = _FakeResponse(
        {"streams": [{"title": "no source at all"}]}
    )

    streams = await addon.get_streams("cinemeta:movie:tt1254207")

    assert streams == []


async def test_get_streams_parses_seeds_and_size_from_title(addon: Any) -> None:
    client = _fake_client(addon)
    client._client.get.return_value = _FakeResponse(
        {
            "streams": [
                {
                    "url": "https://cdn/a.mp4",
                    "title": "Interstellar 2014 1080p\n👤 156 💾 2.87 GB ⚙️ YTS",
                }
            ]
        }
    )

    streams = await addon.get_streams("cinemeta:movie:tt1254207")

    assert len(streams) == 1
    assert streams[0].seeds == 156
    assert streams[0].size_bytes == int(2.87 * 1024**3)


async def test_get_streams_discards_candidates_over_4gb(addon: Any) -> None:
    client = _fake_client(addon)
    client._client.get.return_value = _FakeResponse(
        {
            "streams": [
                {"url": "https://cdn/small.mp4", "title": "Small\n👤 10 💾 2.5 GB"},
                {"url": "https://cdn/big.mp4", "title": "Big\n👤 999 💾 5.2 GB"},
            ]
        }
    )

    streams = await addon.get_streams("cinemeta:movie:tt1254207")

    assert len(streams) == 1
    assert streams[0].url == "https://cdn/small.mp4"


async def test_get_streams_sorts_by_seeds_descending(addon: Any) -> None:
    client = _fake_client(addon)
    client._client.get.return_value = _FakeResponse(
        {
            "streams": [
                {"url": "https://cdn/low.mp4", "title": "Low\n👤 5 💾 1 GB"},
                {"url": "https://cdn/high.mp4", "title": "High\n👤 500 💾 1 GB"},
                {"url": "https://cdn/unknown.mp4", "title": "Unknown seeds"},
            ]
        }
    )

    streams = await addon.get_streams("cinemeta:movie:tt1254207")

    assert [s.url for s in streams] == [
        "https://cdn/high.mp4",
        "https://cdn/low.mp4",
        "https://cdn/unknown.mp4",
    ]


async def test_get_streams_keeps_candidates_with_unknown_size(addon: Any) -> None:
    client = _fake_client(addon)
    client._client.get.return_value = _FakeResponse(
        {"streams": [{"url": "https://cdn/nosize.mp4", "title": "No size info here"}]}
    )

    streams = await addon.get_streams("cinemeta:movie:tt1254207")

    assert len(streams) == 1
    assert streams[0].size_bytes is None
    assert streams[0].seeds is None


async def test_get_streams_queries_all_stream_providers(separated: Any) -> None:
    # Provedor de catálogo (cinemeta) NÃO é consultado; torrentio, sim. E mais
    # um provedor de stream adicional. Todos em paralelo.
    separated._stream_provider_names.append("orion")
    separated._registry.register("orion", "https://orion.provider")
    orion = _client(separated, "orion")
    orion._client = AsyncMock()
    torrentio = _client(separated, "torrentio")

    torrentio._client.get.return_value = _FakeResponse(
        {"streams": [{"url": "https://torrentio/a.mp4", "title": "T\n👤 30 💾 1 GB"}]}
    )
    orion._client.get.return_value = _FakeResponse(
        {"streams": [{"url": "https://orion/b.mp4", "title": "O\n👤 80 💾 1 GB"}]}
    )

    streams = await separated.get_streams("cinemeta:movie:tt0816692")

    urls = {s.url for s in streams}
    assert urls == {"https://torrentio/a.mp4", "https://orion/b.mp4"}
    # O provedor de catálogo não participa da fase de streams.
    cinemeta = _client(separated, "cinemeta")
    cinemeta._client.get.assert_not_called()


async def test_get_streams_dedups_by_url_across_providers(separated: Any) -> None:
    # Dois provedores devolvem a mesma URL — só fica uma ocorrência.
    torrentio = _client(separated, "torrentio")
    torrentio._client.get.return_value = _FakeResponse(
        {"streams": [{"url": "https://shared/x.mp4", "title": "T\n👤 30 💾 1 GB"}]}
    )

    streams = await separated.get_streams("cinemeta:movie:tt0816692")

    assert len(streams) == 1
    assert streams[0].url == "https://shared/x.mp4"


async def test_get_streams_dedups_by_infohash_when_no_url(separated: Any) -> None:
    # Sem URL direta, a dedup usa o infoHash como chave.
    separated._stream_provider_names.append("orion")
    separated._registry.register("orion", "https://orion.provider")
    torrentio = _client(separated, "torrentio")
    orion = _client(separated, "orion")
    orion._client = AsyncMock()
    torrentio._client.get.return_value = _FakeResponse(
        {"streams": [{"infoHash": "shared-hash", "title": "T"}]}
    )
    orion._client.get.return_value = _FakeResponse(
        {"streams": [{"infoHash": "shared-hash", "title": "O"}]}
    )

    streams = await separated.get_streams("cinemeta:movie:tt0816692")

    assert len(streams) == 1
    assert streams[0].info_hash == "shared-hash"


async def test_get_streams_ignores_upstream_name_in_media_id(separated: Any) -> None:
    # O nome do provedor no media_id (cinemeta) é ignorado em get_streams —
    # consulta os provedores de stream (torrentio), não o de catálogo.
    torrentio = _client(separated, "torrentio")
    torrentio._client.get.return_value = _FakeResponse(
        {"streams": [{"url": "https://torrentio/y.mp4", "title": "T\n👤 1 💾 1 GB"}]}
    )

    streams = await separated.get_streams("cinemeta:movie:tt0816692")

    assert len(streams) == 1
    requested_url = torrentio._client.get.call_args.args[0]
    assert requested_url == "https://torrentio.strem.fun/stream/movie/tt0816692.json"


async def test_get_streams_no_stream_providers_returns_empty(plugin_module: ModuleType) -> None:
    # Só catálogo, sem nem um provedor de stream: get_streams fica inerte.
    instance = plugin_module.Addon(
        config={"catalogs": [{"name": "cinemeta", "base_url": "https://x", "catalogs": [{"type": "movie", "id": "top"}]}]}
    )

    assert await instance.get_streams("cinemeta:movie:tt0816692") == []


# ---------------------------------------------------------------------------
# Reusa o mesmo httpx client quando um provedor é catálogo e stream
# ---------------------------------------------------------------------------


async def test_shared_name_reuses_one_httpx_client(plugin_module: ModuleType) -> None:
    # Cinemeta aparece como catálogo e como stream sob o MESMO name — não
    # deve criar dois httpx.AsyncClient.
    instance = plugin_module.Addon(
        config={
            "catalogs": [{"name": "cinemeta", "base_url": "https://cinemeta", "catalogs": [{"type": "movie", "id": "top"}]}],
            "streams": [{"name": "cinemeta", "base_url": "https://cinemeta"}],
        }
    )

    assert instance._registry.all() == {"cinemeta": instance._registry.get("cinemeta")}
    assert "cinemeta" in instance._catalog_providers
    assert "cinemeta" in instance._stream_provider_names


# ---------------------------------------------------------------------------
# health()
# ---------------------------------------------------------------------------


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


async def test_health_reports_unreachable_stream_provider(separated: Any) -> None:
    # Cinemeta responde, Torrentio cai — health deve listar torrentio.
    cinemeta = _client(separated, "cinemeta")
    torrentio = _client(separated, "torrentio")
    cinemeta._client.get.return_value = _FakeResponse({"id": "cinemeta", "name": "Cinemeta"})

    async def _raise(*args: Any, **kwargs: Any) -> Any:
        raise httpx.ConnectError("unreachable")

    torrentio._client.get = _raise

    health = await separated.health()

    assert health.healthy is False
    assert "torrentio" in (health.detail or "")
    assert "cinemeta" not in (health.detail or "")


async def test_health_healthy_lists_provider_counts(separated: Any) -> None:
    cinemeta = _client(separated, "cinemeta")
    torrentio = _client(separated, "torrentio")
    cinemeta._client.get.return_value = _FakeResponse({"id": "cinemeta", "name": "Cinemeta"})
    torrentio._client.get.return_value = _FakeResponse({"id": "torrentio", "name": "Torrentio"})

    health = await separated.health()

    assert health.healthy is True
    assert health.detail is not None
    assert "1 catálogo" in health.detail  # 1 catálogo → exibe singular
    assert "1 stream" in health.detail


# ---------------------------------------------------------------------------
# close()
# ---------------------------------------------------------------------------


async def test_close_closes_all_registered_clients(addon: Any) -> None:
    client = _fake_client(addon)

    await addon.close()

    client._client.aclose.assert_awaited_once()


async def test_close_closes_catalog_and_stream_clients(separated: Any) -> None:
    cinemeta = _client(separated, "cinemeta")
    torrentio = _client(separated, "torrentio")

    await separated.close()

    cinemeta._client.aclose.assert_awaited_once()
    torrentio._client.aclose.assert_awaited_once()
