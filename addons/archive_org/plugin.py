"""Addon oficial: busca filmes de domínio público / licença aberta no Internet Archive.

Usa só a API pública do archive.org (`advancedsearch.php` + `/metadata/`), sem
scraping e sem chave de API. O filtro `licenseurl:(*publicdomain* OR
*creativecommons*)` restringe a busca a itens com licença aberta declarada —
não é uma garantia legal absoluta (metadados podem estar errados), mas é o
mecanismo de curadoria que o próprio archive.org expõe.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.addon_system.base import AddonHealth, BaseAddon, Metadata, SearchResult, StreamCandidate

_SEARCH_URL = "https://archive.org/advancedsearch.php"
_METADATA_URL = "https://archive.org/metadata/{identifier}"
_DOWNLOAD_URL = "https://archive.org/download/{identifier}/{filename}"
_HEALTH_URL = "https://archive.org"

# Formatos de vídeo reais que o archive.org atribui a arquivos de filme.
_VIDEO_FORMATS = frozenset({"MPEG4", "h.264", "512Kb MPEG4", "MPEG2", "Matroska"})


class Addon(BaseAddon):
    """Implementação de `BaseAddon` para o Internet Archive."""

    name = "archive_org"
    version = "1.0.0"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        timeout = float(self.config.get("timeout_seconds", 10.0))
        self._client = httpx.AsyncClient(timeout=timeout)

    async def search(self, query: str) -> list[SearchResult]:
        params = {
            "q": f"({query}) AND mediatype:(movies) AND licenseurl:(*publicdomain* OR *creativecommons*)",
            "fl[]": ["identifier", "title", "year"],
            "rows": "10",
            "output": "json",
        }
        response = await self._client.get(_SEARCH_URL, params=params)
        response.raise_for_status()
        docs = response.json().get("response", {}).get("docs", [])
        return [
            SearchResult(
                media_id=doc["identifier"],
                title=doc.get("title", doc["identifier"]),
                year=doc.get("year"),
            )
            for doc in docs
            if "identifier" in doc
        ]

    async def get_metadata(self, media_id: str) -> Metadata:
        data = await self._fetch_metadata(media_id)
        meta = data.get("metadata", {})
        return Metadata(
            media_id=media_id,
            title=meta.get("title", media_id),
            description=meta.get("description"),
            year=meta.get("year"),
        )

    async def get_streams(self, media_id: str) -> list[StreamCandidate]:
        data = await self._fetch_metadata(media_id)
        candidates = []
        for file_entry in data.get("files", []):
            file_format = file_entry.get("format")
            file_name = file_entry.get("name", "")
            if file_format not in _VIDEO_FORMATS or not file_name.lower().endswith(
                (".mp4", ".m4v")
            ):
                continue
            url = _DOWNLOAD_URL.format(identifier=media_id, filename=file_name)
            candidates.append(StreamCandidate(url=url, title=file_name, quality=file_format))
        # Prioriza h.264/.mp4 (mais compatível/menor) sobre outros formatos.
        candidates.sort(key=lambda candidate: candidate.quality != "h.264")
        return candidates

    async def health(self) -> AddonHealth:
        try:
            response = await self._client.get(_HEALTH_URL, timeout=5.0)
            response.raise_for_status()
            return AddonHealth(healthy=True)
        except httpx.HTTPError as exc:
            return AddonHealth(healthy=False, detail=str(exc))

    async def close(self) -> None:
        await self._client.aclose()

    async def _fetch_metadata(self, media_id: str) -> dict[str, Any]:
        response = await self._client.get(_METADATA_URL.format(identifier=media_id))
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        return data
