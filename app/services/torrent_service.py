"""Orquestração da resolução de `StreamCandidate`s torrent-only em um arquivo local.

Ponto único que `AddonService` toca para tudo relacionado a torrents — mesma
regra de camadas que `TMDBService` já segue para TMDB. Fala apenas com a
interface `TorrentBackend` (nunca importa `QBittorrentClient` fora do
construtor), decide qual arquivo do torrent é o vídeo principal, e aguarda um
buffer mínimo antes de liberar o caminho para `PlaybackService.play()`.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from app.addon_system.base import StreamCandidate
from app.config.settings import Settings
from app.services.exceptions import TorrentResolutionError, TorrentTimeoutError
from app.services.qbittorrent_client import (
    QBittorrentAuthError,
    QBittorrentClient,
    QBittorrentUnavailableError,
)
from app.services.torrent_backend import (
    TorrentBackend,
    TorrentFile,
    TorrentStatus,
    build_magnet_uri,
)
from app.utils.logging import get_logger
from app.utils.sanitize import MediaSource

_logger = get_logger("services")

# Mesma lista de `utils.sanitize._ALLOWED_LOCAL_EXTENSIONS` — mantida separada
# porque aqui filtra candidatos a "arquivo de vídeo principal" dentro de um
# torrent, um problema conceitualmente diferente de validar uma fonte de
# `/play` (mas o conjunto de extensões aceitas é intencionalmente o mesmo).
_VIDEO_EXTENSIONS = frozenset({".mp4", ".mkv", ".avi", ".mov", ".ts", ".m2ts"})
_MIN_VIDEO_SIZE_BYTES = 5 * 1024 * 1024  # ignora amostras/arquivos residuais (<5MB)
_POLL_INTERVAL_SECONDS = 2.0


class TorrentService:
    """Resolve `StreamCandidate`s sem `url` (infoHash/magnet) em um caminho local reproduzível.

    Instancia `QBittorrentClient` internamente — troca de backend (ex.: para
    um baseado em `libtorrent`) é só trocar essa instanciação, nenhuma outra
    camada muda (`AddonService` só conhece `resolve`/`release`/`close`).
    """

    def __init__(self, settings: Settings, backend: TorrentBackend | None = None) -> None:
        self._settings = settings
        self._backend: TorrentBackend = backend or QBittorrentClient(settings)
        # Rastreia caminho local -> handle, para `release()` saber qual
        # torrent remover quando `settings.remove_torrent_after_play` está ligado.
        self._handles_by_path: dict[str, str] = {}

    async def resolve(self, candidate: StreamCandidate) -> str:
        """Baixa o torrent de `candidate` até um buffer mínimo e devolve o caminho absoluto.

        Levanta `TorrentTimeoutError` se metadata ou buffer não chegarem
        dentro de `settings.torrent_timeout_seconds` (recuperável — o
        chamador pode tentar o próximo candidato). Levanta
        `TorrentResolutionError` para falhas não recuperáveis (auth,
        indisponibilidade, magnet inválido, nenhum vídeo encontrado).
        """
        magnet = candidate.magnet or (
            build_magnet_uri(candidate.info_hash, candidate.title)
            if candidate.info_hash
            else None
        )
        if magnet is None:
            raise TorrentResolutionError(
                "Candidato sem magnet nem infoHash — nada para resolver via torrent."
            )

        deadline = time.monotonic() + self._settings.torrent_timeout_seconds
        try:
            handle = await self._backend.add(magnet)
        except (QBittorrentAuthError, QBittorrentUnavailableError) as exc:
            raise TorrentResolutionError(f"Falha ao adicionar torrent: {exc}") from exc

        await self._wait_for_metadata(handle, deadline)
        video_file = await self._select_video_file(handle, candidate.file_index)
        _logger.info("Arquivo selecionado: {path}", path=video_file.path)
        await self._wait_for_buffer(handle, video_file, deadline)

        status = await self._get_status(handle)
        save_path = Path(status.save_path) if status else self._settings.qbittorrent_save_path
        local_path = str((save_path / video_file.path).resolve())
        self._handles_by_path[local_path] = handle
        _logger.info("Buffer atingido, iniciando reprodução: {path}", path=local_path)
        return local_path

    async def release(self, source: MediaSource) -> None:
        """Libera um torrent que deixou de ser a fonte atual de reprodução.

        Remove do qBittorrent apenas se `settings.remove_torrent_after_play`
        estiver ligado; caso contrário mantém em seed (comportamento padrão).
        Não faz nada se `source` não corresponder a um torrent rastreado
        (ex.: era uma URL HTTP direta).
        """
        handle = self._handles_by_path.pop(source.raw, None)
        if handle is None:
            return
        if not self._settings.remove_torrent_after_play:
            return
        try:
            await self._backend.remove(handle)
        except (QBittorrentAuthError, QBittorrentUnavailableError) as exc:
            _logger.warning(
                "Falha ao remover torrent {hash}: {err}", hash=handle, err=exc
            )

    async def close(self) -> None:
        """Fecha o backend subjacente (ex.: `httpx.AsyncClient` do `QBittorrentClient`)."""
        await self._backend.close()

    async def _wait_for_metadata(self, handle: str, deadline: float) -> None:
        while True:
            status = await self._get_status(handle)
            if status is not None and status.has_metadata:
                _logger.info(
                    "Metadata recebida: {hash} (seeders={seeds} peers={peers})",
                    hash=handle,
                    seeds=status.num_seeds,
                    peers=status.num_peers,
                )
                return
            if time.monotonic() >= deadline:
                raise TorrentTimeoutError(f"Timeout aguardando metadata de {handle}.")
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)

    async def _select_video_file(self, handle: str, file_index: int | None) -> TorrentFile:
        try:
            files = await self._backend.files(handle)
        except (QBittorrentAuthError, QBittorrentUnavailableError) as exc:
            raise TorrentResolutionError(f"Falha ao listar arquivos de {handle}: {exc}") from exc

        if file_index is not None:
            for file in files:
                if file.index == file_index:
                    return file

        candidates = [
            file
            for file in files
            if Path(file.path).suffix.lower() in _VIDEO_EXTENSIONS
            and file.size_bytes >= _MIN_VIDEO_SIZE_BYTES
            and "sample" not in file.path.lower()
        ]
        if not candidates:
            raise TorrentResolutionError(f"Nenhum arquivo de vídeo encontrado em {handle}.")
        return max(candidates, key=lambda f: f.size_bytes)

    async def _wait_for_buffer(self, handle: str, video_file: TorrentFile, deadline: float) -> None:
        buffer_bytes = self._settings.torrent_buffer_mb * 1024 * 1024
        target_bytes = min(buffer_bytes, video_file.size_bytes)
        while True:
            files = await self._backend.files(handle)
            current = next((f for f in files if f.index == video_file.index), None)
            downloaded = current.downloaded_bytes if current else 0
            status = await self._get_status(handle)
            if status is not None:
                _logger.info(
                    "Download: {pct:.0%} | Seeders: {seeds} | Peers: {peers}",
                    pct=status.progress,
                    seeds=status.num_seeds,
                    peers=status.num_peers,
                )
            if downloaded >= target_bytes:
                return
            if time.monotonic() >= deadline:
                raise TorrentTimeoutError(
                    f"Timeout aguardando buffer mínimo de {handle} "
                    f"({downloaded / 1024 / 1024:.1f}MB de {target_bytes / 1024 / 1024:.1f}MB)."
                )
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)

    async def _get_status(self, handle: str) -> TorrentStatus | None:
        try:
            return await self._backend.status(handle)
        except (QBittorrentAuthError, QBittorrentUnavailableError) as exc:
            raise TorrentResolutionError(f"Falha ao consultar status de {handle}: {exc}") from exc
