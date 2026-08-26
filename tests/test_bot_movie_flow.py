"""Fluxo efêmero e individual de seleção de filmes."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from app.addon_system.base import SearchResult, StreamCandidate
from app.bot.auth import build_authorized_filter
from app.bot.handlers import addons
from app.services.tmdb_service import TMDBMetadata, TMDBMovie
from tests.test_bot_handlers import FakeCallbackQuery, FakeClient, FakeMessage, dispatch_callback


class _RichAddonService:
    def __init__(self) -> None:
        self.movies = [self._movie(0), self._movie(1)]
        self.selected: list[int] = []
        self.played: list[tuple[str, int]] = []
        self.current_index = 0

    @staticmethod
    def _movie(index: int) -> TMDBMovie:
        return TMDBMovie(
            id=index,
            title=f"Movie {index}",
            original_title=None,
            overview=f"Overview {index}",
            poster_url=None,
            vote_average=8.0,
            release_date="2020-01-01",
        )

    def catalog(self) -> list[TMDBMovie]:
        return self.movies

    async def select_catalog_movie(
        self, index: int
    ) -> tuple[TMDBMovie, TMDBMetadata, list[SearchResult]]:
        self.current_index = index
        self.selected.append(index)
        movie = self.movies[index]
        metadata = TMDBMetadata(
            title=movie.title,
            original_title=None,
            overview=movie.overview,
            poster_url=None,
            vote_average=movie.vote_average,
            genres=["Drama"],
            release_date=movie.release_date,
            cast=[],
            backdrop_urls=[],
        )
        return movie, metadata, []

    async def resolve_candidates(self) -> list[tuple[str, SearchResult, StreamCandidate]]:
        index = self.current_index
        return [
            (
                "0",
                SearchResult(str(index), f"Movie {index}", 2020, "stremio"),
                StreamCandidate(title=f"Movie {index} 2020", quality="1080p", seeds=100 + index),
            )
        ]

    async def play_resolved_candidate(
        self, result: SearchResult, candidate: StreamCandidate, requested_by: int
    ) -> tuple[str, int]:
        self.played.append((result.media_id, requested_by))
        return result.addon_name, 1


class _FakeBotAPI:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.edited: list[dict[str, Any]] = []

    async def send_rich_message(
        self, chat_id: int, rich_message: dict[str, object], **kwargs: object
    ) -> dict[str, object]:
        self.sent.append({"chat_id": chat_id, "rich_message": rich_message, **kwargs})
        return {"ephemeral_message_id": 77 + len(self.sent)}

    async def edit_ephemeral_message(
        self,
        chat_id: int,
        receiver_user_id: int,
        ephemeral_message_id: int,
        rich_message: dict[str, object],
    ) -> None:
        self.edited.append(
            {
                "chat_id": chat_id,
                "receiver_user_id": receiver_user_id,
                "ephemeral_message_id": ephemeral_message_id,
                "rich_message": rich_message,
            }
        )


def _callback(data: str, user_id: int) -> FakeCallbackQuery:
    callback = FakeCallbackQuery(data, user_id)
    callback.id = f"cb-{user_id}"
    callback.message = FakeMessage("", user_id)
    callback.message.chat = SimpleNamespace(id=-1001)
    return callback


async def test_movie_callback_replaces_message_with_user_specific_details(
    make_settings: Any,
) -> None:
    client = FakeClient()
    service = _RichAddonService()
    bot_api = _FakeBotAPI()
    authorized = build_authorized_filter(make_settings(authorized_user_ids=[111]))
    addons.register_search(client, service, authorized, True, bot_api=bot_api)  # type: ignore[arg-type]

    assert await dispatch_callback(client, _callback("movie:0", 111))

    sent = bot_api.sent[-1]
    assert sent["receiver_user_id"] == 111
    assert sent["callback_query_id"] == "cb-111"
    assert sent["replace_callback_query_message"] is True
    assert "Movie 0" in json.dumps(sent["rich_message"])


async def test_two_users_keep_independent_candidates(make_settings: Any) -> None:
    client = FakeClient()
    service = _RichAddonService()
    bot_api = _FakeBotAPI()
    authorized = build_authorized_filter(make_settings(authorized_user_ids=[111, 222]))
    addons.register_search(client, service, authorized, True, bot_api=bot_api)  # type: ignore[arg-type]

    await dispatch_callback(client, _callback("movie:0", 111))
    await dispatch_callback(client, _callback("movie:1", 222))
    await dispatch_callback(client, _callback("source:0", 111))
    await dispatch_callback(client, _callback("source:0", 222))

    assert service.played == [("0", 111), ("1", 222)]
    assert [edit["receiver_user_id"] for edit in bot_api.edited] == [111, 111, 222, 222]


async def test_source_callback_edits_progress_then_success(make_settings: Any) -> None:
    client = FakeClient()
    service = _RichAddonService()
    bot_api = _FakeBotAPI()
    authorized = build_authorized_filter(make_settings(authorized_user_ids=[111]))
    addons.register_search(client, service, authorized, True, bot_api=bot_api)  # type: ignore[arg-type]
    await dispatch_callback(client, _callback("movie:0", 111))

    await dispatch_callback(client, _callback("source:0", 111))

    assert len(bot_api.edited) == 2
    assert "Preparando" in json.dumps(bot_api.edited[0]["rich_message"], ensure_ascii=False)
    assert "fila" in json.dumps(bot_api.edited[1]["rich_message"], ensure_ascii=False)
