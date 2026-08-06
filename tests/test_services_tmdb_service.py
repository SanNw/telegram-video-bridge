"""Testes de `app/services/tmdb_service.py`."""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import AsyncMock

import pytest

from app.config.settings import Settings
from app.services.tmdb_service import TMDBCastMember, TMDBService


@pytest.fixture
def disabled_service(make_settings: Callable[..., Settings]) -> TMDBService:
    return TMDBService(make_settings())


@pytest.fixture
def enabled_service(make_settings: Callable[..., Settings]) -> TMDBService:
    service = TMDBService(make_settings(tmdb_api_key="test-api-key"))
    service._client = AsyncMock()  # type: ignore[assignment]
    service._client.get_movie_credits.return_value = None  # type: ignore[attr-defined]
    service._client.get_movie_images.return_value = None  # type: ignore[attr-defined]
    return service


async def test_disabled_when_no_api_key(disabled_service: TMDBService) -> None:
    assert disabled_service.enabled is False


async def test_enrich_returns_none_when_disabled(disabled_service: TMDBService) -> None:
    result = await disabled_service.enrich("Inception", 2010)
    assert result is None


async def test_close_is_noop_when_disabled(disabled_service: TMDBService) -> None:
    await disabled_service.close()


async def test_enabled_when_api_key_set(enabled_service: TMDBService) -> None:
    assert enabled_service.enabled is True


async def test_enrich_returns_none_when_search_finds_nothing(
    enabled_service: TMDBService,
) -> None:
    enabled_service._client.search_movie.return_value = []  # type: ignore[attr-defined]

    result = await enabled_service.enrich("Nonexistent Movie", None)

    assert result is None


async def test_enrich_returns_none_when_result_has_no_int_id(
    enabled_service: TMDBService,
) -> None:
    enabled_service._client.search_movie.return_value = [{"title": "Inception"}]  # type: ignore[attr-defined]

    result = await enabled_service.enrich("Inception", None)

    assert result is None


async def test_enrich_returns_none_when_details_missing(enabled_service: TMDBService) -> None:
    enabled_service._client.search_movie.return_value = [{"id": 27205}]  # type: ignore[attr-defined]
    enabled_service._client.get_movie_details.return_value = None  # type: ignore[attr-defined]

    result = await enabled_service.enrich("Inception", None)

    assert result is None


async def test_enrich_builds_metadata_from_details(enabled_service: TMDBService) -> None:
    enabled_service._client.search_movie.return_value = [{"id": 27205}]  # type: ignore[attr-defined]
    enabled_service._client.get_movie_details.return_value = {  # type: ignore[attr-defined]
        "title": "Inception",
        "overview": "A thief who steals corporate secrets.",
        "poster_path": "/abc123.jpg",
        "vote_average": 8.4,
        "genres": [{"id": 28, "name": "Action"}, {"id": 878, "name": "Science Fiction"}],
        "release_date": "2010-07-15",
    }

    metadata = await enabled_service.enrich("Inception", 2010)

    assert metadata is not None
    assert metadata.title == "Inception"
    assert metadata.overview == "A thief who steals corporate secrets."
    assert metadata.poster_url == "https://image.tmdb.org/t/p/w500/abc123.jpg"
    assert metadata.vote_average == 8.4
    assert metadata.genres == ["Action", "Science Fiction"]
    assert metadata.release_date == "2010-07-15"
    assert metadata.cast == []
    assert metadata.backdrop_urls == []


async def test_enrich_falls_back_to_query_title_when_missing(
    enabled_service: TMDBService,
) -> None:
    enabled_service._client.search_movie.return_value = [{"id": 27205}]  # type: ignore[attr-defined]
    enabled_service._client.get_movie_details.return_value = {}  # type: ignore[attr-defined]

    metadata = await enabled_service.enrich("Inception", None)

    assert metadata is not None
    assert metadata.title == "Inception"
    assert metadata.overview is None
    assert metadata.poster_url is None
    assert metadata.vote_average is None
    assert metadata.genres == []
    assert metadata.release_date is None


async def test_enrich_ignores_genres_without_name_field(enabled_service: TMDBService) -> None:
    enabled_service._client.search_movie.return_value = [{"id": 27205}]  # type: ignore[attr-defined]
    enabled_service._client.get_movie_details.return_value = {  # type: ignore[attr-defined]
        "title": "Inception",
        "genres": [{"id": 28}, "not-a-dict", {"id": 878, "name": "Science Fiction"}],
    }

    metadata = await enabled_service.enrich("Inception", None)

    assert metadata is not None
    assert metadata.genres == ["Science Fiction"]


async def test_close_closes_underlying_client(enabled_service: TMDBService) -> None:
    await enabled_service.close()
    enabled_service._client.close.assert_awaited_once()  # type: ignore[attr-defined]


async def test_enrich_populates_cast_with_profile_urls(enabled_service: TMDBService) -> None:
    enabled_service._client.search_movie.return_value = [{"id": 27205}]  # type: ignore[attr-defined]
    enabled_service._client.get_movie_details.return_value = {"title": "Inception"}  # type: ignore[attr-defined]
    enabled_service._client.get_movie_credits.return_value = {  # type: ignore[attr-defined]
        "cast": [
            {"name": "Leonardo DiCaprio", "profile_path": "/leo.jpg"},
            {"name": "Joseph Gordon-Levitt", "profile_path": "/joseph.jpg"},
        ]
    }

    metadata = await enabled_service.enrich("Inception", None)

    assert metadata is not None
    assert metadata.cast[0].name == "Leonardo DiCaprio"
    assert metadata.cast[0].profile_url == "https://image.tmdb.org/t/p/w185/leo.jpg"
    assert metadata.cast[1].name == "Joseph Gordon-Levitt"


async def test_enrich_caps_cast_at_six_members(enabled_service: TMDBService) -> None:
    enabled_service._client.search_movie.return_value = [{"id": 27205}]  # type: ignore[attr-defined]
    enabled_service._client.get_movie_details.return_value = {"title": "Inception"}  # type: ignore[attr-defined]
    enabled_service._client.get_movie_credits.return_value = {  # type: ignore[attr-defined]
        "cast": [{"name": f"Actor {i}", "profile_path": None} for i in range(10)]
    }

    metadata = await enabled_service.enrich("Inception", None)

    assert metadata is not None
    assert len(metadata.cast) == 6


async def test_enrich_cast_member_without_profile_path_has_none_url(
    enabled_service: TMDBService,
) -> None:
    enabled_service._client.search_movie.return_value = [{"id": 27205}]  # type: ignore[attr-defined]
    enabled_service._client.get_movie_details.return_value = {"title": "Inception"}  # type: ignore[attr-defined]
    enabled_service._client.get_movie_credits.return_value = {  # type: ignore[attr-defined]
        "cast": [{"name": "Extra sem foto"}]
    }

    metadata = await enabled_service.enrich("Inception", None)

    assert metadata is not None
    assert metadata.cast == [TMDBCastMember(name="Extra sem foto", profile_url=None)]


async def test_enrich_ignores_cast_entries_without_name(enabled_service: TMDBService) -> None:
    enabled_service._client.search_movie.return_value = [{"id": 27205}]  # type: ignore[attr-defined]
    enabled_service._client.get_movie_details.return_value = {"title": "Inception"}  # type: ignore[attr-defined]
    enabled_service._client.get_movie_credits.return_value = {  # type: ignore[attr-defined]
        "cast": [{"profile_path": "/no-name.jpg"}, "not-a-dict"]
    }

    metadata = await enabled_service.enrich("Inception", None)

    assert metadata is not None
    assert metadata.cast == []


async def test_enrich_returns_empty_cast_when_credits_fetch_fails(
    enabled_service: TMDBService,
) -> None:
    enabled_service._client.search_movie.return_value = [{"id": 27205}]  # type: ignore[attr-defined]
    enabled_service._client.get_movie_details.return_value = {"title": "Inception"}  # type: ignore[attr-defined]
    enabled_service._client.get_movie_credits.return_value = None  # type: ignore[attr-defined]

    metadata = await enabled_service.enrich("Inception", None)

    assert metadata is not None
    assert metadata.cast == []


async def test_enrich_populates_backdrop_urls(enabled_service: TMDBService) -> None:
    enabled_service._client.search_movie.return_value = [{"id": 27205}]  # type: ignore[attr-defined]
    enabled_service._client.get_movie_details.return_value = {"title": "Inception"}  # type: ignore[attr-defined]
    enabled_service._client.get_movie_images.return_value = {  # type: ignore[attr-defined]
        "backdrops": [{"file_path": "/back1.jpg"}, {"file_path": "/back2.jpg"}]
    }

    metadata = await enabled_service.enrich("Inception", None)

    assert metadata is not None
    assert metadata.backdrop_urls == [
        "https://image.tmdb.org/t/p/w780/back1.jpg",
        "https://image.tmdb.org/t/p/w780/back2.jpg",
    ]


async def test_enrich_caps_backdrops_at_four(enabled_service: TMDBService) -> None:
    enabled_service._client.search_movie.return_value = [{"id": 27205}]  # type: ignore[attr-defined]
    enabled_service._client.get_movie_details.return_value = {"title": "Inception"}  # type: ignore[attr-defined]
    enabled_service._client.get_movie_images.return_value = {  # type: ignore[attr-defined]
        "backdrops": [{"file_path": f"/back{i}.jpg"} for i in range(10)]
    }

    metadata = await enabled_service.enrich("Inception", None)

    assert metadata is not None
    assert len(metadata.backdrop_urls) == 4


async def test_enrich_returns_empty_backdrops_when_images_fetch_fails(
    enabled_service: TMDBService,
) -> None:
    enabled_service._client.search_movie.return_value = [{"id": 27205}]  # type: ignore[attr-defined]
    enabled_service._client.get_movie_details.return_value = {"title": "Inception"}  # type: ignore[attr-defined]
    enabled_service._client.get_movie_images.return_value = None  # type: ignore[attr-defined]

    metadata = await enabled_service.enrich("Inception", None)

    assert metadata is not None
    assert metadata.backdrop_urls == []
