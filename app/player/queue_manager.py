"""Fila FIFO de reprodução: adicionar, remover, pular, avançar automaticamente.

Suporta loop de fila e loop de item (opcionais, via `LoopMode`) e persiste em
disco a cada mutação, via `QueuePersistence` — sobrevive a restart do processo.
Limite máximo de itens vem de `Settings.queue_max_items` (ver Segurança no README).
"""

from __future__ import annotations

import asyncio
from collections import deque

from app.config.settings import Settings
from app.player.exceptions import InvalidQueueIndexError, QueueFullError
from app.player.models import LoopMode, PlaybackState, QueueItem
from app.player.persistence import QueuePersistence
from app.utils.logging import get_logger

_logger = get_logger("player")


class QueueManager:
    """Gerencia a fila FIFO em memória, espelhada em disco a cada mutação."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._persistence = QueuePersistence(settings.queue_data_path)
        self._items: deque[QueueItem] = deque()
        self._current: QueueItem | None = None
        self._loop_mode: LoopMode = LoopMode.OFF
        self._lock = asyncio.Lock()

    async def load(self) -> None:
        """Carrega o estado persistido (se houver) antes do primeiro uso."""
        state = await self._persistence.load()
        if state is not None:
            self._items = deque(state.items)
            self._current = state.current
            self._loop_mode = state.loop_mode
            _logger.info(
                "Fila restaurada do disco: {n} item(ns) pendente(s), loop={loop}.",
                n=len(self._items),
                loop=self._loop_mode.value,
            )

    async def add(self, item: QueueItem) -> int:
        """Adiciona `item` ao fim da fila. Retorna a posição (1-indexada). Levanta `QueueFullError`."""
        async with self._lock:
            if len(self._items) >= self._settings.queue_max_items:
                raise QueueFullError(
                    f"Fila cheia (máximo de {self._settings.queue_max_items} itens)."
                )
            self._items.append(item)
            await self._save()
            return len(self._items)

    async def remove(self, position: int) -> QueueItem:
        """Remove o item na posição `position` (1-indexada). Levanta `InvalidQueueIndexError`."""
        async with self._lock:
            index = position - 1
            if index < 0 or index >= len(self._items):
                raise InvalidQueueIndexError(f"Posição inválida: {position}.")
            items_list = list(self._items)
            removed = items_list.pop(index)
            self._items = deque(items_list)
            await self._save()
            return removed

    async def advance(self) -> QueueItem | None:
        """Avança automaticamente (fim natural do item atual), respeitando `loop_mode`."""
        async with self._lock:
            result = self._advance_locked(respect_item_loop=True)
            await self._save()
            return result

    async def skip(self) -> QueueItem | None:
        """Força avanço para o próximo item (`/skip`), ignorando loop de item."""
        async with self._lock:
            result = self._advance_locked(respect_item_loop=False)
            await self._save()
            return result

    def _advance_locked(self, *, respect_item_loop: bool) -> QueueItem | None:
        if respect_item_loop and self._loop_mode is LoopMode.ITEM and self._current is not None:
            return self._current

        if self._current is not None and self._loop_mode is LoopMode.QUEUE:
            self._items.append(self._current)

        if not self._items:
            self._current = None
            return None

        self._current = self._items.popleft()
        return self._current

    async def clear(self) -> None:
        """Esvazia a fila pendente. Não afeta o item em reprodução (`current`)."""
        async with self._lock:
            self._items.clear()
            await self._save()

    async def discard_current(self) -> None:
        async with self._lock:
            self._current = None
            await self._save()

    async def set_loop_mode(self, mode: LoopMode) -> None:
        """Define o modo de loop (`off`, `item` ou `queue`)."""
        async with self._lock:
            self._loop_mode = mode
            await self._save()

    async def set_subtitle_delay(self, delay_ms: int) -> None:
        async with self._lock:
            if self._current is not None:
                self._current.subtitle_delay_ms = delay_ms
                await self._save()

    async def set_subtitle_path(self, path: str | None) -> None:
        async with self._lock:
            if self._current is not None:
                self._current.subtitle_path = path
                self._current.subtitles_enabled = path is not None
                await self._save()

    async def set_subtitles_enabled(self, enabled: bool) -> None:
        async with self._lock:
            if self._current is not None:
                self._current.subtitles_enabled = enabled
                await self._save()

    def snapshot(self) -> PlaybackState:
        """Estado atual, consultável por `services/` para formatar `/queue` e `/status`."""
        return PlaybackState(
            items=list(self._items), current=self._current, loop_mode=self._loop_mode
        )

    async def _save(self) -> None:
        await self._persistence.save(self.snapshot())
