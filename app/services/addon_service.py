"""Orquestração de addons pro bot: busca, escolha, e entrega ao `PlaybackService`.

Ponto único que `bot/` toca para tudo relacionado a addons — nunca importa
`app.addon_system` diretamente, mesma regra de camadas que `PlaybackService`
já segue para `player/streaming/telegram`.
"""

from __future__ import annotations

from app.addon_system.base import AddonHealth, SearchResult, StreamCandidate
from app.addon_system.manager import AddonInfo, AddonManager
from app.config.settings import Settings
from app.services.exceptions import (
    InvalidSearchIndexError,
    NoStreamsAvailableError,
    TorrentTimeoutError,
)
from app.services.playback_service import PlaybackService
from app.services.torrent_service import TorrentService
from app.utils.logging import get_logger

_logger = get_logger("services")

# Limite de upload de arquivo do Telegram. Aplicado aqui — não só dentro do
# addon `stremio`, que já filtra por conta própria (`plugin.py::_rank_candidates`)
# — porque outros addons podem devolver `size_bytes` sem aplicar esse corte.
# Tamanho desconhecido (`None`) não é descartado: não dá pra confirmar violação.
_MAX_STREAM_SIZE_BYTES = 4 * 1024**3


class AddonService:
    """Busca em addons + escolha de resultado, delegando a reprodução ao `PlaybackService`."""

    def __init__(
        self,
        settings: Settings,
        playback_service: PlaybackService,
        torrent_service: TorrentService,
    ) -> None:
        self._manager = AddonManager(settings)
        self._playback = playback_service
        self._torrent = torrent_service
        self._last_results: list[SearchResult] = []
        self._candidates: dict[str, tuple[SearchResult, StreamCandidate]] = {}

    async def start(self) -> None:
        """Descobre e carrega todos os addons presentes em `addons_path`."""
        await self._manager.discover()

    def list_addons(self) -> list[AddonInfo]:
        """Todos os addons carregados, para `/addons`."""
        return self._manager.list_addons()

    def addon_info(self, name: str) -> AddonInfo:
        """Detalhes de um addon. Levanta `AddonNotFoundError` (tratada por `bot/`)."""
        return self._manager.addon_info(name)

    async def addon_health(self, name: str) -> AddonHealth:
        """Healthcheck de um addon. Levanta `AddonNotFoundError`."""
        return await self._manager.health(name)

    async def enable(self, name: str) -> None:
        """Habilita um addon. Levanta `AddonNotFoundError`."""
        await self._manager.enable(name)

    async def disable(self, name: str) -> None:
        """Desabilita um addon. Levanta `AddonNotFoundError`."""
        await self._manager.disable(name)

    async def reload(self, name: str) -> None:
        """Recarrega o código de um addon a partir do disco. Levanta `AddonError`."""
        await self._manager.reload(name)

    async def uninstall(self, name: str) -> None:
        """Remove um addon (descarrega + apaga do disco). Levanta `AddonNotFoundError`."""
        await self._manager.uninstall(name)

    async def find(self, query: str) -> list[SearchResult]:
        """Busca `query` em todos os addons habilitados; guarda os resultados para `/pick`."""
        results = await self._manager.search(query)
        self._last_results = results
        self._candidates = {}
        return results

    async def pick(self, index: int, requested_by: int) -> int:
        """Resolve o resultado `index` (1-indexado) da última busca e enfileira para reprodução.

        Retorna a posição na fila (via `PlaybackService.play`). Levanta
        `InvalidSearchIndexError` (índice fora da última busca) ou
        `NoStreamsAvailableError` (addon não achou fonte reproduzível, ou
        todos os candidatos torrent estouraram o timeout de resolução — ver
        `_play_candidate`).
        """
        if index < 1 or index > len(self._last_results):
            raise InvalidSearchIndexError(
                f"Posição inválida: {index}. Use /find de novo se a lista expirou."
            )
        result = self._last_results[index - 1]
        streams = await self._manager.get_streams(result.addon_name, result.media_id)
        if not streams:
            raise NoStreamsAvailableError(
                f"Nenhuma fonte reproduzível encontrada para {result.title!r}."
            )
        for candidate in streams:
            try:
                return await self._play_candidate(result, candidate, requested_by)
            except TorrentTimeoutError as exc:
                _logger.warning(
                    "Candidato torrent expirou para {title}, tentando o próximo: {err}",
                    title=result.title,
                    err=exc,
                )
        raise NoStreamsAvailableError(
            f"Todos os candidatos torrent expiraram para {result.title!r}."
        )

    async def _play_candidate(
        self, result: SearchResult, candidate: StreamCandidate, requested_by: int
    ) -> int:
        """Enfileira `candidate` para reprodução, resolvendo via torrent se necessário.

        `candidate.url` preenchido: reproduzido direto (fluxo de sempre).
        Sem `url`: resolvido primeiro via `TorrentService` (infoHash/magnet)
        para um caminho local, só então enfileirado — mesma interface
        `PlaybackService.play`, o torrent já chega como arquivo local.
        """
        if candidate.url:
            source = candidate.url
        else:
            source = await self._torrent.resolve(candidate)
        _logger.info(
            "Resolvido via addon {addon}: {title} -> {source}",
            addon=result.addon_name,
            title=result.title,
            source=source,
        )
        return await self._playback.play(source, requested_by)

    async def resolve_top_candidates(self) -> list[tuple[str, SearchResult, StreamCandidate]]:
        """Resolve o melhor stream de cada addon distinto na última busca (`/find`).

        Usado para montar os botões inline da Rich Message do TMDB: no máximo
        1 candidato por addon (o primeiro resultado desse addon na busca,
        melhor stream dentre as que respeitam `_MAX_STREAM_SIZE_BYTES` — nem
        todo addon filtra por tamanho por conta própria, ex.: `archive_org`
        não carrega `size_bytes`). Falha ou timeout ao resolver um addon, ou
        todas as streams excedendo o limite, só omite o candidato desse
        addon, nunca derruba os demais (mesmo isolamento de
        `AddonManager._search_one`). Reseta e repopula `self._candidates` —
        tokens de uma resolução anterior deixam de ser válidos em
        `pick_candidate`.
        """
        self._candidates = {}
        seen_addons: set[str] = set()
        candidates: list[tuple[str, SearchResult, StreamCandidate]] = []
        for result in self._last_results:
            if result.addon_name in seen_addons:
                continue
            seen_addons.add(result.addon_name)
            try:
                streams = await self._manager.get_streams(result.addon_name, result.media_id)
            except Exception as exc:  # noqa: BLE001 - isola falha de um addon dos demais
                _logger.warning(
                    "Addon {addon} falhou ao resolver stream para botão: {err}",
                    addon=result.addon_name,
                    err=exc,
                )
                continue
            best = next(
                (
                    s
                    for s in streams
                    if s.size_bytes is None or s.size_bytes <= _MAX_STREAM_SIZE_BYTES
                ),
                None,
            )
            if best is None:
                continue
            token = str(len(candidates))
            self._candidates[token] = (result, best)
            candidates.append((token, result, best))
        return candidates

    async def pick_candidate(self, token: str, requested_by: int) -> tuple[str, int]:
        """Enfileira o candidato de `token` (de `resolve_top_candidates`) para reprodução.

        Retorna `(addon_name, posição na fila)`. Levanta `InvalidSearchIndexError`
        se `token` não existir (ex.: uma busca mais nova já invalidou os tokens
        anteriores) — mesma exceção de `pick()`, já tratada em `bot/handlers/addons.py`.

        Diferente de `pick()`, não tenta um próximo candidato em caso de
        `TorrentTimeoutError`/`TorrentResolutionError`: `resolve_top_candidates`
        já escolhe um único candidato por addon para este fluxo (clique de
        botão), então não há "próximo" para tentar — a exceção propaga para
        `bot/handlers/addons.py`, que já sabe converter em alerta pedindo
        `/find` de novo.
        """
        entry = self._candidates.get(token)
        if entry is None:
            raise InvalidSearchIndexError("Esse botão expirou. Use /find de novo.")
        result, candidate = entry
        position = await self._play_candidate(result, candidate, requested_by)
        return result.addon_name, position
