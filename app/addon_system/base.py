"""Interface que todo addon implementa, e os tipos de dados trocados com ele.

Um addon resolve **fontes de mídia** — recebe uma busca em texto livre e
devolve candidatos que, uma vez escolhidos, viram a `<fonte>` de um `/play`
comum. Ele não sabe nada sobre `streaming/`, `telegram/` ou FFmpeg; só fala
`search` → `get_streams` → uma URL http(s) reproduzível.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SearchResult:
    """Um resultado de busca devolvido por `BaseAddon.search`."""

    media_id: str
    title: str
    year: int | None = None
    addon_name: str = ""  # preenchido por AddonManager ao agregar resultados de vários addons


@dataclass(frozen=True, slots=True)
class Metadata:
    """Metadados de uma mídia específica, devolvidos por `BaseAddon.get_metadata`."""

    media_id: str
    title: str
    description: str | None = None
    year: int | None = None


@dataclass(frozen=True, slots=True)
class StreamCandidate:
    """Uma fonte reproduzível concreta, devolvida por `BaseAddon.get_streams`.

    Dois modos, mutuamente exclusivos:

    * `url` preenchido: algo que `utils.sanitize.resolve_source` já sabe
      validar (HTTP/HTTPS direto, HLS, RTMP, RTSP) — reproduzido diretamente
      pelo FFmpeg, fluxo de sempre.
    * `url` ausente e `info_hash`/`magnet` preenchido: torrent sem fonte
      HTTP direta (ex.: Torrentio sem debrid configurado) — resolvido via
      `app.services.torrent_service.TorrentService` antes de chegar ao
      FFmpeg. `file_index`, se conhecido, indica qual arquivo do torrent é o
      vídeo principal (evita a heurística de "maior arquivo de vídeo").

    O addon não decide como o vídeo é transmitido, só de onde ele vem.

    `seeds`/`size_bytes` são opcionais e só preenchidos por addons cuja fonte
    exponha essa informação (ex.: `stremio`, extraída do texto do stream) —
    usados para priorização; `None` significa "desconhecido", não "zero".
    """

    title: str
    url: str | None = None
    info_hash: str | None = None
    magnet: str | None = None
    file_index: int | None = None
    quality: str | None = None
    seeds: int | None = None
    size_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class AddonHealth:
    """Resultado de `BaseAddon.health`, exibido em `/addon info <nome>`."""

    healthy: bool
    detail: str | None = None


class BaseAddon(ABC):
    """Interface que todo addon deve implementar em `plugin.py`.

    Construtor: `def __init__(self, config: dict[str, Any] | None = None) -> None`.
    `config` vem de `config/addons/<nome>.{json,yaml}` se existir, senão `None`.
    """

    name: str
    version: str

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}

    @abstractmethod
    async def search(self, query: str) -> list[SearchResult]:
        """Busca `query` e devolve candidatos (pode ser lista vazia)."""

    @abstractmethod
    async def get_metadata(self, media_id: str) -> Metadata:
        """Detalhes de uma mídia específica, identificada por `media_id`."""

    @abstractmethod
    async def get_streams(self, media_id: str) -> list[StreamCandidate]:
        """Fontes reproduzíveis para `media_id`, idealmente ordenadas por preferência."""

    async def health(self) -> AddonHealth:
        """Verifica se o addon está operante (ex.: API externa respondendo).

        Implementação padrão: sempre saudável. Addons com dependências
        externas devem sobrescrever.
        """
        return AddonHealth(healthy=True)

    async def close(self) -> None:  # noqa: B027 - hook opcional, não obrigatório para subclasses
        """Libera recursos (ex.: fecha um `httpx.AsyncClient`) antes de descarregar.

        Chamado por `AddonManager` em `reload()`/`uninstall()`, antes de
        descartar a instância antiga. Implementação padrão: nada a fazer.
        """
