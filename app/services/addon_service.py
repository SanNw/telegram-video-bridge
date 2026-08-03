"""Orquestração de addons pro bot: busca, escolha, e entrega ao `PlaybackService`.

Ponto único que `bot/` toca para tudo relacionado a addons — nunca importa
`app.addon_system` diretamente, mesma regra de camadas que `PlaybackService`
já segue para `player/streaming/telegram`.
"""

from __future__ import annotations

from app.addon_system.base import AddonHealth, SearchResult
from app.addon_system.manager import AddonInfo, AddonManager
from app.config.settings import Settings
from app.services.exceptions import InvalidSearchIndexError, NoStreamsAvailableError
from app.services.playback_service import PlaybackService
from app.utils.logging import get_logger

_logger = get_logger("services")


class AddonService:
    """Busca em addons + escolha de resultado, delegando a reprodução ao `PlaybackService`."""

    def __init__(self, settings: Settings, playback_service: PlaybackService) -> None:
        self._manager = AddonManager(settings)
        self._playback = playback_service
        self._last_results: list[SearchResult] = []

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
        return results

    async def pick(self, index: int, requested_by: int) -> int:
        """Resolve o resultado `index` (1-indexado) da última busca e enfileira para reprodução.

        Retorna a posição na fila (via `PlaybackService.play`). Levanta
        `InvalidSearchIndexError` (índice fora da última busca) ou
        `NoStreamsAvailableError` (addon não achou fonte reproduzível).
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
        best = streams[0]
        _logger.info(
            "Resolvido via addon {addon}: {title} -> {url}",
            addon=result.addon_name,
            title=result.title,
            url=best.url,
        )
        return await self._playback.play(best.url, requested_by)
