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

    `url` deve ser algo que `utils.sanitize.resolve_source` já sabe validar
    (hoje: HTTP/HTTPS direto, HLS, RTMP, RTSP) — o addon não decide como o
    vídeo é transmitido, só de onde ele vem.
    """

    url: str
    title: str
    quality: str | None = None


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
