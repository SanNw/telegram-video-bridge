"""Testes de `app/services/tmdb_client.py`.

Nada disso bate na rede real: o `httpx.AsyncClient` interno (`instance._client`)
é substituído por um `AsyncMock`, mesmo padrão de `test_services_stremio_client.py`.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.services.tmdb_client import TMDBClient


class _FakeResponse:
    """Dublê mínimo de `httpx.Response`: `raise_for_status()` + `json()`."""

    def __init__(self, json_data: Any = None, status_error: bool = False) -> None:
        self._json_data = json_data
        self._status_error = status_error
        self.status_code = 500 if status_error else 200

    def raise_for_status(self) -> None:
        if self._status_error:
            raise httpx.HTTPStatusError(
                "error", request=MagicMock(), response=MagicMock(status_code=self.status_code)
            )

    def json(self) -> Any:
        return self._json_data


@pytest.fixture
def client() -> TMDBClient:
    instance = TMDBClient("test-api-key")
    instance._client = AsyncMock()
    return instance


async def test_search_movie_returns_results_list(client: TMDBClient) -> None:
    client._client.get.return_value = _FakeResponse(
        {"results": [{"id": 1, "title": "Inception"}, {"id": 2, "title": "Inception 2"}]}
    )

    results = await client.search_movie("Inception")

    assert len(results) == 2
    assert results[0]["id"] == 1
    assert results[1]["title"] == "Inception 2"


async def test_search_movie_includes_year_filter(client: TMDBClient) -> None:
    client._client.get.return_value = _FakeResponse({"results": []})

    await client.search_movie("Inception", year=2010)

    params = client._client.get.call_args.kwargs["params"]
    assert params["primary_release_year"] == 2010


async def test_search_movie_omits_year_when_not_given(client: TMDBClient) -> None:
    client._client.get.return_value = _FakeResponse({"results": []})

    await client.search_movie("Inception")

    params = client._client.get.call_args.kwargs["params"]
    assert "primary_release_year" not in params


async def test_search_movie_returns_empty_on_non_list_results(client: TMDBClient) -> None:
    client._client.get.return_value = _FakeResponse({"results": "not a list"})

    results = await client.search_movie("Inception")

    assert results == []


async def test_search_movie_returns_empty_on_network_error(client: TMDBClient) -> None:
    async def _raise(*args: Any, **kwargs: Any) -> Any:
        raise httpx.ConnectError("unreachable")

    client._client.get = _raise

    results = await client.search_movie("Inception")

    assert results == []


async def test_search_movie_returns_empty_on_timeout(client: TMDBClient) -> None:
    async def _raise(*args: Any, **kwargs: Any) -> Any:
        raise httpx.TimeoutException("slow")

    client._client.get = _raise

    results = await client.search_movie("Inception")

    assert results == []


async def test_search_movie_returns_empty_on_http_error(client: TMDBClient) -> None:
    client._client.get.return_value = _FakeResponse({}, status_error=True)

    results = await client.search_movie("Inception")

    assert results == []


async def test_search_movie_returns_empty_on_malformed_json(client: TMDBClient) -> None:
    def _raise(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("no json")

    client._client.get.return_value = _FakeResponse(None)
    client._client.get.return_value.json = _raise  # type: ignore[method-assign]

    results = await client.search_movie("Inception")

    assert results == []


async def test_get_movie_details_returns_full_dict(client: TMDBClient) -> None:
    client._client.get.return_value = _FakeResponse(
        {"id": 27205, "title": "Inception", "overview": "A thief.", "vote_average": 8.4}
    )

    details = await client.get_movie_details(27205)

    assert details is not None
    assert details["title"] == "Inception"
    assert details["vote_average"] == 8.4


async def test_get_movie_details_returns_none_on_http_error(client: TMDBClient) -> None:
    client._client.get.return_value = _FakeResponse({}, status_error=True)

    details = await client.get_movie_details(27205)

    assert details is None


async def test_get_movie_details_returns_none_on_empty_payload(client: TMDBClient) -> None:
    client._client.get.return_value = _FakeResponse({})

    details = await client.get_movie_details(27205)

    assert details is None


async def test_get_json_returns_empty_on_non_dict_payload(client: TMDBClient) -> None:
    client._client.get.return_value = _FakeResponse(["not", "a", "dict"])

    details = await client.get_movie_details(27205)

    assert details is None


def test_poster_url_builds_full_url() -> None:
    assert (
        TMDBClient.poster_url("/abc123.jpg") == "https://image.tmdb.org/t/p/w500/abc123.jpg"
    )


def test_poster_url_returns_none_when_missing() -> None:
    assert TMDBClient.poster_url(None) is None
    assert TMDBClient.poster_url("") is None


async def test_get_movie_credits_returns_full_dict(client: TMDBClient) -> None:
    client._client.get.return_value = _FakeResponse(
        {"cast": [{"name": "Brad Pitt", "profile_path": "/abc.jpg"}]}
    )

    credits_ = await client.get_movie_credits(27205)

    assert credits_ is not None
    assert credits_["cast"][0]["name"] == "Brad Pitt"


async def test_get_movie_credits_returns_none_on_http_error(client: TMDBClient) -> None:
    client._client.get.return_value = _FakeResponse({}, status_error=True)

    credits_ = await client.get_movie_credits(27205)

    assert credits_ is None


async def test_get_movie_credits_returns_none_on_empty_payload(client: TMDBClient) -> None:
    client._client.get.return_value = _FakeResponse({})

    credits_ = await client.get_movie_credits(27205)

    assert credits_ is None


async def test_get_movie_images_returns_full_dict(client: TMDBClient) -> None:
    client._client.get.return_value = _FakeResponse(
        {"backdrops": [{"file_path": "/back.jpg"}]}
    )

    images = await client.get_movie_images(27205)

    assert images is not None
    assert images["backdrops"][0]["file_path"] == "/back.jpg"


async def test_get_movie_images_returns_none_on_http_error(client: TMDBClient) -> None:
    client._client.get.return_value = _FakeResponse({}, status_error=True)

    images = await client.get_movie_images(27205)

    assert images is None


async def test_get_movie_images_returns_none_on_empty_payload(client: TMDBClient) -> None:
    client._client.get.return_value = _FakeResponse({})

    images = await client.get_movie_images(27205)

    assert images is None


def test_backdrop_url_builds_full_url() -> None:
    assert (
        TMDBClient.backdrop_url("/back.jpg") == "https://image.tmdb.org/t/p/w780/back.jpg"
    )


def test_backdrop_url_returns_none_when_missing() -> None:
    assert TMDBClient.backdrop_url(None) is None
    assert TMDBClient.backdrop_url("") is None


def test_profile_url_builds_full_url() -> None:
    assert (
        TMDBClient.profile_url("/face.jpg") == "https://image.tmdb.org/t/p/w185/face.jpg"
    )


def test_profile_url_returns_none_when_missing() -> None:
    assert TMDBClient.profile_url(None) is None
    assert TMDBClient.profile_url("") is None


async def test_close_closes_http_client(client: TMDBClient) -> None:
    await client.close()
    client._client.aclose.assert_awaited_once()
