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
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pyrogram import Client

from app.config.settings import Settings
from app.player.models import LoopMode, PlaybackState, QueueItem
from app.player.queue_manager import QueueManager
from app.services.exceptions import InvalidVolumeError, NothingPlayingError
from app.services.models import ServiceStatus
from app.streaming.ffmpeg_streamer import FFmpegStreamer
from app.telegram.call_manager import TelegramCallManager
from app.utils.logging import get_logger
from app.utils.sanitize import MediaSource, SourceType, resolve_source

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
        self._started_at: datetime | None = None
        self._current_started_at: datetime | None = None
        self._paused_at_seconds: float | None = None
        self._volume = 100
        self._source_released_callbacks: list[Callable[[MediaSource], Awaitable[None]]] = []

        self._streamer.set_completion_callback(self._handle_item_completed)
        self._streamer.set_permanent_failure_callback(self._handle_streamer_permanent_failure)
        self._streamer.set_source_released_callback(self._handle_source_released)
        self._call_manager.set_permanent_failure_callback(self._handle_call_permanent_failure)

    def set_source_released_callback(
        self, callback: Callable[[MediaSource], Awaitable[None]]
    ) -> None:
        """Registra callback chamado quando uma fonte deixa de ser a atual.

        Repassa direto para `FFmpegStreamer` — usado por `TorrentService.release`
        para decidir se remove o torrent do qBittorrent ou o mantém em seed.
        """
        self._source_released_callbacks.append(callback)

    async def _handle_source_released(self, source: MediaSource) -> None:
        results = await asyncio.gather(
            *(callback(source) for callback in self._source_released_callbacks),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                _logger.opt(exception=result).error(
                    "Falha ao liberar uma fonte de mídia; as demais limpezas continuaram."
                )

    @property
    def client(self) -> Client:
        """Cliente Pyrogram subjacente, para `bot/` registrar handlers de comando."""
        return self._call_manager.client

    async def start(self) -> None:
        """Carrega a fila persistida, conecta o Telegram e retoma o item atual."""
        self._started_at = datetime.now(UTC)
        await self._queue.load()
        await self._call_manager.start()
        current = self._queue.snapshot().current
        while (
            current is not None
            and current.source.type is SourceType.LOCAL_FILE
            and not Path(current.source.raw).is_file()
        ):
            _logger.warning(
                "Ignorando arquivo restaurado ausente: {source}", source=current.source.raw
            )
            current = await self._queue.advance()
        if current is not None:
            try:
                await self._start_or_switch_locked(current)
            except Exception as exc:
                await self._streamer.stop(notify_release=False)
                self._degraded = True
                self._degraded_reason = str(exc)
                _logger.opt(exception=exc).error(
                    "Não foi possível retomar a reprodução; aguardando novo comando."
                )
        _logger.info("PlaybackService iniciado.")

    async def shutdown(self) -> None:
        """Encerra reprodução ativa (se houver) e desconecta o cliente Telegram."""
        async with self._lock:
            if self._is_active:
                await self._streamer.stop(notify_release=False)
                await self._call_manager.leave_call()
                self._is_active = False
                self._current_started_at = None
        await self._call_manager.stop()
        _logger.info("PlaybackService encerrado.")

    async def play(
        self,
        source_raw: str,
        requested_by: int,
        subtitle_path: str | None = None,
        *,
        media_id: str | None = None,
        display_title: str | None = None,
    ) -> int:
        """Valida e enfileira `source_raw`; inicia a reprodução imediatamente se ociosa.

        Retorna a posição na fila (1-indexada). Levanta `InvalidSourceError` (fonte
        inválida) ou `QueueFullError` (fila cheia) — ambas tratadas por `bot/`.
        """
        media_source = resolve_source(source_raw, self._settings.media_path)
        item = QueueItem(
            source=media_source,
            requested_by=requested_by,
            subtitle_path=subtitle_path,
            media_id=media_id,
            display_title=display_title,
        )
        position = await self._queue.add(item)

        async with self._lock:
            if not self._is_active:
                next_item = await self._queue.advance()
                if next_item is not None:
                    try:
                        await self._start_or_switch_locked(next_item)
                    except Exception:
                        try:
                            await self._streamer.stop()
                        finally:
                            await self._queue.discard_current()
                        raise
        return position

    async def pause(self) -> None:
        """Pausa a chamada. Levanta `NothingPlayingError` se nada estiver tocando."""
        if not self._is_active:
            raise NothingPlayingError("Nada está tocando no momento.")
        if self._call_manager.rtmp_active:
            self._paused_at_seconds = self._elapsed_seconds()
            await self._streamer.stop(notify_release=False)
        else:
            await self._call_manager.pause_call()

    async def resume(self) -> None:
        """Retoma uma chamada pausada. Levanta `NothingPlayingError` se nada estiver tocando."""
        if not self._is_active:
            raise NothingPlayingError("Nada está tocando no momento.")
        if self._call_manager.rtmp_active and self._paused_at_seconds is not None:
            current = self._queue.snapshot().current
            if current is None:
                raise NothingPlayingError("Nada está tocando no momento.")
            await self._restart_rtmp_locked(current, self._paused_at_seconds)
            self._paused_at_seconds = None
        else:
            await self._call_manager.resume_call()

    async def stop_playback(self) -> None:
        """Para a reprodução atual e sai da chamada. Não limpa a fila pendente."""
        async with self._lock:
            if not self._is_active:
                raise NothingPlayingError("Nada está tocando no momento.")
            await self._stop_active_locked()
            await self._queue.discard_current()

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

    async def remove_from_queue(self, position: int) -> QueueItem:
        """Remove o item na posição `position` (1-indexada) da fila pendente.

        Levanta `InvalidQueueIndexError` (tratada por `bot/`) se a posição não existir.
        """
        return await self._queue.remove(position)

    async def set_loop_mode(self, mode: LoopMode) -> None:
        """Define o modo de loop da fila (`off`, `item` ou `queue`)."""
        await self._queue.set_loop_mode(mode)

    async def set_volume(self, volume: int) -> None:
        """Ajusta o volume da chamada (0-200).

        Levanta `NothingPlayingError` se nada estiver tocando, `InvalidVolumeError`
        se `volume` estiver fora do intervalo permitido.
        """
        if not self._is_active:
            raise NothingPlayingError("Nada está tocando no momento.")
        if not 0 <= volume <= 200:
            raise InvalidVolumeError("Volume deve estar entre 0 e 200.")
        self._volume = volume
        if self._call_manager.rtmp_active:
            current = self._queue.snapshot().current
            if current is None:
                raise NothingPlayingError("Nada está tocando no momento.")
            await self._restart_rtmp_locked(current, self._elapsed_seconds())
        else:
            await self._call_manager.change_volume(volume)

    async def restart_current(self) -> None:
        """Reinicia o item atual do zero (mesma fonte). `NothingPlayingError` se ocioso."""
        if not self._is_active:
            raise NothingPlayingError("Nada está tocando no momento.")
        await self._streamer.restart()
        self._current_started_at = datetime.now(UTC)

    async def set_subtitle_delay(self, delay_ms: int) -> None:
        """Ajusta a legenda e retoma aproximadamente da posição atual."""
        current = self._queue.snapshot().current
        if (
            not self._is_active
            or current is None
            or current.subtitle_path is None
            or not current.subtitles_enabled
        ):
            raise NothingPlayingError("Não há filme legendado em reprodução.")
        elapsed = (
            (datetime.now(UTC) - self._current_started_at).total_seconds()
            if self._current_started_at is not None
            else 0.0
        )
        await self._queue.set_subtitle_delay(delay_ms)
        try:
            output_url = await self._call_manager.prepare_rtmp()
        except Exception as exc:
            _logger.warning("RTMP indisponível ao ajustar legenda: {err}", err=exc)
            output_url = None
        await self._streamer.change_source(
            current.source,
            output_url,
            current.subtitle_path,
            delay_ms,
            elapsed,
            self._volume,
        )
        self._current_started_at = datetime.now(UTC) - timedelta(seconds=elapsed)

    async def set_subtitles_enabled(self, enabled: bool) -> None:
        current = self._queue.snapshot().current
        if not self._is_active or current is None or current.subtitle_path is None:
            raise NothingPlayingError("Não há legenda disponível para este filme.")
        elapsed = (
            (datetime.now(UTC) - self._current_started_at).total_seconds()
            if self._current_started_at is not None
            else 0.0
        )
        await self._queue.set_subtitles_enabled(enabled)
        try:
            output_url = await self._call_manager.prepare_rtmp()
        except Exception as exc:
            _logger.warning("RTMP indisponível ao alternar legenda: {err}", err=exc)
            output_url = None
        await self._streamer.change_source(
            current.source,
            output_url,
            current.subtitle_path if enabled else None,
            current.subtitle_delay_ms,
            elapsed,
            self._volume,
        )
        self._current_started_at = datetime.now(UTC) - timedelta(seconds=elapsed)

    def now_playing(self) -> tuple[QueueItem, datetime] | None:
        """Item em reprodução + horário de início, para `/nowplaying`. `None` se ocioso."""
        current = self._queue.snapshot().current
        if current is None or self._current_started_at is None:
            return None
        return current, self._current_started_at

    def uptime(self) -> timedelta:
        """Há quanto tempo o processo está em execução, para `/uptime`."""
        if self._started_at is None:
            return timedelta(0)
        return datetime.now(UTC) - self._started_at

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
        subtitle_path = item.subtitle_path if item.subtitles_enabled else None
        try:
            output_url = await self._call_manager.prepare_rtmp()
            if subtitle_path is None:
                await self._streamer.change_source(
                    item.source, output_url, volume_percent=self._volume
                )
            else:
                await self._streamer.change_source(
                    item.source,
                    output_url,
                    subtitle_path,
                    item.subtitle_delay_ms,
                    volume_percent=self._volume,
                )
            _logger.info("Reprodução iniciada por RTMP.")
        except Exception as exc:
            _logger.warning("RTMP indisponível ({err}); usando PyTgCalls.", err=exc)
            if subtitle_path is None:
                await self._streamer.change_source(item.source)
            else:
                await self._streamer.change_source(
                    item.source, None, subtitle_path, item.subtitle_delay_ms
                )
            if self._is_active:
                await self._call_manager.send_media(
                    self._streamer.video_pipe_path, self._streamer.audio_pipe_path
                )
            else:
                await self._call_manager.join_call(
                    self._streamer.video_pipe_path, self._streamer.audio_pipe_path
                )
        self._is_active = True
        self._degraded = False
        self._degraded_reason = None
        self._current_started_at = datetime.now(UTC)
        self._paused_at_seconds = None

    async def _stop_active_locked(self) -> None:
        await self._streamer.stop()
        await self._call_manager.leave_call()
        self._is_active = False
        self._current_started_at = None
        self._paused_at_seconds = None

    def _elapsed_seconds(self) -> float:
        if self._current_started_at is None:
            return 0.0
        return max(0.0, (datetime.now(UTC) - self._current_started_at).total_seconds())

    async def _restart_rtmp_locked(self, item: QueueItem, elapsed: float) -> None:
        output_url = await self._call_manager.prepare_rtmp()
        await self._streamer.change_source(
            item.source,
            output_url,
            item.subtitle_path if item.subtitles_enabled else None,
            item.subtitle_delay_ms,
            elapsed,
            self._volume,
        )
        self._current_started_at = datetime.now(UTC) - timedelta(seconds=elapsed)

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
