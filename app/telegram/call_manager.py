"""Gerenciamento de chamada normal e transmissão RTMP do Telegram."""

from __future__ import annotations

import asyncio
import contextlib
import secrets
import shlex
from collections.abc import Awaitable, Callable
from pathlib import Path

from ntgcalls import MediaSource
from pyrogram import Client
from pyrogram.errors import RPCError
from pyrogram.raw.functions.channels import GetFullChannel
from pyrogram.raw.functions.phone import (
    CreateGroupCall,
    DiscardGroupCall,
    GetGroupCallStreamRtmpUrl,
)
from pyrogram.raw.types import InputChannel, InputPeerChannel
from pyrogram.raw.types.phone import GroupCallStreamRtmpUrl
from pytgcalls import PyTgCalls
from pytgcalls import filters as pytgcalls_filters
from pytgcalls.exceptions import NoActiveGroupCall, NotInCallError
from pytgcalls.types import ChatUpdate, StreamEnded, Update
from pytgcalls.types.raw import AudioParameters, AudioStream, Stream, VideoParameters, VideoStream

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
_AUDIO_PARAMETERS = AudioParameters(bitrate=AUDIO_SAMPLE_RATE, channels=AUDIO_CHANNELS)
_VIDEO_PARAMETERS = VideoParameters(
    width=VIDEO_WIDTH, height=VIDEO_HEIGHT, frame_rate=VIDEO_FPS, adjust_by_height=False
)


class TelegramCallManager:
    """Prioriza RTMP e mantém PyTgCalls como fallback automático."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._chat_id = settings.stream_chat_id or settings.chat_id
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
        self._rtmp_active = False
        self._started = False
        self._on_permanent_failure: Callable[[], Awaitable[None]] | None = None
        self._register_handlers()

    def set_permanent_failure_callback(self, callback: Callable[[], Awaitable[None]]) -> None:
        self._on_permanent_failure = callback

    def _register_handlers(self) -> None:
        @self._call_py.on_update(pytgcalls_filters.chat_update(ChatUpdate.Status.LEFT_CALL))  # type: ignore[untyped-decorator]
        async def _handle_left(_: PyTgCalls, update: Update) -> None:
            await self._on_disconnected(update)

        @self._call_py.on_update(pytgcalls_filters.stream_end())  # type: ignore[untyped-decorator]
        async def _handle_stream_end(_: PyTgCalls, update: Update) -> None:
            stream_type = update.stream_type if isinstance(update, StreamEnded) else None
            _logger.warning("Stream do PyTgCalls encerrado ({type}).", type=stream_type)
            await self._on_disconnected(update)

    async def start(self) -> None:
        if self._started:
            return
        await self._call_py.start()
        self._started = True
        _logger.info("Cliente Telegram iniciado (RTMP com fallback PyTgCalls).")

    async def stop(self) -> None:
        if not self._started:
            return
        await self.leave_call()
        await self._client.stop()
        self._started = False

    async def prepare_rtmp(self) -> str:
        """Obtém o ingest RTMP; qualquer falha é tratada pelo serviço como fallback."""
        if self._last_pipes is not None:
            await self.leave_call()
        self._state = CallState.CONNECTING
        peer = await self._client.resolve_peer(self._chat_id)
        if peer is None:
            raise RuntimeError("O chat de transmissão não pôde ser resolvido.")
        try:
            await self._client.invoke(
                CreateGroupCall(
                    peer=peer, random_id=secrets.randbits(31), rtmp_stream=True, title="Telerion"
                )
            )
        except RPCError as exc:
            if "GROUPCALL_ALREADY_STARTED" not in str(exc):
                raise
        endpoint = await self._client.invoke(GetGroupCallStreamRtmpUrl(peer=peer, revoke=False))
        if not isinstance(endpoint, GroupCallStreamRtmpUrl):
            raise RuntimeError("O Telegram não retornou um endpoint RTMP válido.")
        self._state = CallState.CONNECTED
        self._rtmp_active = True
        self._last_error = None
        _logger.info("Endpoint RTMP preparado no chat {chat_id}.", chat_id=self._chat_id)
        return f"{endpoint.url.rstrip('/')}/{endpoint.key.lstrip('/')}"

    async def join_call(self, video_pipe: Path, audio_pipe: Path) -> None:
        self._rtmp_active = False
        self._state = CallState.CONNECTING
        try:
            await self._play(video_pipe, audio_pipe)
        except Exception as exc:
            self._state = CallState.FAILED
            self._last_error = str(exc)
            raise
        self._state = CallState.CONNECTED
        self._last_error = None
        _logger.info("Fallback PyTgCalls iniciado no chat {chat_id}.", chat_id=self._chat_id)

    async def send_media(self, video_pipe: Path, audio_pipe: Path) -> None:
        if self._state is not CallState.CONNECTED:
            await self.join_call(video_pipe, audio_pipe)
            return
        await self._play(video_pipe, audio_pipe)

    async def _play(self, video_pipe: Path, audio_pipe: Path) -> None:
        async with self._lock:
            stream = Stream(
                microphone=AudioStream(
                    MediaSource.SHELL, shlex.join(["cat", "--", str(audio_pipe)]), _AUDIO_PARAMETERS
                ),
                camera=VideoStream(
                    MediaSource.SHELL, shlex.join(["cat", "--", str(video_pipe)]), _VIDEO_PARAMETERS
                ),
            )
            await self._call_py.play(self._chat_id, stream)
            self._last_pipes = (video_pipe, audio_pipe)

    async def pause_call(self) -> None:
        with contextlib.suppress(NotInCallError):
            await self._call_py.pause(self._chat_id)

    async def resume_call(self) -> None:
        with contextlib.suppress(NotInCallError):
            await self._call_py.resume(self._chat_id)

    async def change_volume(self, volume: int) -> None:
        with contextlib.suppress(NotInCallError, NoActiveGroupCall):
            await self._call_py.change_volume_call(self._chat_id, volume)

    async def leave_call(self) -> None:
        async with self._lock:
            self._last_pipes = None
            self._rtmp_active = False
            with contextlib.suppress(NotInCallError, NoActiveGroupCall):
                await self._call_py.leave_call(self._chat_id)
            self._state = CallState.DISCONNECTED

    async def end_call(self) -> None:
        """Sai do transporte e encerra a live RTMP ativa no Telegram."""
        rtmp_active = self._rtmp_active
        try:
            if rtmp_active:
                peer = await self._client.resolve_peer(self._chat_id)
                if isinstance(peer, InputPeerChannel):
                    full = await self._client.invoke(
                        GetFullChannel(
                            channel=InputChannel(
                                channel_id=peer.channel_id,
                                access_hash=peer.access_hash,
                            )
                        )
                    )
                    call = getattr(getattr(full, "full_chat", None), "call", None)
                    if call is not None:
                        await self._client.invoke(DiscardGroupCall(call=call))
        finally:
            await self.leave_call()

    async def _on_disconnected(self, _update: Update) -> None:
        if self._state == CallState.RECONNECTING or self._last_pipes is None:
            return
        self._state = CallState.RECONNECTING
        await self.reconnect()

    async def reconnect(self) -> None:
        if self._last_pipes is None:
            self._state = CallState.FAILED
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
        except RetryExhaustedError as exc:
            self._state = CallState.FAILED
            self._last_error = str(exc)
            if self._on_permanent_failure is not None:
                await self._on_permanent_failure()
            raise CallPermanentFailureError(str(exc)) from exc

    @property
    def client(self) -> Client:
        return self._client

    @property
    def rtmp_active(self) -> bool:
        return self._rtmp_active

    def healthcheck(self) -> CallHealth:
        return CallHealth(self._state, self._chat_id, self._reconnect_count, self._last_error)
