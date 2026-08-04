"""Addon oficial: consome addons Stremio externos configurados pelo operador.

Cada addon Stremio de terceiro (ex.: Cinemeta, um addon self-hosted) expõe sua
própria API HTTP em `http://host:porta`, seguindo o protocolo Stremio
(`/manifest.json`, `/catalog`, `/stream`, `/meta`). Este addon nativo é uma
ponte: usa `StremioAddonClient` (`app/services/stremio_client.py`) para falar
com cada um dos "upstreams" declarados em
`config/addons/stremio.json`/`.yaml` e expõe o resultado através da interface
`BaseAddon` normal — o mesmo `search` → `get_streams` que qualquer outro
addon nativo implementa.

Sem upstreams configurados, o addon fica inerte: `search` devolve lista vazia
e `health()` reporta não saudável, nunca lança exceção.
"""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import quote

from app.addon_system.base import AddonHealth, BaseAddon, Metadata, SearchResult, StreamCandidate
from app.services.stremio_client import StremioAddonRegistry

_MEDIA_ID_SEP = ":"


class _UpstreamCatalog:
    """Um catálogo (`type`/`id`) a pesquisar dentro de um upstream Stremio."""

    __slots__ = ("type_", "catalog_id")

    def __init__(self, type_: str, catalog_id: str) -> None:
        self.type_ = type_
        self.catalog_id = catalog_id


class Addon(BaseAddon):
    """Implementação de `BaseAddon` que delega a addons Stremio externos."""

    name = "stremio"
    version = "1.0.0"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self._registry = StremioAddonRegistry()
        self._catalogs: dict[str, list[_UpstreamCatalog]] = {}
        for upstream in self.config.get("upstreams", []):
            upstream_name = upstream["name"]
            self._registry.register(upstream_name, upstream["base_url"])
            self._catalogs[upstream_name] = [
                _UpstreamCatalog(catalog["type"], catalog["id"])
                for catalog in upstream.get("catalogs", [])
            ]

    async def search(self, query: str) -> list[SearchResult]:
        encoded_query = quote(query)
        tasks = [
            self._search_one_catalog(upstream_name, catalog, encoded_query)
            for upstream_name, catalogs in self._catalogs.items()
            for catalog in catalogs
        ]
        if not tasks:
            return []
        results_per_catalog = await asyncio.gather(*tasks)
        return [result for results in results_per_catalog for result in results]

    async def _search_one_catalog(
        self, upstream_name: str, catalog: _UpstreamCatalog, encoded_query: str
    ) -> list[SearchResult]:
        client = self._registry.get(upstream_name)
        if client is None:
            return []
        metas = await client.get_catalog(
            catalog.type_, catalog.catalog_id, extra={"search": encoded_query}
        )
        results = []
        for meta in metas:
            media_id = meta.get("id")
            if not media_id:
                continue
            title = meta.get("name") or media_id
            year = _parse_year(meta.get("releaseInfo") or meta.get("year"))
            results.append(
                SearchResult(
                    media_id=_encode_media_id(upstream_name, catalog.type_, media_id),
                    title=title,
                    year=year,
                )
            )
        return results

    async def get_metadata(self, media_id: str) -> Metadata:
        upstream_name, type_, upstream_id = _decode_media_id(media_id)
        client = self._registry.get(upstream_name)
        if client is None:
            return Metadata(media_id=media_id, title=media_id)
        meta = await client.get_meta(type_, upstream_id)
        return Metadata(
            media_id=media_id,
            title=meta.get("name") or upstream_id,
            description=meta.get("description"),
            year=_parse_year(meta.get("releaseInfo") or meta.get("year")),
        )

    async def get_streams(self, media_id: str) -> list[StreamCandidate]:
        upstream_name, type_, upstream_id = _decode_media_id(media_id)
        client = self._registry.get(upstream_name)
        if client is None:
            return []
        streams = await client.get_streams(type_, upstream_id)
        candidates = []
        for stream in streams:
            url = stream.get("url")
            if not url:
                # Fontes sem URL direta (ex.: torrent via infoHash) não são
                # reproduzíveis pelo pipeline FFmpeg atual (só http/https/hls/
                # rtmp/rtsp) — ignoradas, não é um erro.
                continue
            title = stream.get("title") or stream.get("name") or url
            candidates.append(StreamCandidate(url=url, title=title, quality=stream.get("name")))
        return candidates

    async def health(self) -> AddonHealth:
        upstreams = self._registry.all()
        if not upstreams:
            return AddonHealth(healthy=False, detail="Nenhum upstream Stremio configurado.")
        manifests = await asyncio.gather(
            *(client.get_manifest() for client in upstreams.values())
        )
        unreachable = [
            name for name, manifest in zip(upstreams.keys(), manifests, strict=True) if not manifest
        ]
        if unreachable:
            return AddonHealth(
                healthy=False, detail=f"Upstreams inacessíveis: {', '.join(unreachable)}"
            )
        return AddonHealth(healthy=True, detail=f"{len(upstreams)} upstream(s) respondendo.")

    async def close(self) -> None:
        await self._registry.close_all()


def _encode_media_id(upstream_name: str, type_: str, upstream_id: str) -> str:
    return _MEDIA_ID_SEP.join((upstream_name, type_, upstream_id))


def _decode_media_id(media_id: str) -> tuple[str, str, str]:
    upstream_name, _, rest = media_id.partition(_MEDIA_ID_SEP)
    type_, _, upstream_id = rest.partition(_MEDIA_ID_SEP)
    return upstream_name, type_, upstream_id


def _parse_year(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value)[:4]
    return int(text) if text.isdigit() else None
