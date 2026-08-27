"""Interface que todo backend de torrent implementa, e os tipos trocados com ele.

`TorrentService` (orquestração: aguardar metadata, escolher o arquivo de
vídeo, aguardar buffer, limpar) fala só com esta interface — nunca importa
`QBittorrentClient` diretamente nem conhece detalhes da Web API do
qBittorrent. Trocar de backend (ex.: para um baseado em `libtorrent`) é
implementar uma nova classe aqui e trocar a instanciação em
`TorrentService.__init__`; nenhuma outra camada muda.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from urllib.parse import quote

# Trackers públicos bem estabelecidos, embutidos para melhorar a descoberta de
# peers quando um magnet é montado só a partir de um infoHash (sem lista de
# trackers própria) — caso comum de addons torrent tipo Torrentio.
_PUBLIC_TRACKERS = (
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.tracker.cl:1337/announce",
    "udp://tracker.openbittorrent.com:6969/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://tracker.torrent.eu.org:451/announce",
)


@dataclass(frozen=True, slots=True)
class TorrentFile:
    """Um arquivo dentro de um torrent, como devolvido por `TorrentBackend.files`.

    `path` é relativo à raiz do torrent (mesma convenção da Web API do
    qBittorrent) — quem resolve o caminho absoluto em disco é
    `TorrentService`, que conhece `save_path`.
    """

    index: int
    path: str
    size_bytes: int
    downloaded_bytes: int


@dataclass(frozen=True, slots=True)
class TorrentStatus:
    """Estado de um torrent em um instante, como devolvido por `TorrentBackend.status`.

    `has_metadata` distingue o estado inicial (só magnet, sem saber ainda
    quais arquivos existem) do estado normal — a Web API do qBittorrent não
    expõe isso como um campo direto, cada backend mapeia à sua maneira.
    """

    has_metadata: bool
    progress: float
    num_seeds: int
    num_peers: int
    save_path: str


def build_magnet_uri(info_hash: str, title: str) -> str:
    """Monta um magnet URI a partir de um infoHash puro (sem lista de trackers).

    Caso comum de addons torrent tipo Torrentio: o stream só traz o infoHash,
    não um magnet completo. Os trackers públicos embutidos em
    `_PUBLIC_TRACKERS` melhoram a descoberta de peers nesse cenário.
    """
    trackers = "".join(f"&tr={quote(tracker, safe='')}" for tracker in _PUBLIC_TRACKERS)
    return f"magnet:?xt=urn:btih:{info_hash}&dn={quote(title)}{trackers}"


class TorrentBackend(ABC):
    """Interface mínima que um backend de download de torrents deve implementar.

    Só expõe dados brutos do backend (progresso, arquivos, status) — a
    lógica de "qual arquivo é o vídeo principal" ou "o buffer já é
    suficiente" vive em `TorrentService`, não aqui, para que qualquer
    backend futuro reuse essa regra sem duplicá-la.
    """

    @abstractmethod
    async def add(self, magnet: str) -> str:
        """Adiciona um torrent via magnet URI. Devolve o hash/handle do torrent."""

    @abstractmethod
    async def status(self, handle: str) -> TorrentStatus | None:
        """Estado atual do torrent `handle`, ou `None` se não for encontrado."""

    @abstractmethod
    async def files(self, handle: str) -> list[TorrentFile]:
        """Arquivos do torrent `handle` (vazio se a metadata ainda não chegou)."""

    @abstractmethod
    async def select_file(self, handle: str, file_index: int) -> None:
        """Desativa os demais arquivos e prioriza somente `file_index`."""

    @abstractmethod
    async def remove(self, handle: str) -> None:
        """Remove o torrent `handle` do backend (dados baixados inclusos)."""

    @abstractmethod
    async def list_active(self) -> list[TorrentStatus]:
        """Todos os torrents ativos no backend no momento."""

    async def close(self) -> None:  # noqa: B027 - hook opcional, não obrigatório para subclasses
        """Libera recursos (ex.: fecha um `httpx.AsyncClient`). Implementação padrão: nada a fazer."""
