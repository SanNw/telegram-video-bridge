"""Cliente para a Web API v2 do qBittorrent — implementação concreta de `TorrentBackend`.

Diferente de `TMDBClient`/`StremioAddonClient` (que nunca lançam exceção
porque alimentam um enriquecimento opcional), este cliente está no caminho
crítico de reprodução: falhas de autenticação, indisponibilidade da API ou
torrent inválido precisam ser distinguíveis pelo chamador (`TorrentService`),
que decide se tenta de novo, propaga, ou passa para o próximo candidato.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from app.config.settings import Settings
from app.services.torrent_backend import TorrentBackend, TorrentFile, TorrentStatus
from app.utils.logging import get_logger

_logger = get_logger("services")

_INFO_HASH_RE = re.compile(r"btih:([a-fA-F0-9]{40}|[a-zA-Z2-7]{32})")

# Estados da Web API do qBittorrent em que a metadata do torrent (lista de
# arquivos, tamanho total) ainda não está disponível — só magnet resolvido.
_NO_METADATA_STATES = frozenset({"metaDL", "checkingResumeData", "allocating", "missingFiles"})


class QBittorrentAuthError(Exception):
    """Login na Web API do qBittorrent falhou (usuário/senha inválidos)."""


class QBittorrentUnavailableError(Exception):
    """A Web API do qBittorrent não respondeu ou respondeu com erro de servidor."""


class QBittorrentClient(TorrentBackend):
    """Cliente assíncrono para a Web API v2 do qBittorrent, autenticado por cookie de sessão.

    Reautentica automaticamente (uma única retentativa) quando uma chamada
    volta 403 — a sessão expira por inatividade prolongada.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        base_url = f"http://{settings.qbittorrent_host}:{settings.qbittorrent_port}/api/v2"
        self._client = httpx.AsyncClient(base_url=base_url, timeout=15.0)
        self._authenticated = False

    async def close(self) -> None:
        await self._client.aclose()

    async def add(self, magnet: str) -> str:
        """Adiciona um torrent via magnet URI. Devolve o infoHash extraído do magnet.

        A Web API não devolve o hash na resposta de `torrents/add` (só HTTP
        200 sem corpo útil) — por isso o handle usado em todo o resto desta
        classe é sempre o infoHash extraído do próprio magnet, nunca algo
        devolvido pelo servidor.
        """
        handle = _extract_info_hash(magnet)
        data: dict[str, Any] = {
            "urls": magnet,
            "sequentialDownload": "true",
            "firstLastPiecePrio": "true",
            "savepath": str(self._settings.qbittorrent_save_path),
        }
        if self._settings.qbittorrent_category:
            data["category"] = self._settings.qbittorrent_category
        await self._request("POST", "/torrents/add", data=data)
        _logger.info("Torrent adicionado: {hash}", hash=handle)
        return handle

    async def status(self, handle: str) -> TorrentStatus | None:
        response = await self._request("GET", "/torrents/info", params={"hashes": handle})
        entries = response.json()
        if not entries:
            return None
        return _to_torrent_status(entries[0])

    async def files(self, handle: str) -> list[TorrentFile]:
        response = await self._request("GET", "/torrents/files", params={"hash": handle})
        try:
            entries = response.json()
        except ValueError:
            return []
        return [
            TorrentFile(
                index=entry["index"],
                path=entry["name"],
                size_bytes=entry["size"],
                downloaded_bytes=int(entry["size"] * entry["progress"]),
            )
            for entry in entries
        ]

    async def select_file(self, handle: str, file_index: int) -> None:
        files = await self.files(handle)
        other_ids = "|".join(str(file.index) for file in files if file.index != file_index)
        if other_ids:
            await self._request(
                "POST",
                "/torrents/filePrio",
                data={"hash": handle, "id": other_ids, "priority": "0"},
            )
        await self._request(
            "POST",
            "/torrents/filePrio",
            data={"hash": handle, "id": str(file_index), "priority": "7"},
        )

    async def remove(self, handle: str) -> None:
        await self._request(
            "POST", "/torrents/delete", data={"hashes": handle, "deleteFiles": "true"}
        )
        _logger.info("Torrent removido: {hash}", hash=handle)

    async def list_active(self) -> list[TorrentStatus]:
        response = await self._request("GET", "/torrents/info")
        return [_to_torrent_status(entry) for entry in response.json()]

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> httpx.Response:
        if not self._authenticated:
            await self._login()
        response = await self._send(method, path, params=params, data=data)
        if response.status_code == 403:
            # Sessão expirada — reautentica e tenta uma única vez de novo.
            await self._login()
            response = await self._send(method, path, params=params, data=data)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise QBittorrentUnavailableError(
                f"qBittorrent respondeu {exc.response.status_code} em {path}"
            ) from exc
        return response

    async def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None,
        data: dict[str, Any] | None,
    ) -> httpx.Response:
        try:
            return await self._client.request(method, path, params=params, data=data)
        except httpx.HTTPError as exc:
            raise QBittorrentUnavailableError(
                f"Falha de rede ao acessar a Web API do qBittorrent: {exc}"
            ) from exc

    async def _login(self) -> None:
        try:
            response = await self._client.post(
                "/auth/login",
                data={
                    "username": self._settings.qbittorrent_username,
                    "password": self._settings.qbittorrent_password.get_secret_value(),
                },
            )
        except httpx.HTTPError as exc:
            raise QBittorrentUnavailableError(
                f"Falha de rede ao autenticar na Web API do qBittorrent: {exc}"
            ) from exc
        if response.status_code != 200 or response.text.strip() != "Ok.":
            raise QBittorrentAuthError("Login na Web API do qBittorrent falhou (usuário/senha).")
        self._authenticated = True


def _extract_info_hash(magnet: str) -> str:
    match = _INFO_HASH_RE.search(magnet)
    if not match:
        raise QBittorrentUnavailableError(f"Magnet sem infoHash reconhecível: {magnet!r}")
    return match.group(1).lower()


def _to_torrent_status(entry: dict[str, Any]) -> TorrentStatus:
    return TorrentStatus(
        has_metadata=entry.get("state") not in _NO_METADATA_STATES,
        progress=entry.get("progress", 0.0),
        num_seeds=entry.get("num_seeds", 0),
        num_peers=entry.get("num_leechs", 0),
        save_path=entry.get("save_path", ""),
    )


__all__ = ["QBittorrentAuthError", "QBittorrentClient", "QBittorrentUnavailableError"]
