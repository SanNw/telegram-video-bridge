"""Pipeline FFmpeg: inicialização, monitoramento e reinício automático.

Arquitetura: `FFmpegStreamer` transcodifica a fonte (arquivo local, HTTP(S), HLS,
RTMP ou RTSP) para dois named pipes (FIFO) fixos — vídeo cru (`rawvideo`/yuv420p) e
áudio cru (`s16le`) — que `TelegramCallManager` consome via PyTgCalls. Essa
indireção por pipe é o que permite trocar de fonte ou sobreviver a uma falha do
FFmpeg **sem** derrubar a chamada de vídeo em andamento: a chamada continua lendo
dos mesmos pipes; só o processo que escreve neles é reiniciado.

Nunca dispara comandos FFmpeg soltos: todo argv passa por `build_command`, e o
processo é sempre criado via `asyncio.create_subprocess_exec` (lista de argumentos,
nunca shell=True) — elimina risco de injeção de shell.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path

from app.config.settings import Settings
from app.streaming.exceptions import FFmpegPermanentFailureError, FFmpegStreamerError
from app.streaming.models import FFmpegProcessState, HealthStatus
from app.utils.logging import get_logger
from app.utils.media_contract import (
    AUDIO_CHANNELS,
    AUDIO_SAMPLE_FORMAT,
    AUDIO_SAMPLE_RATE,
    VIDEO_FPS,
    VIDEO_HEIGHT,
    VIDEO_PIXEL_FORMAT,
    VIDEO_WIDTH,
)
from app.utils.retry import RetryExhaustedError, RetryPolicy, retry_with_backoff
from app.utils.sanitize import MediaSource, SourceType

_ffmpeg_logger = get_logger("ffmpeg")
_streaming_logger = get_logger("streaming")

_VIDEO_PIPE_NAME = "video.pipe"
_AUDIO_PIPE_NAME = "audio.pipe"


class FFmpegStreamer:
    """Gerencia o ciclo de vida de um processo FFmpeg que alimenta a chamada."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pipes_dir = settings.media_path / "pipes"
        self.video_pipe_path: Path = self._pipes_dir / _VIDEO_PIPE_NAME
        self.audio_pipe_path: Path = self._pipes_dir / _AUDIO_PIPE_NAME

        self._process: asyncio.subprocess.Process | None = None
        self._current_source: MediaSource | None = None
        self._state: FFmpegProcessState = FFmpegProcessState.IDLE
        self._restart_count = 0
        self._last_error: str | None = None
        self._stopping_intentionally = False

        self._lock = asyncio.Lock()
        self._monitor_task: asyncio.Task[None] | None = None
        self._healthcheck_task: asyncio.Task[None] | None = None
        self._pump_tasks: list[asyncio.Task[None]] = []
        self._on_permanent_failure: Callable[[], Awaitable[None]] | None = None
        self._on_completion: Callable[[], Awaitable[None]] | None = None

    def set_permanent_failure_callback(self, callback: Callable[[], Awaitable[None]]) -> None:
        """Registra callback chamado quando o auto-restart esgota as tentativas (alerta)."""
        self._on_permanent_failure = callback

    def set_completion_callback(self, callback: Callable[[], Awaitable[None]]) -> None:
        """Registra callback chamado quando a fonte termina normalmente (EOF, código 0)."""
        self._on_completion = callback

    def build_command(self, source: MediaSource) -> list[str]:
        """Monta o argv do FFmpeg para `source`. Função pura, sem efeitos colaterais."""
        ffmpeg_bin = self._settings.ffmpeg_path
        command: list[str] = [ffmpeg_bin, "-y", "-loglevel", "warning", "-hide_banner"]
        command.extend(self._input_flags(source))
        command.extend(["-i", source.raw])
        command.extend(
            [
                "-map",
                "0:v:0",
                "-an",
                "-s",
                f"{VIDEO_WIDTH}x{VIDEO_HEIGHT}",
                "-r",
                str(VIDEO_FPS),
                "-c:v",
                "rawvideo",
                "-pix_fmt",
                VIDEO_PIXEL_FORMAT,
                "-f",
                "rawvideo",
                str(self.video_pipe_path),
                "-map",
                "0:a:0?",
                "-vn",
                "-c:a",
                "pcm_s16le",
                "-ar",
                str(AUDIO_SAMPLE_RATE),
                "-ac",
                str(AUDIO_CHANNELS),
                "-f",
                AUDIO_SAMPLE_FORMAT,
                str(self.audio_pipe_path),
            ]
        )
        return command

    @staticmethod
    def _input_flags(source: MediaSource) -> list[str]:
        reconnect_flags = ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]
        if source.type is SourceType.LOCAL_FILE:
            return ["-re"]
        if source.type is SourceType.HTTP:
            return ["-re", *reconnect_flags]
        if source.type is SourceType.HLS:
            return reconnect_flags
        if source.type is SourceType.RTSP:
            return ["-rtsp_transport", "tcp"]
        return []  # RTMP: já é push em tempo real, sem flags extras necessárias

    async def start(self, source: MediaSource) -> None:
        """Inicia o FFmpeg para `source`. Levanta `FFmpegStreamerError` se já rodando."""
        async with self._lock:
            if self._process is not None and self._process.returncode is None:
                raise FFmpegStreamerError("FFmpeg já está em execução; use change_source().")
            await self._start_locked(source)

    async def _start_locked(self, source: MediaSource) -> None:
        if not shutil.which(self._settings.ffmpeg_path) and not Path(self._settings.ffmpeg_path).is_file():
            raise FFmpegStreamerError(f"Binário FFmpeg não encontrado: {self._settings.ffmpeg_path!r}")

        self._ensure_pipes()
        self._state = FFmpegProcessState.STARTING
        self._stopping_intentionally = False
        command = self.build_command(source)
        _ffmpeg_logger.debug("Iniciando FFmpeg para {source}", source=source.raw)

        try:
            self._process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            self._state = FFmpegProcessState.FAILED
            self._last_error = str(exc)
            raise FFmpegStreamerError(f"Falha ao iniciar FFmpeg: {exc}") from exc

        self._current_source = source
        self._state = FFmpegProcessState.RUNNING
        self._pump_tasks = [
            asyncio.create_task(self._pump_stream(self._process.stdout, level="DEBUG")),
            asyncio.create_task(self._pump_stream(self._process.stderr, level="WARNING")),
        ]
        self._monitor_task = asyncio.create_task(self._monitor(self._process))
        if self._healthcheck_task is None or self._healthcheck_task.done():
            self._healthcheck_task = asyncio.create_task(self._periodic_healthcheck())

    async def stop(self) -> None:
        """Encerra o FFmpeg de forma graciosa (SIGTERM, depois SIGKILL se necessário)."""
        async with self._lock:
            await self._stop_locked()

    async def _stop_locked(self) -> None:
        self._stopping_intentionally = True
        if self._healthcheck_task is not None:
            self._healthcheck_task.cancel()
            self._healthcheck_task = None
        for task in self._pump_tasks:
            task.cancel()
        self._pump_tasks = []
        if self._process is not None and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except TimeoutError:
                self._process.kill()
                await self._process.wait()
        if self._monitor_task is not None:
            self._monitor_task.cancel()
            self._monitor_task = None
        self._process = None
        self._state = FFmpegProcessState.STOPPED

    async def restart(self) -> None:
        """Reinicia o FFmpeg mantendo a fonte atual (reinício manual, sem contar retries)."""
        async with self._lock:
            source = self._current_source
            if source is None:
                raise FFmpegStreamerError("Nenhuma fonte ativa para reiniciar.")
            await self._stop_locked()
            self._restart_count += 1
            await self._start_locked(source)

    async def change_source(self, source: MediaSource) -> None:
        """Troca a fonte sem exigir reconexão da chamada: só o writer dos pipes muda."""
        async with self._lock:
            await self._stop_locked()
            await self._start_locked(source)

    def healthcheck(self) -> HealthStatus:
        """Snapshot síncrono do estado atual, consultável por `services/` a qualquer momento."""
        return HealthStatus(
            state=self._state,
            pid=self._process.pid if self._process is not None else None,
            current_source=self._current_source.raw if self._current_source is not None else None,
            restart_count=self._restart_count,
            last_error=self._last_error,
        )

    async def _monitor(self, process: asyncio.subprocess.Process) -> None:
        returncode = await process.wait()
        if self._stopping_intentionally:
            return
        if returncode == 0:
            _streaming_logger.info("FFmpeg concluiu a fonte atual (EOF).")
            self._state = FFmpegProcessState.IDLE
            if self._on_completion is not None:
                await self._on_completion()
            return
        self._last_error = f"FFmpeg encerrou inesperadamente (código {returncode})."
        _streaming_logger.error(self._last_error)
        await self._attempt_auto_restart()

    async def _attempt_auto_restart(self) -> None:
        source = self._current_source
        if source is None:
            self._state = FFmpegProcessState.FAILED
            return

        self._state = FFmpegProcessState.RESTARTING
        policy = RetryPolicy(
            base_delay_seconds=self._settings.retry_base_delay_seconds,
            max_delay_seconds=self._settings.retry_max_delay_seconds,
            max_attempts=self._settings.retry_max_attempts,
            jitter_seconds=self._settings.retry_jitter_seconds,
        )

        async def _try_restart() -> None:
            async with self._lock:
                self._restart_count += 1
                await self._start_locked(source)

        try:
            await retry_with_backoff(_try_restart, policy)
            _streaming_logger.info("FFmpeg reiniciado automaticamente após falha.")
        except RetryExhaustedError as exc:
            self._state = FFmpegProcessState.FAILED
            self._last_error = str(exc)
            _streaming_logger.error(
                "Falha permanente do FFmpeg após {n} tentativas: {err}",
                n=policy.max_attempts,
                err=exc,
            )
            if self._on_permanent_failure is not None:
                await self._on_permanent_failure()
            raise FFmpegPermanentFailureError(str(exc)) from exc

    async def _pump_stream(self, stream: asyncio.StreamReader | None, *, level: str) -> None:
        if stream is None:
            return
        try:
            async for raw_line in stream:
                line = raw_line.decode(errors="replace").rstrip()
                if line:
                    _ffmpeg_logger.log(level, line)
        except asyncio.CancelledError:
            pass

    def _ensure_pipes(self) -> None:
        self._pipes_dir.mkdir(parents=True, exist_ok=True)
        for pipe_path in (self.video_pipe_path, self.audio_pipe_path):
            if not pipe_path.exists():
                os.mkfifo(pipe_path)

    async def _periodic_healthcheck(self) -> None:
        interval = self._settings.ffmpeg_healthcheck_interval_seconds
        try:
            while True:
                await asyncio.sleep(interval)
                _streaming_logger.debug("Healthcheck FFmpeg: {status}", status=self.healthcheck())
        except asyncio.CancelledError:
            pass
