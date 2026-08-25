"""Pesquisa e download progressivo de filmes publicados no canal configurado."""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from pathlib import Path

from pyrogram import Client
from pyrogram.enums import MessagesFilter
from pyrogram.types import Message

from app.config.settings import Settings
from app.services.addon_service import AddonService
from app.services.playback_service import PlaybackService
from app.services.tmdb_service import TMDBService
from app.utils.language_detection import has_portuguese_audio
from app.utils.logging import get_logger
from app.utils.sanitize import MediaSource

_logger = get_logger("services")
_VIDEO_EXTENSIONS = frozenset({".mp4", ".mkv", ".avi", ".mov", ".ts", ".m2ts"})


@dataclass(frozen=True, slots=True)
class ChannelMovie:
    message_id: int
    title: str
    size_bytes: int


class ChannelMediaService:
    def __init__(
        self,
        settings: Settings,
        client: Client,
        playback: PlaybackService,
        addons: AddonService,
        tmdb: TMDBService,
    ) -> None:
        self._settings = settings
        self._client = client
        self._playback = playback
        self._addons = addons
        self._tmdb = tmdb
        self._chat_id = settings.stream_chat_id
        self._downloads: dict[str, asyncio.Task[None]] = {}

    async def search(self, query: str) -> list[ChannelMovie]:
        if self._chat_id is None:
            return []
        found: list[ChannelMovie] = []
        seen: set[int] = set()
        for media_filter in (MessagesFilter.VIDEO, MessagesFilter.DOCUMENT):
            async for message in self._client.search_messages(
                self._chat_id, query=query, filter=media_filter, limit=20
            ):
                movie = self._as_movie(message)
                if movie is not None and movie.message_id not in seen:
                    seen.add(movie.message_id)
                    found.append(movie)
        return found[:20]

    async def play(self, message_id: int, requested_by: int) -> int:
        if self._chat_id is None:
            raise ValueError("STREAM_CHAT_ID não configurado.")
        message = await self._client.get_messages(self._chat_id, message_id)
        if not isinstance(message, Message) or self._as_movie(message) is None:
            raise ValueError("Publicação não encontrada ou sem arquivo de vídeo.")
        movie = self._as_movie(message)
        assert movie is not None
        media = message.video or message.document
        assert media is not None
        filename = Path(media.file_name or f"channel-{message_id}.mp4").name
        destination = (
            self._settings.qbittorrent_local_path
            / "channel"
            / f"{message_id}-{time.time_ns()}-{filename}"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        ready = asyncio.Event()
        task = asyncio.create_task(self._download(message, destination, ready))
        self._downloads[str(destination.resolve())] = task
        try:
            async with asyncio.timeout(self._settings.torrent_timeout_seconds):
                await ready.wait()
                if task.done():
                    task.result()
        except Exception:
            await self._discard_download(destination)
            raise
        _logger.info("Buffer do canal atingido: {path}", path=destination)
        try:
            subtitle_path = None
            clean_title = self._clean_channel_title(movie.title)
            if not has_portuguese_audio(clean_title):
                _logger.info(
                    "Buscando legenda para vídeo do canal: {title} (clean: {clean})",
                    title=movie.title,
                    clean=clean_title,
                )
                imdb_id = await self._tmdb.find_imdb_id(clean_title)
                if imdb_id is not None:
                    subtitle_path = await self._addons.prepare_subtitle(imdb_id, clean_title)
                else:
                    _logger.warning(
                        "IMDb ID não encontrado no TMDB para vídeo do canal: {title} (clean: {clean})",
                        title=movie.title,
                        clean=clean_title,
                    )
            else:
                _logger.info(
                    "Vídeo do canal possui áudio em português; legenda ignorada: {title}",
                    title=clean_title,
                )
            return await self._playback.play(str(destination), requested_by, subtitle_path)
        except Exception:
            await self._discard_download(destination)
            raise

    async def release(self, source: MediaSource) -> None:
        task = self._downloads.pop(source.raw, None)
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        Path(source.raw).unlink(missing_ok=True)
        _logger.info("Arquivo temporário do canal removido: {path}", path=source.raw)

    async def close(self) -> None:
        downloads = list(self._downloads.items())
        for _, task in downloads:
            task.cancel()
        await asyncio.gather(*(task for _, task in downloads), return_exceptions=True)
        for path, _ in downloads:
            Path(path).unlink(missing_ok=True)
        self._downloads.clear()

    async def _discard_download(self, destination: Path) -> None:
        task = self._downloads.pop(str(destination.resolve()), None)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        destination.unlink(missing_ok=True)

    async def _download(self, message: Message, destination: Path, ready: asyncio.Event) -> None:
        downloaded = 0
        target = int(self._settings.torrent_buffer_mb * 1024 * 1024)
        try:
            with destination.open("wb") as output:
                async for chunk in self._client.stream_media(message):  # type: ignore[union-attr]
                    output.write(chunk)
                    downloaded += len(chunk)
                    if downloaded >= target:
                        ready.set()
            ready.set()
        except Exception:
            ready.set()
            raise

    @staticmethod
    def _clean_channel_title(raw: str) -> str:
        """Extrai o título do filme de um título de postagem do canal.

        Trata padrões como:
        - "#F33 Ran - Akira Kurosawa"
        - "Ran (1985) - Akira Kurosawa"
        """
        cleaned = re.sub(r"^#\w+\s*", "", raw).strip()
        if " - " in cleaned:
            parts = [p.strip() for p in cleaned.split(" - ")]
            if len(parts) >= 2:
                last = parts[-1]
                if re.match(r"^[A-Z][a-z]+ [A-Z][a-z]+$", last):
                    cleaned = " - ".join(parts[:-1])
        cleaned = re.sub(r"\s*\((?:19|20)\d{2}\)\s*$", "", cleaned)
        return cleaned.strip()

    @staticmethod
    def _as_movie(message: Message) -> ChannelMovie | None:
        media = message.video or message.document
        if media is None:
            return None
        filename = media.file_name or ""
        if message.document is not None and Path(filename).suffix.lower() not in _VIDEO_EXTENSIONS:
            return None
        title = (message.caption or filename or f"Vídeo {message.id}").splitlines()[0]
        return ChannelMovie(message.id, title[:100], media.file_size or 0)
