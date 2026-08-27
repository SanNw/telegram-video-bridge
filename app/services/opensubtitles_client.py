"""Cliente assíncrono mínimo para a API oficial OpenSubtitles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

_BASE_URL = "https://api.opensubtitles.com/api/v1"
_MAX_SUBTITLE_BYTES = 2 * 1024 * 1024


class OpenSubtitlesError(Exception):
    """Falha segura da integração, sem expor respostas ou credenciais."""


@dataclass(frozen=True, slots=True)
class SubtitleOption:
    file_id: int
    language: str
    release: str
    downloads: int


class OpenSubtitlesClient:
    def __init__(
        self,
        api_key: str,
        username: str,
        password: str,
        user_agent: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._username = username
        self._password = password
        self._token: str | None = None
        self._client = client or httpx.AsyncClient(
            base_url=_BASE_URL,
            timeout=15.0,
            headers={
                "Api-Key": api_key,
                "User-Agent": user_agent,
                "Content-Type": "application/json",
            },
        )
        if client is not None:
            self._client.headers.update(
                {
                    "Api-Key": api_key,
                    "User-Agent": user_agent,
                    "Content-Type": "application/json",
                }
            )

    async def search(self, imdb_id: str) -> list[SubtitleOption]:
        try:
            numeric_imdb_id = str(int(imdb_id.removeprefix("tt")))
        except ValueError as exc:
            raise OpenSubtitlesError("IMDb ID inválido.") from exc
        response = await self._authenticated_request(
            "GET",
            "/subtitles",
            params={"imdb_id": numeric_imdb_id, "languages": "pt-BR,pt"},
        )
        try:
            entries = response.json().get("data", [])
            options: list[SubtitleOption] = []
            for entry in entries:
                attributes = entry["attributes"]
                file_id = attributes["files"][0]["file_id"]
                options.append(
                    SubtitleOption(
                        file_id=int(file_id),
                        language=str(attributes.get("language") or "pt"),
                        release=str(attributes.get("release") or "Legenda"),
                        downloads=int(attributes.get("download_count") or 0),
                    )
                )
            return options
        except (AttributeError, KeyError, TypeError, ValueError, IndexError) as exc:
            raise OpenSubtitlesError("Resposta inválida do OpenSubtitles.") from exc

    async def download(self, file_id: int) -> bytes:
        response = await self._authenticated_request("POST", "/download", json={"file_id": file_id})
        try:
            link = response.json()["link"]
            if not isinstance(link, str):
                raise TypeError
            subtitle = await self._client.get(link)
            subtitle.raise_for_status()
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise OpenSubtitlesError("Não foi possível baixar a legenda.") from exc
        if len(subtitle.content) > _MAX_SUBTITLE_BYTES:
            raise OpenSubtitlesError("Legenda excede 2 MB.")
        return subtitle.content

    async def _login(self) -> None:
        try:
            response = await self._client.post(
                "/login", json={"username": self._username, "password": self._password}
            )
            response.raise_for_status()
            token = response.json()["token"]
            if not isinstance(token, str) or not token:
                raise TypeError
            self._token = token
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise OpenSubtitlesError("Autenticação no OpenSubtitles falhou.") from exc

    async def _authenticated_request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        if self._token is None:
            await self._login()
        for attempt in range(2):
            try:
                response = await self._client.request(
                    method,
                    url,
                    headers={"Authorization": f"Bearer {self._token}"},
                    **kwargs,
                )
            except httpx.HTTPError as exc:
                raise OpenSubtitlesError("OpenSubtitles indisponível.") from exc
            if response.status_code != 401 or attempt == 1:
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    message = (
                        "Limite do OpenSubtitles atingido."
                        if response.status_code == 429
                        else "OpenSubtitles recusou a solicitação."
                    )
                    raise OpenSubtitlesError(message) from exc
                return response
            self._token = None
            await self._login()
        raise OpenSubtitlesError("OpenSubtitles recusou a solicitação.")

    async def close(self) -> None:
        await self._client.aclose()
