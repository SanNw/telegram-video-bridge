"""Orquestra o ciclo de vida dos addons: descoberta, habilitar/desabilitar/recarregar/remover,
e as chamadas agregadas (`search`) ou pontuais (`get_streams`) que atravessam addons de terceiros.

Isolamento de falha é o requisito central aqui: um addon que trava, estoura
timeout ou levanta exceção nunca derruba o processo nem os outros addons —
vira log + resultado vazio.
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass

from app.addon_system.base import AddonHealth, SearchResult, StreamCandidate
from app.addon_system.cache import TTLCache
from app.addon_system.exceptions import AddonError, AddonNotFoundError, AddonTimeoutError
from app.addon_system.loader import AddonLoader, LoadedAddon
from app.addon_system.persistence import AddonStatePersistence
from app.config.settings import Settings
from app.utils.logging import get_logger

_logger = get_logger("services")


@dataclass(frozen=True, slots=True)
class AddonInfo:
    """Snapshot de um addon carregado, para `/addons` e `/addon info`."""

    name: str
    version: str
    description: str
    enabled: bool


class AddonManager:
    """Ponto único de acesso aos addons carregados. `services/` fala só com esta classe."""

    def __init__(self, settings: Settings, loader: AddonLoader | None = None) -> None:
        self._settings = settings
        self._loader = loader or AddonLoader(settings.addons_path, settings.addons_config_path)
        self._persistence = AddonStatePersistence(settings.addons_state_path)
        self._addons: dict[str, LoadedAddon] = {}
        self._enabled: dict[str, bool] = {}
        self._search_cache: TTLCache[str, list[SearchResult]] = TTLCache(
            settings.addon_search_cache_ttl_seconds
        )

    async def discover(self) -> None:
        """Carrega todos os addons presentes em `addons_path`. Falha isolada por addon."""
        saved_state = await self._persistence.load()
        for name in self._loader.discover_names():
            await self._load_one(name, default_enabled=saved_state.get(name, True))

    async def _load_one(self, name: str, *, default_enabled: bool) -> None:
        try:
            loaded = self._loader.load(name)
        except AddonError as exc:
            _logger.error("Falha ao carregar addon {name}: {err}", name=name, err=exc)
            return
        self._addons[name] = loaded
        self._enabled[name] = default_enabled
        _logger.info(
            "Addon carregado: {name} v{version} ({state})",
            name=name,
            version=loaded.manifest.version,
            state="habilitado" if default_enabled else "desabilitado",
        )

    def list_addons(self) -> list[AddonInfo]:
        """Todos os addons carregados (habilitados ou não), para `/addons`."""
        return [
            AddonInfo(
                name=name,
                version=loaded.manifest.version,
                description=loaded.manifest.description,
                enabled=self._enabled.get(name, False),
            )
            for name, loaded in sorted(self._addons.items())
        ]

    def addon_info(self, name: str) -> AddonInfo:
        """Detalhes de um addon específico. Levanta `AddonNotFoundError`."""
        loaded = self._get_loaded(name)
        return AddonInfo(
            name=name,
            version=loaded.manifest.version,
            description=loaded.manifest.description,
            enabled=self._enabled.get(name, False),
        )

    async def health(self, name: str) -> AddonHealth:
        """Consulta `BaseAddon.health()` do addon, com timeout. Nunca levanta."""
        loaded = self._get_loaded(name)
        try:
            async with asyncio.timeout(self._settings.addon_search_timeout_seconds):
                return await loaded.instance.health()
        except Exception as exc:  # noqa: BLE001 - health nunca deve propagar erro de terceiro
            return AddonHealth(healthy=False, detail=str(exc))

    async def enable(self, name: str) -> None:
        """Habilita um addon já carregado. Levanta `AddonNotFoundError`."""
        self._get_loaded(name)
        self._enabled[name] = True
        await self._save_state()

    async def disable(self, name: str) -> None:
        """Desabilita um addon já carregado. Levanta `AddonNotFoundError`."""
        self._get_loaded(name)
        self._enabled[name] = False
        await self._save_state()

    async def reload(self, name: str) -> None:
        """Recarrega o código do addon a partir do disco, preservando enabled/disabled.

        Se o reload falhar, o addon anterior continua carregado e ativo — uma
        recarga malsucedida não deve deixar o sistema sem o addon.
        """
        previous = self._get_loaded(name)  # AddonNotFoundError se nunca existiu
        was_enabled = self._enabled.get(name, True)
        loaded = self._loader.load(name)  # AddonError propaga, addon antigo intacto
        self._addons[name] = loaded
        self._enabled[name] = was_enabled
        self._search_cache.clear()
        await self._close_quietly(previous)
        _logger.info("Addon recarregado: {name}", name=name)

    async def uninstall(self, name: str) -> None:
        """Remove o addon: descarrega e apaga a pasta em `addons_path`. Ação destrutiva."""
        previous = self._get_loaded(name)
        del self._addons[name]
        self._enabled.pop(name, None)
        await self._save_state()
        await self._close_quietly(previous)
        addon_dir = self._settings.addons_path / name
        if addon_dir.is_dir():
            shutil.rmtree(addon_dir)
        _logger.warning("Addon removido do disco: {name} ({path})", name=name, path=addon_dir)

    @staticmethod
    async def _close_quietly(loaded: LoadedAddon) -> None:
        try:
            await loaded.instance.close()
        except (
            Exception
        ) as exc:  # noqa: BLE001 - liberar recurso de terceiro não pode derrubar o reload
            _logger.warning(
                "Erro ao fechar addon antigo {name}: {err}", name=loaded.manifest.name, err=exc
            )

    async def _save_state(self) -> None:
        await self._persistence.save(self._enabled)

    def _get_loaded(self, name: str) -> LoadedAddon:
        loaded = self._addons.get(name)
        if loaded is None:
            raise AddonNotFoundError(f"Addon não encontrado: {name!r}")
        return loaded

    async def search(self, query: str) -> list[SearchResult]:
        """Busca `query` em todos os addons habilitados, em paralelo, com cache TTL.

        Falha ou timeout de um addon vira lista vazia pra ele — nunca derruba
        a busca dos outros nem levanta pro chamador.
        """
        cached = self._search_cache.get(query)
        if cached is not None:
            return cached

        enabled_addons = [
            (name, loaded)
            for name, loaded in self._addons.items()
            if self._enabled.get(name, False)
        ]
        results_per_addon = await asyncio.gather(
            *(self._search_one(name, loaded, query) for name, loaded in enabled_addons)
        )
        aggregated = [item for sublist in results_per_addon for item in sublist]
        self._search_cache.set(query, aggregated)
        return aggregated

    async def _search_one(self, name: str, loaded: LoadedAddon, query: str) -> list[SearchResult]:
        try:
            async with asyncio.timeout(self._settings.addon_search_timeout_seconds):
                results = await loaded.instance.search(query)
        except Exception as exc:  # noqa: BLE001 - isola falha de um addon dos demais
            _logger.error(
                "Addon {name} falhou em search({query!r}): {err}", name=name, query=query, err=exc
            )
            return []
        return [
            SearchResult(
                media_id=result.media_id, title=result.title, year=result.year, addon_name=name
            )
            for result in results
        ]

    async def get_streams(self, addon_name: str, media_id: str) -> list[StreamCandidate]:
        """Resolve streams de `media_id` no addon `addon_name`. Levanta `AddonTimeoutError`."""
        loaded = self._get_loaded(addon_name)
        try:
            async with asyncio.timeout(self._settings.addon_streams_timeout_seconds):
                return await loaded.instance.get_streams(media_id)
        except TimeoutError as exc:
            raise AddonTimeoutError(f"Timeout ao resolver streams em {addon_name}") from exc
