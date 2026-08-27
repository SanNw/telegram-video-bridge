"""Orquestração de addons pro bot: busca, escolha, e entrega ao `PlaybackService`.

Ponto único que `bot/` toca para tudo relacionado a addons — nunca importa
`app.addon_system` diretamente, mesma regra de camadas que `PlaybackService`
já segue para `player/streaming/telegram`.

`find()` também filtra os resultados brutos dos addons por relevância ao
título pesquisado (TMDB primeiro, fuzzy match como fallback — ver
`_filter_results`), uma junção dos domínios addon+TMDB que não cabe em
nenhum dos dois isoladamente. Por isso o construtor depende de `TMDBService`.
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

from app.addon_system.base import AddonHealth, SearchResult, StreamCandidate
from app.addon_system.manager import AddonInfo, AddonManager
from app.config.settings import Settings
from app.services.exceptions import (
    InvalidSearchIndexError,
    NoStreamsAvailableError,
    TorrentTimeoutError,
)
from app.services.playback_service import PlaybackService
from app.services.stremio_client import StremioAddonClient
from app.services.tmdb_service import TMDBMetadata, TMDBMovie, TMDBService
from app.services.torrent_service import TorrentService
from app.utils.language_detection import detect_language_flag, has_portuguese_audio
from app.utils.logging import get_logger
from app.utils.title_matching import matches_any, matches_movie_release, stream_resolution

_logger = get_logger("services")

# Limite de upload de arquivo do Telegram. Aplicado aqui — não só dentro do
# addon `stremio`, que já filtra por conta própria (`plugin.py::_rank_candidates`)
# — porque outros addons podem devolver `size_bytes` sem aplicar esse corte.
# Tamanho desconhecido (`None`) não é descartado: não dá pra confirmar violação.
_MAX_STREAM_SIZE_BYTES = 4 * 1024**3

# Score mínimo (rapidfuzz.fuzz.token_set_ratio, 0-100) para um resultado de
# addon ser considerado o mesmo filme que o título confirmado pelo TMDB.
# Título "limpo" do TMDB vs. título de addon (que pode ter ruído tipo
# "1080p Dublado" — token_set_ratio ignora isso bem) permite um corte mais
# rígido do que o fallback abaixo.
_TMDB_MATCH_THRESHOLD = 65.0
# Score mínimo contra a query crua do usuário (sem normalização do TMDB) —
# mais tolerante, usado só quando o TMDB não confirma o filme.
_FUZZY_FALLBACK_THRESHOLD = 55.0
_OPENSUBTITLES_URL = "https://opensubtitles-v3.strem.io"

# IMDb ID -> tupla de file_id conhecidos que corrigem problemas de sincronia
# conhecidos.
_PINNED_SUBTITLES: dict[str, tuple[int, ...]] = {"tt0089881": (292268,)}


class AddonService:
    """Busca em addons + escolha de resultado, delegando a reprodução ao `PlaybackService`."""

    def __init__(
        self,
        settings: Settings,
        playback_service: PlaybackService,
        torrent_service: TorrentService,
        tmdb_service: TMDBService,
    ) -> None:
        self._manager = AddonManager(settings)
        self._playback = playback_service
        self._torrent = torrent_service
        self._tmdb = tmdb_service
        self._last_results: list[SearchResult] = []
        self._last_metadata: TMDBMetadata | None = None
        self._catalog: list[TMDBMovie] = []
        self._candidates: dict[str, tuple[SearchResult, StreamCandidate]] = {}
        self._settings = settings
        self._subtitles = StremioAddonClient(_OPENSUBTITLES_URL)

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
        """Busca `query` em todos os addons habilitados; guarda os resultados para `/pick`.

        Filtra os resultados brutos por relevância ao filme pesquisado antes
        de guardá-los — ver `_filter_results`. `last_metadata()` expõe o
        metadata do TMDB já buscado aqui, para `bot/` não repetir a chamada.
        """
        raw_results = await self._manager.search(query)
        metadata = await self._tmdb.enrich(query, None) if self._tmdb.enabled else None
        filtered = self._filter_results(raw_results, query, metadata)
        self._last_results = filtered
        self._last_metadata = metadata
        self._candidates = {}
        return filtered

    async def search_catalog(self, query: str) -> list[TMDBMovie]:
        """Busca exclusivamente no TMDB e guarda o catálogo para os callbacks."""
        self._catalog = await self._tmdb.search(query)
        self._last_results = []
        self._last_metadata = None
        self._candidates = {}
        return self._catalog

    def catalog(self) -> list[TMDBMovie]:
        return self._catalog

    async def select_catalog_movie(
        self, index: int
    ) -> tuple[TMDBMovie, TMDBMetadata, list[SearchResult]]:
        """Seleciona um filme do TMDB e só então procura fontes nos addons."""
        if index < 0 or index >= len(self._catalog):
            raise InvalidSearchIndexError("Esse resultado expirou. Use /find de novo.")
        movie = self._catalog[index]
        metadata = await self._tmdb.details(movie.id)
        if metadata is None:
            raise NoStreamsAvailableError("O TMDB não conseguiu carregar os detalhes do filme.")
        year = (
            int(movie.release_date[:4])
            if movie.release_date and movie.release_date[:4].isdigit()
            else None
        )
        raw_results = await self._manager.search(f"{movie.title} {year}" if year else movie.title)
        filtered = self._filter_results(raw_results, movie.title, metadata)
        self._last_results = filtered
        self._last_metadata = metadata
        self._candidates = {}
        return movie, metadata, filtered

    def last_metadata(self) -> TMDBMetadata | None:
        """Metadata do TMDB (se houver) obtido na última chamada a `find()`."""
        return self._last_metadata

    @staticmethod
    def _filter_results(
        raw_results: list[SearchResult], query: str, metadata: TMDBMetadata | None
    ) -> list[SearchResult]:
        """Filtra `raw_results` por relevância: TMDB primeiro, fuzzy match como fallback.

        Rede de segurança: nunca esconde resultados que só um filtro grosseiro
        demais reprovaria — se o filtro TMDB zerar a lista, cai para o fuzzy
        fallback sobre `raw_results`; se isso também zerar, devolve
        `raw_results` sem filtrar.
        """
        if not raw_results:
            return raw_results

        if metadata is not None:
            references = [metadata.title, metadata.original_title]
            filtered = [
                r for r in raw_results if matches_any(r.title, references, _TMDB_MATCH_THRESHOLD)
            ]
            if filtered:
                return filtered

        fuzzy_filtered = [
            r for r in raw_results if matches_any(r.title, [query], _FUZZY_FALLBACK_THRESHOLD)
        ]
        return fuzzy_filtered or raw_results

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
        subtitle_path = (
            None
            if has_portuguese_audio(f"{candidate.title} {candidate.quality or ''}")
            else await self._prepare_subtitle(result)
        )
        movie_context = {
            "media_id": result.media_id.rsplit(":", 1)[-1],
            "display_title": result.title,
        }
        if subtitle_path is None:
            return await self._playback.play(source, requested_by, **movie_context)
        return await self._playback.play(source, requested_by, subtitle_path, **movie_context)

    async def _prepare_subtitle(self, result: SearchResult) -> str | None:
        """Baixa a melhor legenda PT-BR/pt quando o catálogo fornece IMDb ID."""
        imdb_id = result.media_id.rsplit(":", 1)[-1]
        if not imdb_id.startswith("tt") or not imdb_id[2:].isdigit():
            return None
        return await self.prepare_subtitle(imdb_id, result.title)

    async def prepare_subtitle(self, imdb_id: str, title: str) -> str | None:
        subtitles = await self._subtitles.get_subtitles("movie", imdb_id)
        if not subtitles:
            return None
        selected = self._pick_portuguese_subtitle(subtitles, imdb_id)
        if selected is None or not isinstance(selected.get("url"), str):
            return None
        content = await self._subtitles.download_subtitle(selected["url"])
        if content is None:
            return None
        path = self._settings.qbittorrent_local_path / ".subtitles" / f"{imdb_id}-pt.srt"
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, content)
        _logger.info("Legenda em português preparada para {title}.", title=title)
        return str(path)

    @staticmethod
    def _pick_portuguese_subtitle(
        subtitles: list[dict[str, Any]], imdb_id: str
    ) -> dict[str, Any] | None:
        """Escolhe a melhor legenda PT a partir de uma lista bruta do addon.

        Critérios, em ordem:

        1. Correspondência exata por `file_id` em uma tabela de correções
           conhecida (`_PINNED_SUBTITLES`). Usado quando o addon devolve uma
           faixa dessincronizada para um filme específico, mas existe uma
           versão alternativa bem sincronizada que o próprio addon não sabe
           priorizar — caso conhecido: *Ran* (tt0089881), onde a faixa 134344
           chega 2-3 s adiantada e a 292268 é a correta.
        2. Filtro por idioma (`pob` > `por` > `pt-BR` > `pt`), descartando
           faixas marcadas como `hearing_impaired`.
        3. Qualquer faixa em um dos idiomas acima, com ou sem flag de HI
           (fallback).
        """
        pinned_ids = _PINNED_SUBTITLES.get(imdb_id, ())
        for pinned in pinned_ids:
            for item in subtitles:
                if item.get("id") == pinned and item.get("url"):
                    return item
                for file_entry in item.get("files") or ():
                    if file_entry.get("file_id") != pinned:
                        continue
                    if file_entry.get("url"):
                        return cast(dict[str, Any], file_entry)
                    if item.get("url"):
                        return item
        for language in ("pob", "por", "pt-BR", "pt"):
            for item in subtitles:
                if item.get("lang") != language:
                    continue
                if item.get("hearing_impaired") is True:
                    continue
                return item
        for language in ("pob", "por", "pt-BR", "pt"):
            for item in subtitles:
                if item.get("lang") == language:
                    return item
        return None

    async def close(self) -> None:
        await self._subtitles.close()

    async def resolve_candidates(self) -> list[tuple[str, SearchResult, StreamCandidate]]:
        """Coleta e ordena todas as fontes 1080p/720p da última busca."""
        self._candidates = {}
        metadata = self._last_metadata
        year = (
            int(metadata.release_date[:4])
            if metadata and metadata.release_date and metadata.release_date[:4].isdigit()
            else None
        )
        titles = [metadata.title, metadata.original_title] if metadata else []
        collected: list[tuple[SearchResult, StreamCandidate]] = []
        for result in self._last_results:
            try:
                streams = await self._manager.get_streams(result.addon_name, result.media_id)
            except Exception as exc:  # noqa: BLE001 - isola falha de um addon dos demais
                _logger.warning(
                    "Addon {addon} falhou ao resolver stream para botão: {err}",
                    addon=result.addon_name,
                    err=exc,
                )
                continue
            for candidate in streams:
                label = f"{candidate.title} {candidate.quality or ''}"
                if stream_resolution(label) not in (1080, 720):
                    continue
                if titles and not matches_movie_release(label, titles, year):
                    continue
                if (
                    candidate.size_bytes is not None
                    and candidate.size_bytes > _MAX_STREAM_SIZE_BYTES
                ):
                    continue
                collected.append((result, candidate))

        collected.sort(key=_candidate_sort_key)
        output: list[tuple[str, SearchResult, StreamCandidate]] = []
        seen_sources: set[tuple[object, ...]] = set()
        for result, candidate in collected:
            source_key = _candidate_source_key(candidate)
            if source_key in seen_sources:
                continue
            seen_sources.add(source_key)
            token = str(len(output))
            self._candidates[token] = (result, candidate)
            output.append((token, result, candidate))
        return output

    async def pick_candidate(self, token: str, requested_by: int) -> tuple[str, int]:
        """Enfileira o candidato de `token` (de `resolve_candidates`) para reprodução.

        Retorna `(addon_name, posição na fila)`. Levanta `InvalidSearchIndexError`
        se `token` não existir (ex.: uma busca mais nova já invalidou os tokens
        anteriores) — mesma exceção de `pick()`, já tratada em `bot/handlers/addons.py`.

        Diferente de `pick()`, não tenta um próximo candidato em caso de
        `TorrentTimeoutError`/`TorrentResolutionError`: `resolve_candidates`
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

    async def play_resolved_candidate(
        self, result: SearchResult, candidate: StreamCandidate, requested_by: int
    ) -> tuple[str, int]:
        """Reproduz um candidato já isolado no fluxo efêmero do usuário."""
        position = await self._play_candidate(result, candidate, requested_by)
        return result.addon_name, position


def _candidate_sort_key(
    item: tuple[SearchResult, StreamCandidate],
) -> tuple[bool, int, int, int, bool, int]:
    _, candidate = item
    label = f"{candidate.title} {candidate.quality or ''}"
    flag = detect_language_flag(label)
    language_rank = {"🇧🇷": 0, "🇧🇷🇺🇸": 1, "🇺🇸": 2}.get(flag, 3) if flag else 3
    return (
        candidate.seeds is None,
        -(candidate.seeds or 0),
        -(stream_resolution(label) or 0),
        language_rank,
        candidate.size_bytes is None,
        candidate.size_bytes or 0,
    )


def _candidate_source_key(candidate: StreamCandidate) -> tuple[object, ...]:
    if candidate.url:
        return ("url", candidate.url, candidate.file_index)
    if candidate.info_hash:
        return ("info_hash", candidate.info_hash.lower(), candidate.file_index)
    if candidate.magnet:
        return ("magnet", candidate.magnet, candidate.file_index)
    return ("candidate", candidate)
