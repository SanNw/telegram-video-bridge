"""Gerenciamento da chamada de vídeo/grupo do Telegram via Pyrogram + PyTgCalls.

`TelegramCallManager` entra/sai da chamada, envia mídia e reconecta automaticamente
em caso de desconexão (kick, saída forçada, fechamento da chamada, fim de stream).

Consome os pipes nomeados produzidos por `FFmpegStreamer` através de `InputDevice`
do py-tgcalls — o mecanismo público da lib para alimentar a chamada com uma fonte
raw já decodificada, em vez de deixar o py-tgcalls invocar seu próprio FFmpeg
interno. Isso é o que mantém `streaming/` e `telegram/` desacopladas: esta classe
não sabe nada sobre a origem do vídeo, só lê dos pipes. As dimensões/taxas abaixo
(`_AUDIO_QUALITY`, `_VIDEO_QUALITY`) precisam bater exatamente com o formato que
`FFmpegStreamer.build_command` escreve nos pipes.

Política de retry (reconexão de chamada): backoff exponencial com jitter,
parâmetros vindos de `Settings` (mesma política usada por `FFmpegStreamer` para
reconexão do processo FFmpeg — ver `app/utils/retry.py`). Após `retry_max_attempts`
tentativas, a reconexão é marcada como falha permanente, logada em `errors.log` e
o callback de alerta (`set_permanent_failure_callback`) é acionado para notificar
o operador via `services/`.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from pathlib import Path

from pyrogram import Client
from pytgcalls import PyTgCalls
from pytgcalls import filters as pytgcalls_filters
from pytgcalls.exceptions import NoActiveGroupCall, NotInCallError
from pytgcalls.media_devices import InputDevice
from pytgcalls.types import ChatUpdate, MediaStream, StreamEnded, Update
from pytgcalls.types.raw import AudioParameters, VideoParameters

from app.config.settings import Settings
from app.telegram.exceptions import CallPermanentFailureError
from app.telegram.models import CallHealth, CallState
from app.utils.logging import get_logger
from app.utils.media_contract import (
    AUDIO_CHANNELS,
    AUDIO_SAMPLE_RATE,
    VIDEO_FPS,
    VIDEO_HEIGHT,
    VIDEO_WIDTH,
)
from app.utils.retry import RetryExhaustedError, RetryPolicy, retry_with_backoff

_logger = get_logger("telegram")

# Construídos a partir de `utils/media_contract.py` — a mesma fonte de verdade
# que `FFmpegStreamer.build_command` usa para os pipes, garantindo que os dois
# lados nunca fiquem fora de sincronia.
_AUDIO_PARAMETERS = AudioParameters(bitrate=AUDIO_SAMPLE_RATE, channels=AUDIO_CHANNELS)
_VIDEO_PARAMETERS = VideoParameters(
    width=VIDEO_WIDTH, height=VIDEO_HEIGHT, frame_rate=VIDEO_FPS, adjust_by_height=False
)


class TelegramCallManager:
    """Entra, sai, envia mídia e reconecta automaticamente a chamada de vídeo do Telegram."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._chat_id = settings.chat_id
        self._client = Client(
            name="telegram-video-bridge",
            api_id=settings.api_id,
            api_hash=settings.api_hash.get_secret_value(),
            session_string=settings.session_string.get_secret_value(),
            in_memory=True,
        )
        self._call_py = PyTgCalls(self._client)
        self._lock = asyncio.Lock()
        self._state = CallState.DISCONNECTED
        self._reconnect_count = 0
        self._last_error: str | None = None
        self._last_pipes: tuple[Path, Path] | None = None
        self._started = False
        self._on_permanent_failure: Callable[[], Awaitable[None]] | None = None
        self._register_handlers()

    def set_permanent_failure_callback(self, callback: Callable[[], Awaitable[None]]) -> None:
        """Registra callback chamado quando a reconexão esgota as tentativas (alerta)."""
        self._on_permanent_failure = callback

    def _register_handlers(self) -> None:
        # py-tgcalls não publica type stubs; o decorator resolve como Any em modo estrito.
        @self._call_py.on_update(pytgcalls_filters.chat_update(ChatUpdate.Status.LEFT_CALL))  # type: ignore[untyped-decorator]
        async def _handle_left(_: PyTgCalls, update: Update) -> None:
            await self._on_disconnected(update)

        @self._call_py.on_update(pytgcalls_filters.stream_end())  # type: ignore[untyped-decorator]
        async def _handle_stream_end(_: PyTgCalls, update: Update) -> None:
            stream_type = update.stream_type if isinstance(update, StreamEnded) else None
            _logger.warning("Stream do PyTgCalls encerrado ({type}).", type=stream_type)
            await self._on_disconnected(update)

    async def start(self) -> None:
        """Conecta o cliente Pyrogram e inicializa o binding do PyTgCalls."""
        if self._started:
            return
        await self._call_py.start()
        self._started = True
        _logger.info("Cliente Pyrogram + PyTgCalls iniciado.")

    async def stop(self) -> None:
        """Sai da chamada (se ativa) e desconecta o cliente Pyrogram."""
        if not self._started:
            return
        await self.leave_call()
        await self._client.stop()
        self._started = False
        _logger.info("Cliente Pyrogram + PyTgCalls parado.")

    async def join_call(self, video_pipe: Path, audio_pipe: Path) -> None:
        """Entra na chamada configurada em `CHAT_ID`, lendo mídia de `video_pipe`/`audio_pipe`."""
        self._state = CallState.CONNECTING
        await self._play(video_pipe, audio_pipe)
        self._state = CallState.CONNECTED
        _logger.info("Chamada iniciada em chat {chat_id}.", chat_id=self._chat_id)

    async def send_media(self, video_pipe: Path, audio_pipe: Path) -> None:
        """Troca a fonte de uma chamada já em andamento (ex.: após `change_source` no streamer)."""
        await self._play(video_pipe, audio_pipe)
        _logger.info("Fonte da chamada atualizada.")

    async def _play(self, video_pipe: Path, audio_pipe: Path) -> None:
        async with self._lock:
            stream = MediaStream(
                media_path=InputDevice(
                    "telegram-video-bridge-video", str(video_pipe), is_video=True
                ),
                audio_path=InputDevice(
                    "telegram-video-bridge-audio", str(audio_pipe), is_video=False
                ),
                audio_parameters=_AUDIO_PARAMETERS,
                video_parameters=_VIDEO_PARAMETERS,
            )
            await self._call_py.play(self._chat_id, stream)
            self._last_pipes = (video_pipe, audio_pipe)

    async def pause_call(self) -> None:
        """Pausa a chamada (mantém a conexão; o FFmpeg segue rodando, bloqueado no pipe)."""
        with contextlib.suppress(NotInCallError):
            await self._call_py.pause(self._chat_id)

    async def resume_call(self) -> None:
        """Retoma uma chamada pausada."""
        with contextlib.suppress(NotInCallError):
            await self._call_py.resume(self._chat_id)

    async def change_volume(self, volume: int) -> None:
        """Ajusta o volume da chamada (0-200)."""
        with contextlib.suppress(NotInCallError, NoActiveGroupCall):
            await self._call_py.change_volume_call(self._chat_id, volume)

    async def leave_call(self) -> None:
        """Sai da chamada. Idempotente: não levanta erro se já não estiver em uma chamada."""
        async with self._lock:
            with contextlib.suppress(NotInCallError, NoActiveGroupCall):
                await self._call_py.leave_call(self._chat_id)
            self._state = CallState.DISCONNECTED

    async def _on_disconnected(self, _update: Update) -> None:
        if self._state == CallState.RECONNECTING:
            return  # reconexão já em andamento; evita disparos concorrentes
        _logger.warning(
            "Chamada desconectada (chat {chat_id}); iniciando reconexão.", chat_id=self._chat_id
        )
        self._state = CallState.RECONNECTING
        await self.reconnect()

    async def reconnect(self) -> None:
        """Reconecta usando a última fonte conhecida, com backoff exponencial e jitter."""
        if self._last_pipes is None:
            self._state = CallState.FAILED
            _logger.error("Sem fonte anterior para reconectar; chamada permanece desconectada.")
            return

        policy = RetryPolicy(
            base_delay_seconds=self._settings.retry_base_delay_seconds,
            max_delay_seconds=self._settings.retry_max_delay_seconds,
            max_attempts=self._settings.retry_max_attempts,
            jitter_seconds=self._settings.retry_jitter_seconds,
        )
        video_pipe, audio_pipe = self._last_pipes

        async def _try_join() -> None:
            self._reconnect_count += 1
            await self._play(video_pipe, audio_pipe)

        try:
            await retry_with_backoff(_try_join, policy)
            self._state = CallState.CONNECTED
            _logger.info("Reconectado à chamada após falha.")
        except RetryExhaustedError as exc:
            self._state = CallState.FAILED
            self._last_error = str(exc)
            _logger.error(
                "Falha permanente ao reconectar chamada após {n} tentativas: {err}",
                n=policy.max_attempts,
                err=exc,
            )
            if self._on_permanent_failure is not None:
                await self._on_permanent_failure()
            raise CallPermanentFailureError(str(exc)) from exc

    @property
    def client(self) -> Client:
        """Cliente Pyrogram subjacente (mesma sessão que participa da chamada).

        Exposto para que `services/` possa repassá-lo a `bot/`, que registra os
        handlers de comando nele — evita abrir uma segunda sessão MTProto com a
        mesma `SESSION_STRING`, o que derrubaria uma sessão pela outra.
        """
        return self._client

    def healthcheck(self) -> CallHealth:
        """Snapshot síncrono do estado atual da chamada, consultável por `services/`."""
        return CallHealth(
            state=self._state,
            chat_id=self._chat_id,
            reconnect_count=self._reconnect_count,
            last_error=self._last_error,
        )
