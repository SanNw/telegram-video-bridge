"""Addon oficial: consome addons Stremio externos configurados pelo operador.

Cada addon Stremio de terceiro (ex.: Cinemeta, Torrentio) expõe sua própria
API HTTP em `http://host:porta`, seguindo o protocolo Stremio
(`/manifest.json`, `/catalog`, `/stream`, `/meta`). Este addon nativo é uma
ponte: usa `StremioAddonClient` (`app/services/stremio_client.py`) para falar
com cada um dos provedores declarados em
`config/addons/stremio.json`/`.yaml` e expõe o resultado através da interface
`BaseAddon` normal — o mesmo `search` → `get_streams` que qualquer outro
addon nativo implementa.

Provedores de catálogo e de stream são separados — refletem como o ecossistema
Stremio realmente funciona. O Cinemeta fornece catálogo (a lista de mídias que
 aparece em `/find`); o Torrentio fornece apenas streams (fontes reproduzíveis
de uma mídia já conhecida). Assim:

* `search()` consulta somente os provedores de catálogo (`catalogs`).
* `get_metadata()` volta ao provedor de catálogo que originou o `media_id`.
* `get_streams()` consulta TODOS os provedores de stream (`streams`), agrega,
 remove duplicatas por URL (ou infoHash, se não houver URL direta) e
 ranqueia — sem depender do upstream de origem.

Sem provedores configurados, o addon fica inerte: `search` devolve lista vazia
e `health()` reporta não saudável, nunca lança exceção.

`get_streams` também prioriza os resultados: extrai seeds/tamanho do texto do
stream (convenção comum entre addons torrent tipo Torrentio, ex.: "👤 156
💾 21.87 GB"), descarta candidatos com mais de 4GB confirmados, e ordena os
restantes por seeds decrescente — ver `_rank_candidates`.

Compatibilidade: a config legada com `upstreams` continua funcionando — cada
upstream é tratado simultaneamente como provedor de catálogo E de stream,
reproduzindo o comportamento anterior (onde o mesmo upstream fazia tudo).
"""

from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import quote

from app.addon_system.base import AddonHealth, BaseAddon, Metadata, SearchResult, StreamCandidate
from app.services.stremio_client import StremioAddonClient, StremioAddonRegistry

_MEDIA_ID_SEP = ":"

# Addons Stremio baseados em torrent (ex.: Torrentio) não expõem seeds/tamanho
# como campos estruturados — embutem no texto do stream, ex.:
# "Interstellar 2014 2160p\n👤 156 💾 21.87 GB ⚙️ YTS". Sem padrão formal, só
# convenção de fato entre addons desse tipo.
_SEEDS_RE = re.compile(r"👤\s*(\d+)|\bseeds?\s*[:=]?\s*(\d+)", re.IGNORECASE)
_SIZE_RE = re.compile(
    r"💾\s*([\d.,]+)\s*(gi?b|mi?b)|\bsize\s*[:=]?\s*([\d.,]+)\s*(gi?b|mi?b)", re.IGNORECASE
)
_SIZE_MULTIPLIERS = {"gb": 1024**3, "gib": 1024**3, "mb": 1024**2, "mib": 1024**2}

# Prioridade de streams do addon `stremio`: até 4GB, mais seeds primeiro (ver
# docstring do módulo/README). Candidatos sem tamanho conhecido não são
# descartados (não dá pra confirmar violação do limite), mas ficam atrás dos
# que couberam dentro do limite confirmado, na ordenação por seeds.
_MAX_SIZE_BYTES = 4 * 1024**3


def _parse_seeds(text: str) -> int | None:
    match = _SEEDS_RE.search(text)
    if not match:
        return None
    return int(match.group(1) or match.group(2))


def _parse_size_bytes(text: str) -> int | None:
    match = _SIZE_RE.search(text)
    if not match:
        return None
    value_text = match.group(1) or match.group(3)
    unit = (match.group(2) or match.group(4)).lower()
    value = float(value_text.replace(",", "."))
    return int(value * _SIZE_MULTIPLIERS[unit])


class _CatalogRef:
    """Um catálogo (`type`/`id`) a pesquisar dentro de um provedor de catálogo."""

    __slots__ = ("type_", "catalog_id")

    def __init__(self, type_: str, catalog_id: str) -> None:
        self.type_ = type_
        self.catalog_id = catalog_id


class _CatalogProvider:
    """Um provedor de catálogo: cliente Stremio + os catálogos dele."""

    __slots__ = ("name", "client", "catalogs")

    def __init__(self, name: str, client: StremioAddonClient, catalogs: list[_CatalogRef]) -> None:
        self.name = name
        self.client = client
        self.catalogs = catalogs


class Addon(BaseAddon):
    """Implementação de `BaseAddon` que delega a addons Stremio externos.

    Provedores de catálogo e de stream são roteados separadamente (ver
    docstring do módulo): catálogo só alimenta `search`/`get_metadata`; stream
    só alimenta `get_streams`. O `media_id` carrega `<catalog_provider>:<type>
    :<id>` para `get_metadata` saber onde voltar — em `get_streams` o nome do
    provedor é ignorado e consulta-se todos os provedores de stream.
    """

    name = "stremio"
    version = "2.0.0"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        # Um registro só: o StremioAddonClient é o mesmo independentemente do
        # papel (catalog/stream); a separação é decisão de roteamento deste
        # plugin, não do client. Um mesmo provedor pode aparecer em ambos os
        # papéis sem criar dois httpx.AsyncClient.
        self._registry = StremioAddonRegistry()
        self._catalog_providers: dict[str, _CatalogProvider] = {}
        self._stream_provider_names: list[str] = []
        self._configure()

    def _configure(self) -> None:
        """Constrói os provedores a partir da config, migrando o formato legado.

        Formato novo: `catalogs` (com `catalogs`) e `streams` (sem `catalogs`).
        Formato legado: `upstreams` — cada entrada vira provedor de catálogo E
        de stream simultaneamente, reproduzindo o comportamento anterior onde
        o mesmo upstream fazia tudo. Se ambos coexistirem, o novo prevalece.
        """
        if "catalogs" in self.config or "streams" in self.config:
            for catalog in self.config.get("catalogs", []):
                self._register_catalog_provider(catalog)
            for stream in self.config.get("streams", []):
                self._register_stream_provider(stream)
            return

        # Formato legado: cada upstream é catálogo e stream ao mesmo tempo.
        for upstream in self.config.get("upstreams", []):
            name = upstream["name"]
            client = self._registry.register(name, upstream["base_url"])
            catalogs = [_CatalogRef(c["type"], c["id"]) for c in upstream.get("catalogs", [])]
            if catalogs:
                self._catalog_providers[name] = _CatalogProvider(name, client, catalogs)
            # No formato legado, todo upstream também é provedor de stream —
            # era assim que `get_streams` voltava ao upstream de origem.
            if name not in self._stream_provider_names:
                self._stream_provider_names.append(name)

    def _register_catalog_provider(self, entry: dict[str, Any]) -> None:
        name = entry["name"]
        client = self._registry.get(name) or self._registry.register(name, entry["base_url"])
        catalogs = [_CatalogRef(c["type"], c["id"]) for c in entry.get("catalogs", [])]
        self._catalog_providers[name] = _CatalogProvider(name, client, catalogs)

    def _register_stream_provider(self, entry: dict[str, Any]) -> None:
        name = entry["name"]
        # Reusa o cliente se o mesmo provedor já foi registrado como catálogo;
        # senão registra um novo. URLs-base idênticas não duplicam httpx client.
        if self._registry.get(name) is None:
            self._registry.register(name, entry["base_url"])
        if name not in self._stream_provider_names:
            self._stream_provider_names.append(name)

    async def search(self, query: str) -> list[SearchResult]:
        encoded_query = quote(query)
        tasks = [
            self._search_one_catalog(provider, catalog, encoded_query)
            for provider in self._catalog_providers.values()
            for catalog in provider.catalogs
        ]
        if not tasks:
            return []
        results_per_catalog = await asyncio.gather(*tasks)
        return [result for results in results_per_catalog for result in results]

    async def _search_one_catalog(
        self, provider: _CatalogProvider, catalog: _CatalogRef, encoded_query: str
    ) -> list[SearchResult]:
        metas = await provider.client.get_catalog(
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
                    media_id=_encode_media_id(provider.name, catalog.type_, media_id),
                    title=title,
                    year=year,
                )
            )
        return results

    async def _streams_from_provider(
        self, name: str, type_: str, upstream_id: str
    ) -> list[dict[str, Any]]:
        """Pega os streams de um provedor, degradando para `[]` se sumir do
        registro — consistente com o resto do addon, que nunca lança."""
        client = self._registry.get(name)
        if client is None:
            return []
        return await client.get_streams(type_, upstream_id)

    async def get_metadata(self, media_id: str) -> Metadata:
        upstream_name, type_, upstream_id = _decode_media_id(media_id)
        provider = self._catalog_providers.get(upstream_name)
        if provider is None:
            return Metadata(media_id=media_id, title=media_id)
        meta = await provider.client.get_meta(type_, upstream_id)
        return Metadata(
            media_id=media_id,
            title=meta.get("name") or upstream_id,
            description=meta.get("description"),
            year=_parse_year(meta.get("releaseInfo") or meta.get("year")),
        )

    async def get_streams(self, media_id: str) -> list[StreamCandidate]:
        _, type_, upstream_id = _decode_media_id(media_id)
        if not self._stream_provider_names:
            return []
        # Consulta TODOS os provedores de stream configurados — o nome do
        # upstream de origem (catálogo) é deliberadamente ignorado aqui,
        # pois o provedor que lista a mídia (ex.: Cinemeta) quase nunca é o
        # que serve os streams (ex.: Torrentio).
        batches = await asyncio.gather(
            *(
                self._streams_from_provider(name, type_, upstream_id)
                for name in self._stream_provider_names
            )
        )
        candidates: list[StreamCandidate] = []
        seen: set[str] = set()
        for stream in (s for batch in batches for s in batch):
            url = stream.get("url")
            info_hash = stream.get("infoHash")
            # Chave de dedup: URL direta se houver, senão infoHash — mesma
            # fonte servida por dois provedores (ou o mesmo provedor duas
            # vezes) mantém só a primeira ocorrência, refletindo a ordem dos
            # provedores.
            dedup_key = url or info_hash
            if not dedup_key or dedup_key in seen:
                continue
            seen.add(dedup_key)
            title = stream.get("title") or stream.get("name") or dedup_key
            description = stream.get("description") or ""
            search_text = f"{title}\n{description}"
            candidates.append(
                StreamCandidate(
                    title=title,
                    url=url,
                    # Sem URL direta (ex.: Torrentio sem debrid configurado):
                    # resolvido via TorrentService (qBittorrent) antes do
                    # FFmpeg, em vez de descartado.
                    info_hash=info_hash if not url else None,
                    file_index=stream.get("fileIdx") if not url else None,
                    quality=stream.get("name"),
                    seeds=_parse_seeds(search_text),
                    size_bytes=_parse_size_bytes(search_text),
                )
            )
        return _rank_candidates(candidates)

    async def health(self) -> AddonHealth:
        clients = self._registry.all()
        if not clients:
            return AddonHealth(healthy=False, detail="Nenhum provedor Stremio configurado.")
        manifests = await asyncio.gather(*(client.get_manifest() for client in clients.values()))
        unreachable = [
            name for name, manifest in zip(clients.keys(), manifests, strict=True) if not manifest
        ]
        if unreachable:
            return AddonHealth(
                healthy=False, detail=f"Provedores inacessíveis: {', '.join(unreachable)}"
            )
        catalog_n = len(self._catalog_providers)
        stream_n = len(self._stream_provider_names)
        return AddonHealth(
            healthy=True,
            detail=f"{len(clients)} provedor(es) respondendo ({catalog_n} catálogo, {stream_n} stream).",
        )

    async def close(self) -> None:
        await self._registry.close_all()


def _encode_media_id(provider_name: str, type_: str, upstream_id: str) -> str:
    return _MEDIA_ID_SEP.join((provider_name, type_, upstream_id))


def _decode_media_id(media_id: str) -> tuple[str, str, str]:
    provider_name, _, rest = media_id.partition(_MEDIA_ID_SEP)
    type_, _, upstream_id = rest.partition(_MEDIA_ID_SEP)
    return provider_name, type_, upstream_id


def _parse_year(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value)[:4]
    return int(text) if text.isdigit() else None


def _rank_candidates(candidates: list[StreamCandidate]) -> list[StreamCandidate]:
    """Filtra candidatos com tamanho conhecido acima de 4GB e ordena por seeds.

    Regras (ver docstring do módulo): tamanho desconhecido não é descartado
    (não dá pra confirmar violação do limite); ordenação prioriza mais seeds,
    com candidatos sem contagem de seeds conhecida ao final.
    """
    within_limit = [
        candidate
        for candidate in candidates
        if candidate.size_bytes is None or candidate.size_bytes <= _MAX_SIZE_BYTES
    ]
    within_limit.sort(
        key=lambda candidate: candidate.seeds if candidate.seeds is not None else -1, reverse=True
    )
    return within_limit
