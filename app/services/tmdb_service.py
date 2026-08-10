"""Orquestração do enriquecimento de resultados de busca com metadados do TMDB.

Ponto único que `bot/` toca para tudo relacionado a TMDB — mesma regra de
camadas que `AddonService` já segue para `app.addon_system`. Toda a
integração é opcional: sem `tmdb_api_key` configurada, o serviço fica inerte
e `enrich()` sempre devolve `None`, nunca impedindo `/find` de funcionar.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.config.settings import Settings
from app.services.tmdb_client import TMDBClient

# Limites pra não estourar a Rich Message com mídia demais (o protocolo aceita
# até 50 blocos de mídia no total, mas um carrossel gigante não agrega).
_MAX_CAST_MEMBERS = 6
_MAX_BACKDROPS = 4


@dataclass(frozen=True, slots=True)
class TMDBCastMember:
    """Um membro do elenco, pronto para exibição numa Rich Message."""

    name: str
    profile_url: str | None


@dataclass(frozen=True, slots=True)
class TMDBMetadata:
    """Metadados de um filme, prontos para exibição numa Rich Message."""

    title: str
    original_title: str | None
    overview: str | None
    poster_url: str | None
    vote_average: float | None
    genres: list[str]
    release_date: str | None
    cast: list[TMDBCastMember]
    backdrop_urls: list[str]


class TMDBService:
    """Busca o primeiro filme correspondente no TMDB e devolve metadados prontos.

    Instancia `TMDBClient` apenas se `settings.tmdb_api_key` estiver setada;
    caso contrário `enabled` é `False` e `enrich()` sempre devolve `None`.
    """

    def __init__(self, settings: Settings) -> None:
        api_key = settings.tmdb_api_key
        self._client = (
            TMDBClient(
                api_key.get_secret_value(),
                language=settings.tmdb_language,
                timeout_seconds=settings.tmdb_request_timeout_seconds,
            )
            if api_key is not None
            else None
        )

    @property
    def enabled(self) -> bool:
        """`True` se uma `tmdb_api_key` foi configurada."""
        return self._client is not None

    async def enrich(self, title: str, year: int | None) -> TMDBMetadata | None:
        """Busca `title` (opcionalmente filtrando por `year`) e devolve o primeiro resultado.

        Nunca lança: devolve `None` se a integração estiver desabilitada, a
        busca não encontrar nada, ou os detalhes do filme não puderem ser
        obtidos.
        """
        if self._client is None:
            return None
        results = await self._client.search_movie(title, year=year)
        if not results:
            return None
        movie_id = results[0].get("id")
        if not isinstance(movie_id, int):
            return None
        details = await self._client.get_movie_details(movie_id)
        if details is None:
            return None
        genres = details.get("genres", [])
        genre_names = [g["name"] for g in genres if isinstance(g, dict) and "name" in g]

        credits_data, images_data = await asyncio.gather(
            self._client.get_movie_credits(movie_id), self._client.get_movie_images(movie_id)
        )
        cast_entries = (credits_data or {}).get("cast", [])
        cast = [
            TMDBCastMember(
                name=c["name"], profile_url=TMDBClient.profile_url(c.get("profile_path"))
            )
            for c in cast_entries[:_MAX_CAST_MEMBERS]
            if isinstance(c, dict) and c.get("name")
        ]
        backdrop_entries = (images_data or {}).get("backdrops", [])
        backdrop_urls = [
            url
            for b in backdrop_entries[:_MAX_BACKDROPS]
            if isinstance(b, dict) and (url := TMDBClient.backdrop_url(b.get("file_path")))
        ]

        return TMDBMetadata(
            title=details.get("title") or title,
            original_title=details.get("original_title") or None,
            overview=details.get("overview") or None,
            poster_url=TMDBClient.poster_url(details.get("poster_path")),
            vote_average=details.get("vote_average"),
            genres=genre_names,
            release_date=details.get("release_date") or None,
            cast=cast,
            backdrop_urls=backdrop_urls,
        )

    async def close(self) -> None:
        """Fecha a conexão HTTP subjacente, se a integração estiver habilitada."""
        if self._client is not None:
            await self._client.close()
