"""Catálogo seguro de legendas locais e do OpenSubtitles."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path

from app.services.opensubtitles_client import OpenSubtitlesClient, SubtitleOption


@dataclass(frozen=True, slots=True)
class LocalSubtitle:
    token: str
    name: str
    path: Path


class SubtitleService:
    def __init__(self, root: Path, client: OpenSubtitlesClient | None) -> None:
        self._root = root.resolve()
        self._client = client
        self._local: dict[str, Path] = {}

    def list_local(self) -> list[LocalSubtitle]:
        self._local = {}
        if not self._root.is_dir():
            return []
        output: list[LocalSubtitle] = []
        for path in sorted(self._root.glob("*.srt"), key=lambda item: item.name.casefold()):
            resolved = path.resolve()
            if not resolved.is_file():
                continue
            try:
                resolved.relative_to(self._root)
            except ValueError:
                continue
            token = str(len(output))
            self._local[token] = resolved
            output.append(LocalSubtitle(token, path.name, resolved))
        return output

    def resolve_local(self, token: str) -> Path:
        path = self._local.get(token)
        if path is None:
            raise ValueError("Essa opção de legenda expirou.")
        return path

    async def search(self, imdb_id: str) -> list[SubtitleOption]:
        return [] if self._client is None else await self._client.search(imdb_id)

    async def download(self, option: SubtitleOption, imdb_id: str) -> Path:
        if self._client is None:
            raise RuntimeError("OpenSubtitles não configurado.")
        content = await self._client.download(option.file_id)
        safe_release = re.sub(r"[^A-Za-z0-9._-]+", "-", option.release).strip(".-")
        filename = f"{imdb_id}-{safe_release or option.file_id}"[:116] + ".srt"
        self._root.mkdir(parents=True, exist_ok=True)
        path = (self._root / filename).resolve()
        path.relative_to(self._root)
        await asyncio.to_thread(path.write_bytes, content)
        return path
