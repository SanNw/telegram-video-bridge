"""Testes de `app/services/qbittorrent_client.py`.

`httpx.AsyncClient` interno é substituído por um `AsyncMock` — nenhum destes
testes bate na rede real. Mesmo padrão de `tests/test_addon_stremio.py` e
`tests/test_services_tmdb_client.py`.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.config.settings import Settings
from app.services.qbittorrent_client import (
    QBittorrentAuthError,
    QBittorrentClient,
    QBittorrentUnavailableError,
)

_MAGNET = "magnet:?xt=urn:btih:ABC123ABC123ABC123ABC123ABC123ABC123ABC1&dn=Movie"


class _FakeResponse:
    def __init__(
        self,
        json_data: Any = None,
        *,
        status_code: int = 200,
        text: str = "",
        status_error: bool = False,
    ) -> None:
        self._json_data = json_data
        self.status_code = status_code
        self.text = text
        self._status_error = status_error

    def raise_for_status(self) -> None:
        if self._status_error:
            raise httpx.HTTPStatusError(
                "error", request=MagicMock(), response=MagicMock(status_code=self.status_code)
            )

    def json(self) -> Any:
        if self._json_data is None:
            raise ValueError("no json")
        return self._json_data


def _make_client(settings: Settings) -> QBittorrentClient:
    client = QBittorrentClient(settings)
    client._client = AsyncMock()
    return client


@pytest.fixture
def client(settings: Settings) -> QBittorrentClient:
    return _make_client(settings)


# ---------------------------------------------------------------------------
# login / autenticação
# ---------------------------------------------------------------------------


async def test_add_logs_in_before_first_request(client: QBittorrentClient) -> None:
    client._client.post.return_value = _FakeResponse(status_code=200, text="Ok.")
    client._client.request.return_value = _FakeResponse(status_code=200)

    handle = await client.add(_MAGNET)

    assert handle == "abc123abc123abc123abc123abc123abc123abc1"
    client._client.post.assert_awaited_once()
    assert client._authenticated is True


async def test_login_failure_raises_auth_error(client: QBittorrentClient) -> None:
    client._client.post.return_value = _FakeResponse(status_code=200, text="Fails.")

    with pytest.raises(QBittorrentAuthError):
        await client.add(_MAGNET)


async def test_login_network_error_raises_unavailable(client: QBittorrentClient) -> None:
    client._client.post.side_effect = httpx.ConnectError("unreachable")

    with pytest.raises(QBittorrentUnavailableError):
        await client.add(_MAGNET)


async def test_request_reauthenticates_on_403(client: QBittorrentClient) -> None:
    client._client.post.return_value = _FakeResponse(status_code=200, text="Ok.")
    client._client.request.side_effect = [
        _FakeResponse(status_code=403),
        _FakeResponse(status_code=200),
    ]

    await client.add(_MAGNET)

    assert client._client.post.await_count == 2
    assert client._client.request.await_count == 2


async def test_request_http_status_error_raises_unavailable(client: QBittorrentClient) -> None:
    client._client.post.return_value = _FakeResponse(status_code=200, text="Ok.")
    client._client.request.return_value = _FakeResponse(status_code=500, status_error=True)

    with pytest.raises(QBittorrentUnavailableError):
        await client.add(_MAGNET)


async def test_send_network_error_raises_unavailable(client: QBittorrentClient) -> None:
    client._client.post.return_value = _FakeResponse(status_code=200, text="Ok.")
    client._client.request.side_effect = httpx.ConnectError("unreachable")

    with pytest.raises(QBittorrentUnavailableError):
        await client.add(_MAGNET)


# ---------------------------------------------------------------------------
# add()
# ---------------------------------------------------------------------------


async def test_add_sends_sequential_download_and_savepath(
    client: QBittorrentClient, settings: Settings
) -> None:
    client._client.post.return_value = _FakeResponse(status_code=200, text="Ok.")
    client._client.request.return_value = _FakeResponse(status_code=200)

    await client.add(_MAGNET)

    _, kwargs = client._client.request.call_args
    data = kwargs["data"]
    assert data["urls"] == _MAGNET
    assert data["sequentialDownload"] == "true"
    assert data["firstLastPiecePrio"] == "true"
    assert data["savepath"] == str(settings.qbittorrent_save_path)
    assert "category" not in data


async def test_add_includes_category_when_configured(make_settings: Any) -> None:
    settings = make_settings(qbittorrent_category="filmes")
    client = _make_client(settings)
    client._client.post.return_value = _FakeResponse(status_code=200, text="Ok.")
    client._client.request.return_value = _FakeResponse(status_code=200)

    await client.add(_MAGNET)

    _, kwargs = client._client.request.call_args
    assert kwargs["data"]["category"] == "filmes"


async def test_add_extracts_info_hash_from_bare_magnet(client: QBittorrentClient) -> None:
    client._client.post.return_value = _FakeResponse(status_code=200, text="Ok.")
    client._client.request.return_value = _FakeResponse(status_code=200)

    handle = await client.add("magnet:?xt=urn:btih:deadbeefdeadbeefdeadbeefdeadbeefdeadbeef")

    assert handle == "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"


async def test_add_magnet_without_info_hash_raises_unavailable(
    client: QBittorrentClient,
) -> None:
    client._client.post.return_value = _FakeResponse(status_code=200, text="Ok.")

    with pytest.raises(QBittorrentUnavailableError):
        await client.add("magnet:?dn=no-hash-here")


# ---------------------------------------------------------------------------
# status() / files() / remove() / list_active()
# ---------------------------------------------------------------------------


async def test_status_returns_none_when_torrent_not_found(client: QBittorrentClient) -> None:
    client._client.post.return_value = _FakeResponse(status_code=200, text="Ok.")
    client._client.request.return_value = _FakeResponse([])

    status = await client.status("abc123")

    assert status is None


async def test_status_maps_fields(client: QBittorrentClient) -> None:
    client._client.post.return_value = _FakeResponse(status_code=200, text="Ok.")
    client._client.request.return_value = _FakeResponse(
        [
            {
                "state": "downloading",
                "progress": 0.42,
                "num_seeds": 12,
                "num_leechs": 3,
                "save_path": "/downloads",
            }
        ]
    )

    status = await client.status("abc123")

    assert status is not None
    assert status.has_metadata is True
    assert status.progress == 0.42
    assert status.num_seeds == 12
    assert status.num_peers == 3
    assert status.save_path == "/downloads"


async def test_status_no_metadata_state_reports_false(client: QBittorrentClient) -> None:
    client._client.post.return_value = _FakeResponse(status_code=200, text="Ok.")
    client._client.request.return_value = _FakeResponse(
        [{"state": "metaDL", "progress": 0.0, "num_seeds": 0, "num_leechs": 0, "save_path": ""}]
    )

    status = await client.status("abc123")

    assert status is not None
    assert status.has_metadata is False


async def test_files_maps_downloaded_bytes_from_progress(client: QBittorrentClient) -> None:
    client._client.post.return_value = _FakeResponse(status_code=200, text="Ok.")
    client._client.request.return_value = _FakeResponse(
        [{"index": 0, "name": "movie.mkv", "size": 1000, "progress": 0.5}]
    )

    files = await client.files("abc123")

    assert len(files) == 1
    assert files[0].index == 0
    assert files[0].path == "movie.mkv"
    assert files[0].size_bytes == 1000
    assert files[0].downloaded_bytes == 500


async def test_files_returns_empty_on_invalid_json(client: QBittorrentClient) -> None:
    client._client.post.return_value = _FakeResponse(status_code=200, text="Ok.")
    client._client.request.return_value = _FakeResponse(None)

    files = await client.files("abc123")

    assert files == []


async def test_select_file_disables_pack_and_prioritizes_only_target(
    client: QBittorrentClient,
) -> None:
    client._client.post.return_value = _FakeResponse(status_code=200, text="Ok.")
    client._client.request.side_effect = [
        _FakeResponse(
            [
                {"index": 0, "name": "a.mkv", "size": 100, "progress": 0.0},
                {"index": 6, "name": "matrix.mkv", "size": 200, "progress": 0.0},
                {"index": 9, "name": "b.mkv", "size": 100, "progress": 0.0},
            ]
        ),
        _FakeResponse(status_code=200),
        _FakeResponse(status_code=200),
    ]

    await client.select_file("abc123", 6)

    calls = client._client.request.call_args_list
    assert calls[1].args == ("POST", "/torrents/filePrio")
    assert calls[1].kwargs["data"] == {"hash": "abc123", "id": "0|9", "priority": "0"}
    assert calls[2].kwargs["data"] == {"hash": "abc123", "id": "6", "priority": "7"}


async def test_remove_sends_delete_with_files(client: QBittorrentClient) -> None:
    client._client.post.return_value = _FakeResponse(status_code=200, text="Ok.")
    client._client.request.return_value = _FakeResponse(status_code=200)

    await client.remove("abc123")

    args, kwargs = client._client.request.call_args
    assert args[0] == "POST"
    assert args[1] == "/torrents/delete"
    assert kwargs["data"] == {"hashes": "abc123", "deleteFiles": "true"}


async def test_list_active_maps_all_entries(client: QBittorrentClient) -> None:
    client._client.post.return_value = _FakeResponse(status_code=200, text="Ok.")
    client._client.request.return_value = _FakeResponse(
        [
            {
                "state": "downloading",
                "progress": 0.1,
                "num_seeds": 1,
                "num_leechs": 1,
                "save_path": "/downloads",
            },
            {
                "state": "stalledDL",
                "progress": 0.9,
                "num_seeds": 5,
                "num_leechs": 2,
                "save_path": "/downloads",
            },
        ]
    )

    statuses = await client.list_active()

    assert len(statuses) == 2
    assert statuses[0].progress == 0.1
    assert statuses[1].progress == 0.9


# ---------------------------------------------------------------------------
# close()
# ---------------------------------------------------------------------------


async def test_close_closes_httpx_client(client: QBittorrentClient) -> None:
    await client.close()

    client._client.aclose.assert_awaited_once()
