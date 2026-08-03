"""Orquestração entre `player/`, `streaming/` e `telegram/`.

Único ponto de comunicação entre `bot/` e as camadas internas — handlers de
comando só chamam métodos daqui e formatam a resposta; nenhuma lógica de negócio
vive em `bot/`.

Fluxo de dados: bot → services (aqui) → player/streaming/telegram.

Reconexão automática de FFmpeg e da chamada já acontece dentro de cada camada
(`FFmpegStreamer`/`TelegramCallManager`); esta classe só reage a duas coisas que
elas não podem resolver sozinhas: o item atual terminar normalmente (precisa
saber qual é o próximo da fila) e uma falha permanente de qualquer uma das duas
(precisa degradar graciosamente — pausar e alertar — em vez de travar o processo).
"""

from __future__ import annotations

import asyncio

from app.config.settings import Settings
from app.player.models import PlaybackState, QueueItem
from app.player.queue_manager import QueueManager
from app.services.exceptions import NothingPlayingError
from app.services.models import ServiceStatus
from app.streaming.ffmpeg_streamer import FFmpegStreamer
from app.telegram.call_manager import TelegramCallManager
from app.utils.logging import get_logger
from app.utils.sanitize import resolve_source

_logger = get_logger("services")


class PlaybackService:
    """Orquestra fila, streaming e chamada. Ponto único de entrada para `bot/`."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._queue = QueueManager(settings)
        self._streamer = FFmpegStreamer(settings)
        self._call_manager = TelegramCallManager(settings)

        self._lock = asyncio.Lock()
        self._is_active = False
        self._degraded = False
        self._degraded_reason: str | None = None

        self._streamer.set_completion_callback(self._handle_item_completed)
        self._streamer.set_permanent_failure_callback(self._handle_streamer_permanent_failure)
        self._call_manager.set_permanent_failure_callback(self._handle_call_permanent_failure)

    async def start(self) -> None:
        """Carrega a fila persistida e conecta o cliente Telegram. Não retoma reprodução."""
        await self._queue.load()
        await self._call_manager.start()
        _logger.info("PlaybackService iniciado.")

    async def shutdown(self) -> None:
        """Encerra reprodução ativa (se houver) e desconecta o cliente Telegram."""
        async with self._lock:
            if self._is_active:
                await self._stop_active_locked()
        await self._call_manager.stop()
        _logger.info("PlaybackService encerrado.")

    async def play(self, source_raw: str, requested_by: int) -> int:
        """Valida e enfileira `source_raw`; inicia a reprodução imediatamente se ociosa.

        Retorna a posição na fila (1-indexada). Levanta `InvalidSourceError` (fonte
        inválida) ou `QueueFullError` (fila cheia) — ambas tratadas por `bot/`.
        """
        media_source = resolve_source(source_raw, self._settings.media_path)
        item = QueueItem(source=media_source, requested_by=requested_by)
        position = await self._queue.add(item)

        async with self._lock:
            if not self._is_active:
                next_item = await self._queue.advance()
                if next_item is not None:
                    await self._start_or_switch_locked(next_item)
        return position

    async def pause(self) -> None:
        """Pausa a chamada. Levanta `NothingPlayingError` se nada estiver tocando."""
        if not self._is_active:
            raise NothingPlayingError("Nada está tocando no momento.")
        await self._call_manager.pause_call()

    async def resume(self) -> None:
        """Retoma uma chamada pausada. Levanta `NothingPlayingError` se nada estiver tocando."""
        if not self._is_active:
            raise NothingPlayingError("Nada está tocando no momento.")
        await self._call_manager.resume_call()

    async def stop_playback(self) -> None:
        """Para a reprodução atual e sai da chamada. Não limpa a fila pendente."""
        async with self._lock:
            if not self._is_active:
                raise NothingPlayingError("Nada está tocando no momento.")
            await self._stop_active_locked()

    async def skip(self) -> QueueItem | None:
        """Pula para o próximo item da fila (ignora loop de item). `None` se a fila esvaziou."""
        async with self._lock:
            if not self._is_active:
                raise NothingPlayingError("Nada está tocando no momento.")
            next_item = await self._queue.skip()
            if next_item is None:
                await self._stop_active_locked()
                return None
            await self._start_or_switch_locked(next_item)
            return next_item

    async def clear_queue(self) -> None:
        """Esvazia os itens pendentes da fila (não afeta o item em reprodução)."""
        await self._queue.clear()

    def queue_snapshot(self) -> PlaybackState:
        """Estado completo da fila, para formatar `/queue`."""
        return self._queue.snapshot()

    def status(self) -> ServiceStatus:
        """Snapshot combinado de streaming + chamada + fila, para formatar `/status`."""
        snapshot = self._queue.snapshot()
        return ServiceStatus(
            streaming=self._streamer.healthcheck(),
            call=self._call_manager.healthcheck(),
            queue_length=len(snapshot.items),
            loop_mode=snapshot.loop_mode,
            degraded=self._degraded,
            degraded_reason=self._degraded_reason,
        )

    async def _start_or_switch_locked(self, item: QueueItem) -> None:
        await self._streamer.change_source(item.source)
        await self._call_manager.send_media(self._streamer.video_pipe_path, self._streamer.audio_pipe_path)
        self._is_active = True
        self._degraded = False
        self._degraded_reason = None

    async def _stop_active_locked(self) -> None:
        await self._streamer.stop()
        await self._call_manager.leave_call()
        self._is_active = False

    async def _handle_item_completed(self) -> None:
        async with self._lock:
            next_item = await self._queue.advance()
            if next_item is None:
                await self._stop_active_locked()
                _logger.info("Fila vazia; reprodução encerrada.")
                return
            await self._start_or_switch_locked(next_item)

    async def _handle_streamer_permanent_failure(self) -> None:
        async with self._lock:
            self._degraded = True
            self._degraded_reason = (
                "FFmpeg falhou permanentemente após esgotar as tentativas de reinício."
            )
            await self._call_manager.pause_call()
        _logger.critical(self._degraded_reason)

    async def _handle_call_permanent_failure(self) -> None:
        async with self._lock:
            self._degraded = True
            self._degraded_reason = (
                "Reconexão da chamada falhou permanentemente após esgotar as tentativas."
            )
        _logger.critical(self._degraded_reason)
