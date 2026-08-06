"""Testes de `app/services/torrent_service.py`.

`TorrentBackend` é substituído por um dublê em memória (`_FakeBackend`, sem
rede) — o objetivo é testar a orquestração (aguardar metadata, escolher o
arquivo de vídeo, aguardar buffer, montar caminho local, liberar/limpar), não
a Web API do qBittorrent (ver `test_services_qbittorrent_client.py`).
"""

from __future__ import annotations

from dataclasses import replace

import pytest

import app.services.torrent_service as torrent_service_module
from app.addon_system.base import StreamCandidate
from app.config.settings import Settings
from app.services.exceptions import TorrentResolutionError, TorrentTimeoutError
from app.services.torrent_backend import TorrentBackend, TorrentFile, TorrentStatus
from app.services.torrent_service import TorrentService
from app.utils.sanitize import MediaSource, SourceType

_MAGNET = "magnet:?xt=urn:btih:abc123&dn=Movie"


@pytest.fixture(autouse=True)
def _fast_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    """Evita sleeps reais de 2s durante os testes de timeout/polling."""
    monkeypatch.setattr(torrent_service_module, "_POLL_INTERVAL_SECONDS", 0.01)


class _FakeBackend(TorrentBackend):
    """Avança de estado a cada chamada de `status`/`files` — simula progresso ao longo do polling."""

    def __init__(self) -> None:
        self.added: list[str] = []
        self.removed: list[str] = []
        self.closed = False
        self._status_sequence: dict[str, list[TorrentStatus]] = {}
        self._files_sequence: dict[str, list[list[TorrentFile]]] = {}
        self._status_calls: dict[str, int] = {}
        self._files_calls: dict[str, int] = {}

    def program_status(self, handle: str, sequence: list[TorrentStatus]) -> None:
        self._status_sequence[handle] = sequence

    def program_files(self, handle: str, sequence: list[list[TorrentFile]]) -> None:
        self._files_sequence[handle] = sequence

    async def add(self, magnet: str) -> str:
        self.added.append(magnet)
        return "abc123"

    async def status(self, handle: str) -> TorrentStatus | None:
        sequence = self._status_sequence.get(handle, [])
        index = self._status_calls.get(handle, 0)
        self._status_calls[handle] = index + 1
        if not sequence:
            return None
        return sequence[min(index, len(sequence) - 1)]

    async def files(self, handle: str) -> list[TorrentFile]:
        sequence = self._files_sequence.get(handle, [])
        index = self._files_calls.get(handle, 0)
        self._files_calls[handle] = index + 1
        if not sequence:
            return []
        return sequence[min(index, len(sequence) - 1)]

    async def remove(self, handle: str) -> None:
        self.removed.append(handle)

    async def list_active(self) -> list[TorrentStatus]:
        return []

    async def close(self) -> None:
        self.closed = True


def _status(*, has_metadata: bool, progress: float = 0.0, save_path: str = "/downloads") -> TorrentStatus:
    return TorrentStatus(
        has_metadata=has_metadata, progress=progress, num_seeds=1, num_peers=1, save_path=save_path
    )


def _file(index: int, path: str, size_bytes: int, downloaded_bytes: int = 0) -> TorrentFile:
    return TorrentFile(index=index, path=path, size_bytes=size_bytes, downloaded_bytes=downloaded_bytes)


@pytest.fixture
def backend() -> _FakeBackend:
    return _FakeBackend()


@pytest.fixture
def torrent_settings(make_settings: object) -> Settings:
    return make_settings(  # type: ignore[operator]
        torrent_buffer_mb=1.0,
        torrent_timeout_seconds=0.05,
    )


@pytest.fixture
def service(torrent_settings: Settings, backend: _FakeBackend) -> TorrentService:
    return TorrentService(torrent_settings, backend=backend)


def _candidate(**overrides: object) -> StreamCandidate:
    base = StreamCandidate(title="Movie", url=None, info_hash="abc123")
    return replace(base, **overrides)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# resolve() - caminho feliz
# ---------------------------------------------------------------------------


async def test_resolve_full_happy_path_returns_local_path(
    service: TorrentService, backend: _FakeBackend
) -> None:
    backend.program_status("abc123", [_status(has_metadata=True, progress=1.0)])
    big_file = _file(0, "Movie.mkv", size_bytes=10 * 1024 * 1024, downloaded_bytes=10 * 1024 * 1024)
    backend.program_files("abc123", [[big_file]])

    path = await service.resolve(_candidate())

    assert path.replace("\\", "/").endswith("/downloads/Movie.mkv")
    assert backend.added[0].startswith("magnet:?xt=urn:btih:abc123")


async def test_resolve_uses_magnet_uri_built_from_info_hash(
    service: TorrentService, backend: _FakeBackend
) -> None:
    backend.program_status("abc123", [_status(has_metadata=True)])
    backend.program_files(
        "abc123", [[_file(0, "Movie.mkv", size_bytes=10 * 1024 * 1024, downloaded_bytes=10 * 1024 * 1024)]]
    )

    await service.resolve(_candidate(info_hash="abc123", title="Movie"))

    assert backend.added[0].startswith("magnet:?xt=urn:btih:abc123")


async def test_resolve_uses_explicit_magnet_when_present(
    service: TorrentService, backend: _FakeBackend
) -> None:
    backend.program_status("abc123", [_status(has_metadata=True)])
    backend.program_files(
        "abc123", [[_file(0, "Movie.mkv", size_bytes=10 * 1024 * 1024, downloaded_bytes=10 * 1024 * 1024)]]
    )

    await service.resolve(_candidate(magnet=_MAGNET, info_hash=None))

    assert backend.added == [_MAGNET]


async def test_resolve_without_magnet_or_info_hash_raises(service: TorrentService) -> None:
    with pytest.raises(TorrentResolutionError):
        await service.resolve(_candidate(info_hash=None, magnet=None))


# ---------------------------------------------------------------------------
# resolve() - timeouts
# ---------------------------------------------------------------------------


async def test_resolve_metadata_timeout_raises(service: TorrentService, backend: _FakeBackend) -> None:
    backend.program_status("abc123", [_status(has_metadata=False)])

    with pytest.raises(TorrentTimeoutError):
        await service.resolve(_candidate())


async def test_resolve_buffer_timeout_raises(service: TorrentService, backend: _FakeBackend) -> None:
    backend.program_status("abc123", [_status(has_metadata=True, progress=0.01)])
    small_progress_file = _file(0, "Movie.mkv", size_bytes=10 * 1024 * 1024, downloaded_bytes=1024)
    backend.program_files("abc123", [[small_progress_file]])

    with pytest.raises(TorrentTimeoutError):
        await service.resolve(_candidate())


# ---------------------------------------------------------------------------
# resolve() - seleção de arquivo
# ---------------------------------------------------------------------------


async def test_resolve_ignores_sample_and_small_files(
    service: TorrentService, backend: _FakeBackend
) -> None:
    backend.program_status("abc123", [_status(has_metadata=True)])
    sample = _file(0, "Movie.sample.mkv", size_bytes=10 * 1024 * 1024, downloaded_bytes=10 * 1024 * 1024)
    tiny = _file(1, "readme.mkv", size_bytes=1024, downloaded_bytes=1024)
    real = _file(2, "Movie.mkv", size_bytes=10 * 1024 * 1024, downloaded_bytes=10 * 1024 * 1024)
    backend.program_files("abc123", [[sample, tiny, real]])

    path = await service.resolve(_candidate())

    assert path.replace("\\", "/").endswith("/downloads/Movie.mkv")


async def test_resolve_picks_largest_video_file(service: TorrentService, backend: _FakeBackend) -> None:
    backend.program_status("abc123", [_status(has_metadata=True)])
    small = _file(0, "small.mkv", size_bytes=10 * 1024 * 1024, downloaded_bytes=10 * 1024 * 1024)
    large = _file(1, "large.mkv", size_bytes=20 * 1024 * 1024, downloaded_bytes=20 * 1024 * 1024)
    backend.program_files("abc123", [[small, large]])

    path = await service.resolve(_candidate())

    assert path.replace("\\", "/").endswith("/downloads/large.mkv")


async def test_resolve_uses_file_index_hint(service: TorrentService, backend: _FakeBackend) -> None:
    backend.program_status("abc123", [_status(has_metadata=True)])
    small = _file(0, "small.mkv", size_bytes=10 * 1024 * 1024, downloaded_bytes=10 * 1024 * 1024)
    hinted = _file(1, "hinted.mkv", size_bytes=1 * 1024 * 1024, downloaded_bytes=1 * 1024 * 1024)
    backend.program_files("abc123", [[small, hinted]])

    path = await service.resolve(_candidate(file_index=1))

    assert path.replace("\\", "/").endswith("/downloads/hinted.mkv")


async def test_resolve_no_video_file_raises(service: TorrentService, backend: _FakeBackend) -> None:
    backend.program_status("abc123", [_status(has_metadata=True)])
    backend.program_files(
        "abc123", [[_file(0, "readme.txt", size_bytes=2 * 1024 * 1024, downloaded_bytes=2 * 1024 * 1024)]]
    )

    with pytest.raises(TorrentResolutionError):
        await service.resolve(_candidate())


# ---------------------------------------------------------------------------
# release()
# ---------------------------------------------------------------------------


async def test_release_removes_torrent_when_setting_enabled(
    torrent_settings: Settings, backend: _FakeBackend
) -> None:
    settings = torrent_settings.model_copy(update={"remove_torrent_after_play": True})
    service = TorrentService(settings, backend=backend)
    backend.program_status("abc123", [_status(has_metadata=True)])
    backend.program_files(
        "abc123", [[_file(0, "Movie.mkv", size_bytes=10 * 1024 * 1024, downloaded_bytes=10 * 1024 * 1024)]]
    )
    path = await service.resolve(_candidate())

    await service.release(MediaSource(raw=path, type=SourceType.LOCAL_FILE))

    assert backend.removed == ["abc123"]


async def test_release_keeps_seeding_when_setting_disabled(
    service: TorrentService, backend: _FakeBackend
) -> None:
    backend.program_status("abc123", [_status(has_metadata=True)])
    backend.program_files(
        "abc123", [[_file(0, "Movie.mkv", size_bytes=10 * 1024 * 1024, downloaded_bytes=10 * 1024 * 1024)]]
    )
    path = await service.resolve(_candidate())

    await service.release(MediaSource(raw=path, type=SourceType.LOCAL_FILE))

    assert backend.removed == []


async def test_release_ignores_untracked_source(service: TorrentService, backend: _FakeBackend) -> None:
    await service.release(MediaSource(raw="/media/other.mp4", type=SourceType.LOCAL_FILE))

    assert backend.removed == []


# ---------------------------------------------------------------------------
# close()
# ---------------------------------------------------------------------------


async def test_close_closes_backend(service: TorrentService, backend: _FakeBackend) -> None:
    await service.close()

    assert backend.closed is True
