"""Contrato HTTP do cliente oficial OpenSubtitles."""

import httpx
import pytest

from app.services.opensubtitles_client import (
    OpenSubtitlesClient,
    OpenSubtitlesError,
    SubtitleOption,
)


async def test_search_logs_in_and_returns_portuguese_files() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/login"):
            assert request.headers["Api-Key"] == "api-key"
            assert request.headers["User-Agent"] == "Telerion tests"
            assert request.content == b'{"username":"user","password":"secret"}'
            return httpx.Response(200, json={"token": "jwt"})
        assert request.url.path.endswith("/subtitles")
        assert request.headers["Authorization"] == "Bearer jwt"
        assert request.url.params["imdb_id"] == "0133093"
        assert request.url.params["languages"] == "pt-BR,pt"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "attributes": {
                            "language": "pt-BR",
                            "release": "The.Matrix.1999",
                            "download_count": 20,
                            "files": [{"file_id": 7}],
                        }
                    },
                    {
                        "attributes": {
                            "language": "pt",
                            "release": "The.Matrix.WEB",
                            "download_count": 10,
                            "files": [{"file_id": 8}],
                        }
                    },
                ]
            },
        )

    http = httpx.AsyncClient(
        base_url="https://api.opensubtitles.com/api/v1",
        transport=httpx.MockTransport(handler),
    )
    client = OpenSubtitlesClient("api-key", "user", "secret", "Telerion tests", client=http)

    options = await client.search("tt0133093")

    assert options == [
        SubtitleOption(7, "pt-BR", "The.Matrix.1999", 20),
        SubtitleOption(8, "pt", "The.Matrix.WEB", 10),
    ]
    assert len(requests) == 2
    await client.close()


async def test_download_uses_file_id_and_rejects_oversized_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/login"):
            return httpx.Response(200, json={"token": "jwt"})
        if request.url.path.endswith("/download"):
            assert request.content == b'{"file_id":123}'
            return httpx.Response(200, json={"link": "https://files.example/subtitle.srt"})
        return httpx.Response(200, content=b"x" * (2 * 1024 * 1024 + 1))

    http = httpx.AsyncClient(
        base_url="https://api.opensubtitles.com/api/v1",
        transport=httpx.MockTransport(handler),
    )
    client = OpenSubtitlesClient("key", "user", "secret", "agent", client=http)

    with pytest.raises(OpenSubtitlesError, match="Legenda excede 2 MB"):
        await client.download(123)

    await client.close()
