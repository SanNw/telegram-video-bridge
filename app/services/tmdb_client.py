"""Cliente HTTP para a API pública do TMDB (The Movie Database), v3.

Segue o mesmo contrato de `app.services.stremio_client.StremioAddonClient`:
nenhum método levanta exceção por falha de rede, HTTP ou payload inválido —
erros são logados e o chamador recebe uma estrutura vazia (`{}`/`[]`), para
que uma falha do TMDB nunca derrube o bot nem quebre `/find`.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.utils.logging import get_logger

_logger = get_logger("services")

_BASE_URL = "https://api.themoviedb.org/3"
# Bases estáveis documentadas pelo TMDB para montar URLs de imagem a partir de
# `poster_path`/`file_path`/`profile_path` sem uma chamada extra a `/3/configuration`.
_POSTER_BASE_URL = "https://image.tmdb.org/t/p/w500"
_BACKDROP_BASE_URL = "https://image.tmdb.org/t/p/w780"
_PROFILE_BASE_URL = "https://image.tmdb.org/t/p/w185"
_DEFAULT_TIMEOUT_SECONDS = 10.0


class TMDBClient:
    """Cliente assíncrono para a API TMDB v3, autenticado via bearer token.

    Nenhum método levanta exceção: erros são logados e o chamador recebe
    `[]`/`None`, mesma convenção de `StremioAddonClient`.
    """

    def __init__(
        self,
        api_key: str,
        language: str = "pt-BR",
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._language = language
        self._client = httpx.AsyncClient(
            timeout=timeout_seconds,
            headers={"Authorization": f"Bearer {api_key}", "accept": "application/json"},
        )

    async def close(self) -> None:
        """Fecha a conexão HTTP subjacente. Chame ao descartar o cliente."""
        await self._client.aclose()

    async def search_movie(self, query: str, year: int | None = None) -> list[dict[str, Any]]:
        """Busca filmes por título.

        Args:
            query: Título (ou parte dele) a buscar.
            year: Se informado, filtra por ano de lançamento primário
                (`primary_release_year`), para desambiguar remakes/sequências.

        Returns:
            Lista de resultados (`results` da resposta), ou `[]` em falha.
        """
        params: dict[str, Any] = {"query": query, "language": self._language}
        if year is not None:
            params["primary_release_year"] = year
        data = await self._get_json(f"{_BASE_URL}/search/movie", params)
        results = data.get("results", [])
        return results if isinstance(results, list) else []

    async def get_movie_details(self, movie_id: int) -> dict[str, Any] | None:
        """Busca os detalhes completos de um filme (sinopse, nota, gêneros, pôster).

        Returns:
            Dict com os campos do filme, ou `None` se a requisição falhar.
        """
        data = await self._get_json(
            f"{_BASE_URL}/movie/{movie_id}", {"language": self._language}
        )
        return data or None

    async def get_movie_credits(self, movie_id: int) -> dict[str, Any] | None:
        """Busca o elenco de um filme (`cast`, já ordenado por relevância pelo TMDB).

        Sem filtro de idioma — nomes de elenco não variam por idioma.

        Returns:
            Dict com os campos de créditos, ou `None` se a requisição falhar.
        """
        data = await self._get_json(f"{_BASE_URL}/movie/{movie_id}/credits", {})
        return data or None

    async def get_movie_images(self, movie_id: int) -> dict[str, Any] | None:
        """Busca as imagens de um filme (`backdrops`, `posters`, `logos`).

        Sem filtro de idioma — evita descartar backdrops language-neutral.

        Returns:
            Dict com os campos de imagens, ou `None` se a requisição falhar.
        """
        data = await self._get_json(f"{_BASE_URL}/movie/{movie_id}/images", {})
        return data or None

    @staticmethod
    def poster_url(poster_path: str | None) -> str | None:
        """Monta a URL completa do pôster a partir do `poster_path` da API, ou `None`."""
        if not poster_path:
            return None
        return f"{_POSTER_BASE_URL}{poster_path}"

    @staticmethod
    def backdrop_url(file_path: str | None) -> str | None:
        """Monta a URL completa de uma foto do filme a partir do `file_path`, ou `None`."""
        if not file_path:
            return None
        return f"{_BACKDROP_BASE_URL}{file_path}"

    @staticmethod
    def profile_url(profile_path: str | None) -> str | None:
        """Monta a URL completa da foto de um membro do elenco, ou `None`."""
        if not profile_path:
            return None
        return f"{_PROFILE_BASE_URL}{profile_path}"

    async def _get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        """GET genérico com tratamento de timeout, status HTTP e JSON malformado.

        Nunca levanta: qualquer falha vira log + `{}`.
        """
        try:
            response = await self._client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException:
            _logger.error("Timeout ao consultar TMDB: {url}", url=url)
            return {}
        except httpx.HTTPStatusError as exc:
            _logger.error(
                "TMDB respondeu {status}: {url}", status=exc.response.status_code, url=url
            )
            return {}
        except httpx.HTTPError as exc:
            _logger.error("Erro de rede ao consultar TMDB {url}: {err}", url=url, err=exc)
            return {}
        except ValueError as exc:  # json.JSONDecodeError é subclasse de ValueError
            _logger.error("JSON inválido do TMDB {url}: {err}", url=url, err=exc)
            return {}
        if not isinstance(data, dict):
            _logger.error("Resposta inesperada (não é objeto JSON) de {url}", url=url)
            return {}
        return data
