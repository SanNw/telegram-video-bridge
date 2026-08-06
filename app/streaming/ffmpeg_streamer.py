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

Supervisão: existe exatamente UMA task de supervisão (`_supervise`) por sessão de
reprodução (do `start()`/`change_source()` até o próximo `stop()`/`change_source()`).
Todo reinício automático acontece dentro do laço dessa mesma task — nunca cria uma
task de supervisão nova a cada tentativa, o que causaria uma explosão de tasks
concorrentes se o processo falhar repetidas vezes em sequência rápida.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path

from app.config.settings import Settings
from app.streaming.exceptions import FFmpegStreamerError
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
from app.utils.retry import RetryPolicy
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
        self._supervisor_task: asyncio.Task[None] | None = None
        self._healthcheck_task: asyncio.Task[None] | None = None
        self._pump_tasks: list[asyncio.Task[None]] = []
        self._on_permanent_failure: Callable[[], Awaitable[None]] | None = None
        self._on_completion: Callable[[], Awaitable[None]] | None = None
        self._on_source_released: Callable[[MediaSource], Awaitable[None]] | None = None

    def set_permanent_failure_callback(self, callback: Callable[[], Awaitable[None]]) -> None:
        """Registra callback chamado quando o auto-restart esgota as tentativas (alerta)."""
        self._on_permanent_failure = callback

    def set_completion_callback(self, callback: Callable[[], Awaitable[None]]) -> None:
        """Registra callback chamado quando a fonte termina normalmente (EOF, código 0)."""
        self._on_completion = callback

    def set_source_released_callback(
        self, callback: Callable[[MediaSource], Awaitable[None]]
    ) -> None:
        """Registra callback chamado quando uma fonte deixa de ser a atual.

        Disparado em `_stop_locked()` (cobre item concluído avançando, skip,
        stop, shutdown e troca de fonte) — usado por `TorrentService.release`
        para decidir se remove o torrent do qBittorrent ou o mantém em seed.
        """
        self._on_source_released = callback

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
        reconnect_flags = [
            "-reconnect",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_delay_max",
            "5",
        ]
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
            await self._launch(source)
        self._spawn_supervisor(source)

    async def stop(self) -> None:
        """Encerra o FFmpeg de forma graciosa (SIGTERM, depois SIGKILL se necessário)."""
        async with self._lock:
            await self._stop_locked()

    async def restart(self) -> None:
        """Reinicia o FFmpeg mantendo a fonte atual (reinício manual, sem contar retries)."""
        source = self._current_source
        if source is None:
            raise FFmpegStreamerError("Nenhuma fonte ativa para reiniciar.")
        async with self._lock:
            # notify_release=False: a fonte não está sendo trocada, só
            # reiniciada — disparar o callback aqui removeria um torrent com
            # REMOVE_TORRENT_AFTER_PLAY=True bem no meio do restart, quebrando
            # a tentativa seguinte, que lê exatamente o mesmo arquivo.
            await self._stop_locked(notify_release=False)
            self._restart_count += 1
            await self._launch(source)
        self._spawn_supervisor(source)

    async def change_source(self, source: MediaSource) -> None:
        """Troca a fonte sem exigir reconexão da chamada: só o writer dos pipes muda."""
        async with self._lock:
            await self._stop_locked()
            await self._launch(source)
        self._spawn_supervisor(source)

    def healthcheck(self) -> HealthStatus:
        """Snapshot síncrono do estado atual, consultável por `services/` a qualquer momento."""
        return HealthStatus(
            state=self._state,
            pid=self._process.pid if self._process is not None else None,
            current_source=self._current_source.raw if self._current_source is not None else None,
            restart_count=self._restart_count,
            last_error=self._last_error,
        )

    async def _launch(self, source: MediaSource) -> None:
        """Spawna o processo FFmpeg para `source`. Não gerencia a task de supervisão."""
        if (
            not shutil.which(self._settings.ffmpeg_path)
            and not Path(self._settings.ffmpeg_path).is_file()
        ):
            raise FFmpegStreamerError(
                f"Binário FFmpeg não encontrado: {self._settings.ffmpeg_path!r}"
            )

        self._ensure_pipes()
        self._state = FFmpegProcessState.STARTING
        self._stopping_intentionally = False
        self._cancel_pump_tasks()
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
        if self._healthcheck_task is None or self._healthcheck_task.done():
            self._healthcheck_task = asyncio.create_task(self._periodic_healthcheck())

    def _spawn_supervisor(self, source: MediaSource) -> None:
        if self._supervisor_task is not None and not self._supervisor_task.done():
            self._supervisor_task.cancel()
        self._supervisor_task = asyncio.create_task(self._supervise(source))

    async def _stop_locked(self, *, notify_release: bool = True) -> None:
        if (
            notify_release
            and self._current_source is not None
            and self._on_source_released is not None
        ):
            await self._on_source_released(self._current_source)
        self._stopping_intentionally = True
        if self._supervisor_task is not None:
            self._supervisor_task.cancel()
            self._supervisor_task = None
        if self._healthcheck_task is not None:
            self._healthcheck_task.cancel()
            self._healthcheck_task = None
        self._cancel_pump_tasks()
        if self._process is not None and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(
                    self._process.wait(), timeout=self._settings.ffmpeg_terminate_timeout_seconds
                )
            except TimeoutError:
                self._process.kill()
                await self._process.wait()
        self._process = None
        self._state = FFmpegProcessState.STOPPED

    def _cancel_pump_tasks(self) -> None:
        for task in self._pump_tasks:
            task.cancel()
        self._pump_tasks = []

    async def _supervise(self, source: MediaSource) -> None:
        """Laço único de supervisão: aguarda o processo, reage a EOF ou falha.

        Todo reinício automático desta sessão de reprodução acontece aqui dentro,
        sequencialmente — nunca cria uma nova task de supervisão por tentativa.
        """
        policy = RetryPolicy(
            base_delay_seconds=self._settings.retry_base_delay_seconds,
            max_delay_seconds=self._settings.retry_max_delay_seconds,
            max_attempts=self._settings.retry_max_attempts,
            jitter_seconds=self._settings.retry_jitter_seconds,
        )
        consecutive_failures = 0

        while True:
            process = self._process
            if process is None:
                return
            returncode = await process.wait()
            if self._stopping_intentionally:
                return

            if returncode == 0:
                _streaming_logger.info("FFmpeg concluiu a fonte atual (EOF).")
                self._state = FFmpegProcessState.IDLE
                if self._on_completion is not None:
                    await self._on_completion()
                return

            consecutive_failures += 1
            self._last_error = f"FFmpeg encerrou inesperadamente (código {returncode})."
            _streaming_logger.error(self._last_error)

            if consecutive_failures > policy.max_attempts:
                self._state = FFmpegProcessState.FAILED
                _streaming_logger.error(
                    "Falha permanente do FFmpeg após {n} tentativas.", n=policy.max_attempts
                )
                if self._on_permanent_failure is not None:
                    await self._on_permanent_failure()
                return

            delay = policy.delay_for_attempt(consecutive_failures)
            self._state = FFmpegProcessState.RESTARTING
            await asyncio.sleep(delay)

            try:
                async with self._lock:
                    self._restart_count += 1
                    await self._launch(source)
            except FFmpegStreamerError as exc:
                self._last_error = str(exc)
                self._state = FFmpegProcessState.FAILED
                _streaming_logger.error(
                    "Falha permanente ao tentar reiniciar o FFmpeg: {err}", err=exc
                )
                if self._on_permanent_failure is not None:
                    await self._on_permanent_failure()
                return

    async def _pump_stream(self, stream: asyncio.StreamReader | None, *, level: str) -> None:
        if stream is None:
            return
        with contextlib.suppress(asyncio.CancelledError):
            async for raw_line in stream:
                line = raw_line.decode(errors="replace").rstrip()
                if line:
                    _ffmpeg_logger.log(level, line)

    def _ensure_pipes(self) -> None:
        self._pipes_dir.mkdir(parents=True, exist_ok=True)
        for pipe_path in (self.video_pipe_path, self.audio_pipe_path):
            if not pipe_path.exists():
                os.mkfifo(pipe_path)

    async def _periodic_healthcheck(self) -> None:
        interval = self._settings.ffmpeg_healthcheck_interval_seconds
        with contextlib.suppress(asyncio.CancelledError):
            while True:
                await asyncio.sleep(interval)
                _streaming_logger.debug("Healthcheck FFmpeg: {status}", status=self.healthcheck())
