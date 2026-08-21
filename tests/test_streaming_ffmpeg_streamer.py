"""Testes de `app/streaming/ffmpeg_streamer.py`.

Usa um "FFmpeg" falso (script shell) em vez do binário real: o objetivo aqui é
exercitar o gerenciamento do processo (início, parada, reinício, detecção de
falha/conclusão) via `asyncio.create_subprocess_exec` de verdade, não testar o
FFmpeg em si. O script nunca escreve nos pipes nomeados — não há leitor nos
testes, e escrever bloquearia para sempre (semântica de FIFO).
"""

from __future__ import annotations

import asyncio
import os
import stat
from collections.abc import Callable
from pathlib import Path

import pytest

from app.config.settings import Settings
from app.streaming.exceptions import FFmpegStreamerError
from app.streaming.ffmpeg_streamer import FFmpegStreamer
from app.streaming.models import FFmpegProcessState
from app.utils.media_contract import AUDIO_SAMPLE_RATE, VIDEO_FPS, VIDEO_HEIGHT, VIDEO_WIDTH
from app.utils.sanitize import MediaSource, SourceType

_LOCAL_SOURCE = MediaSource(raw="/media/video.mp4", type=SourceType.LOCAL_FILE)
_HTTP_SOURCE = MediaSource(raw="http://example.com/video.mp4", type=SourceType.HTTP)
_HLS_SOURCE = MediaSource(raw="https://example.com/stream.m3u8", type=SourceType.HLS)
_RTMP_SOURCE = MediaSource(raw="rtmp://example.com/live", type=SourceType.RTMP)
_RTSP_SOURCE = MediaSource(raw="rtsp://example.com/stream", type=SourceType.RTSP)


@pytest.fixture(autouse=True)
def require_posix_for_process_tests(request: pytest.FixtureRequest) -> None:
    """The process tests use POSIX FIFOs and executable shell scripts."""
    if os.name == "nt" and "make_fake_ffmpeg" in request.fixturenames:
        pytest.skip("FFmpeg process tests require Linux/POSIX")


async def _wait_until(predicate: Callable[[], bool], timeout_seconds: float = 2.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout_seconds
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condição não satisfeita a tempo")


# --- build_command (função pura) ---


def test_build_command_local_file_uses_re_flag(make_settings: Callable[..., Settings]) -> None:
    streamer = FFmpegStreamer(make_settings())
    command = streamer.build_command(_LOCAL_SOURCE)
    assert command[0] == "ffmpeg"
    assert "-re" in command
    assert "-i" in command
    assert command[command.index("-i") + 1] == _LOCAL_SOURCE.raw
    assert f"{VIDEO_WIDTH}x{VIDEO_HEIGHT}" in command
    assert str(VIDEO_FPS) in command
    assert str(AUDIO_SAMPLE_RATE) in command
    assert str(streamer.video_pipe_path) in command
    assert str(streamer.audio_pipe_path) in command


def test_build_command_http_includes_reconnect_flags(
    make_settings: Callable[..., Settings],
) -> None:
    streamer = FFmpegStreamer(make_settings())
    command = streamer.build_command(_HTTP_SOURCE)
    assert "-reconnect" in command
    assert "-re" in command


def test_build_command_hls_includes_reconnect_but_not_re(
    make_settings: Callable[..., Settings],
) -> None:
    streamer = FFmpegStreamer(make_settings())
    command = streamer.build_command(_HLS_SOURCE)
    assert "-reconnect" in command
    assert "-re" not in command


def test_build_command_rtsp_uses_tcp_transport(make_settings: Callable[..., Settings]) -> None:
    streamer = FFmpegStreamer(make_settings())
    command = streamer.build_command(_RTSP_SOURCE)
    assert "-rtsp_transport" in command
    assert command[command.index("-rtsp_transport") + 1] == "tcp"


def test_build_command_rtmp_has_no_extra_input_flags(
    make_settings: Callable[..., Settings],
) -> None:
    streamer = FFmpegStreamer(make_settings())
    command = streamer.build_command(_RTMP_SOURCE)
    idx_i = command.index("-i")
    # Cabeçalho fixo: [ffmpeg, -y, -loglevel, warning, -hide_banner] (5 itens, índices 0-4).
    # Nenhuma flag extra deve aparecer entre o cabeçalho e -i para RTMP.
    assert command[5:idx_i] == []


def test_build_command_never_uses_shell_string() -> None:
    # build_command sempre retorna list[str]; a chamada real usa create_subprocess_exec,
    # nunca create_subprocess_shell — ver ffmpeg_streamer.py.
    import app.streaming.ffmpeg_streamer as module

    assert "create_subprocess_shell" not in module.__dict__


def test_build_command_rtmp_output_uses_h264_aac_flv(
    make_settings: Callable[..., Settings],
) -> None:
    streamer = FFmpegStreamer(make_settings())
    streamer._output_url = "rtmps://telegram/s/secret"  # noqa: SLF001

    command = streamer.build_command(_LOCAL_SOURCE)

    assert command[command.index("-c:v") : command.index("-c:v") + 2] == ["-c:v", "libx264"]
    assert "aac" in command
    assert command[-2:] == ["flv", "rtmps://telegram/s/secret"]
    assert str(streamer.video_pipe_path) not in command


def test_build_command_rtmp_applies_configured_volume(
    make_settings: Callable[..., Settings],
) -> None:
    streamer = FFmpegStreamer(make_settings())
    streamer._output_url = "rtmps://telegram/s/secret"  # noqa: SLF001
    streamer._volume_percent = 150  # noqa: SLF001

    command = streamer.build_command(_LOCAL_SOURCE)

    assert command[command.index("-af") + 1] == "volume=1.50"


def test_build_command_burns_subtitle_with_delay(
    make_settings: Callable[..., Settings],
) -> None:
    streamer = FFmpegStreamer(make_settings())
    streamer._output_url = "rtmps://telegram/s/secret"  # noqa: SLF001
    streamer._subtitle_path = "/app/media/subtitles/movie-pt.srt"  # noqa: SLF001
    streamer._subtitle_delay_ms = 500  # noqa: SLF001

    command = streamer.build_command(_LOCAL_SOURCE)
    video_filter = command[command.index("-vf") + 1]

    assert "subtitles='/app/media/subtitles/movie-pt.srt'" in video_filter
    assert "setpts=PTS-0.5/TB" in video_filter


# --- ciclo de vida do processo ---


async def test_start_creates_named_pipes(
    make_settings: Callable[..., Settings], make_fake_ffmpeg: Callable[[str], Path]
) -> None:
    fake_ffmpeg = make_fake_ffmpeg("trap 'exit 0' TERM; while true; do sleep 0.05; done")
    settings = make_settings(ffmpeg_path=str(fake_ffmpeg))
    streamer = FFmpegStreamer(settings)

    await streamer.start(_LOCAL_SOURCE)
    try:
        assert stat.S_ISFIFO(streamer.video_pipe_path.stat().st_mode)
        assert stat.S_ISFIFO(streamer.audio_pipe_path.stat().st_mode)
        health = streamer.healthcheck()
        assert health.state is FFmpegProcessState.RUNNING
        assert health.pid is not None
        assert health.current_source == _LOCAL_SOURCE.raw
    finally:
        await streamer.stop()


async def test_start_twice_raises(
    make_settings: Callable[..., Settings], make_fake_ffmpeg: Callable[[str], Path]
) -> None:
    fake_ffmpeg = make_fake_ffmpeg("trap 'exit 0' TERM; while true; do sleep 0.05; done")
    settings = make_settings(ffmpeg_path=str(fake_ffmpeg))
    streamer = FFmpegStreamer(settings)

    await streamer.start(_LOCAL_SOURCE)
    try:
        with pytest.raises(FFmpegStreamerError, match="já está em execução"):
            await streamer.start(_LOCAL_SOURCE)
    finally:
        await streamer.stop()


async def test_stop_terminates_process_gracefully(
    make_settings: Callable[..., Settings], make_fake_ffmpeg: Callable[[str], Path]
) -> None:
    fake_ffmpeg = make_fake_ffmpeg("trap 'exit 0' TERM; while true; do sleep 0.05; done")
    settings = make_settings(ffmpeg_path=str(fake_ffmpeg))
    streamer = FFmpegStreamer(settings)

    await streamer.start(_LOCAL_SOURCE)
    await streamer.stop()

    health = streamer.healthcheck()
    assert health.state is FFmpegProcessState.STOPPED
    assert health.pid is None


async def test_restart_without_prior_start_raises(make_settings: Callable[..., Settings]) -> None:
    streamer = FFmpegStreamer(make_settings())
    with pytest.raises(FFmpegStreamerError, match="Nenhuma fonte ativa"):
        await streamer.restart()


async def test_restart_keeps_source_and_increments_restart_count(
    make_settings: Callable[..., Settings], make_fake_ffmpeg: Callable[[str], Path]
) -> None:
    fake_ffmpeg = make_fake_ffmpeg("trap 'exit 0' TERM; while true; do sleep 0.05; done")
    settings = make_settings(ffmpeg_path=str(fake_ffmpeg))
    streamer = FFmpegStreamer(settings)

    await streamer.start(_LOCAL_SOURCE)
    try:
        await streamer.restart()
        health = streamer.healthcheck()
        assert health.state is FFmpegProcessState.RUNNING
        assert health.current_source == _LOCAL_SOURCE.raw
        assert health.restart_count == 1
    finally:
        await streamer.stop()


async def test_change_source_updates_current_source(
    make_settings: Callable[..., Settings], make_fake_ffmpeg: Callable[[str], Path]
) -> None:
    fake_ffmpeg = make_fake_ffmpeg("trap 'exit 0' TERM; while true; do sleep 0.05; done")
    settings = make_settings(ffmpeg_path=str(fake_ffmpeg))
    streamer = FFmpegStreamer(settings)

    await streamer.start(_LOCAL_SOURCE)
    try:
        await streamer.change_source(_HTTP_SOURCE)
        health = streamer.healthcheck()
        assert health.current_source == _HTTP_SOURCE.raw
    finally:
        await streamer.stop()


async def test_change_source_same_source_does_not_release_it(
    make_settings: Callable[..., Settings], make_fake_ffmpeg: Callable[[str], Path]
) -> None:
    fake_ffmpeg = make_fake_ffmpeg("trap 'exit 0' TERM; while true; do sleep 0.05; done")
    streamer = FFmpegStreamer(make_settings(ffmpeg_path=str(fake_ffmpeg)))
    released: list[MediaSource] = []

    async def on_release(source: MediaSource) -> None:
        released.append(source)

    streamer.set_source_released_callback(on_release)
    await streamer.start(_LOCAL_SOURCE)
    try:
        await streamer.change_source(_LOCAL_SOURCE)
        assert released == []
    finally:
        await streamer.stop()


async def test_change_source_works_even_without_prior_start(
    make_settings: Callable[..., Settings], make_fake_ffmpeg: Callable[[str], Path]
) -> None:
    fake_ffmpeg = make_fake_ffmpeg("trap 'exit 0' TERM; while true; do sleep 0.05; done")
    settings = make_settings(ffmpeg_path=str(fake_ffmpeg))
    streamer = FFmpegStreamer(settings)

    await streamer.change_source(_LOCAL_SOURCE)
    try:
        assert streamer.healthcheck().state is FFmpegProcessState.RUNNING
    finally:
        await streamer.stop()


async def test_missing_ffmpeg_binary_raises(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings(ffmpeg_path="/nao/existe/ffmpeg-binario-fake")
    streamer = FFmpegStreamer(settings)
    with pytest.raises(FFmpegStreamerError, match="não encontrado"):
        await streamer.start(_LOCAL_SOURCE)


# --- detecção de falha / conclusão ---


async def test_clean_exit_triggers_completion_callback(
    make_settings: Callable[..., Settings], make_fake_ffmpeg: Callable[[str], Path]
) -> None:
    fake_ffmpeg = make_fake_ffmpeg("exit 0")
    settings = make_settings(ffmpeg_path=str(fake_ffmpeg))
    streamer = FFmpegStreamer(settings)
    completed = asyncio.Event()
    streamer.set_completion_callback(_make_setter(completed))

    await streamer.start(_LOCAL_SOURCE)
    await asyncio.wait_for(completed.wait(), timeout=2.0)
    assert streamer.healthcheck().state is FFmpegProcessState.IDLE


async def test_completion_callback_can_stop_streamer_without_self_cancellation(
    make_settings: Callable[..., Settings], make_fake_ffmpeg: Callable[[str], Path]
) -> None:
    streamer = FFmpegStreamer(make_settings(ffmpeg_path=str(make_fake_ffmpeg("exit 0"))))
    completed = asyncio.Event()

    async def stop_after_completion() -> None:
        await streamer.stop()
        completed.set()

    streamer.set_completion_callback(stop_after_completion)
    await streamer.start(_LOCAL_SOURCE)

    await asyncio.wait_for(completed.wait(), timeout=2.0)
    assert streamer.healthcheck().state is FFmpegProcessState.STOPPED


async def test_crash_triggers_auto_restart_and_recovers(
    make_settings: Callable[..., Settings], make_fake_ffmpeg: Callable[[str], Path], tmp_path: Path
) -> None:
    # Falha na primeira execução, depois roda de forma saudável (simula uma
    # reconexão transitória que se recupera sozinha).
    marker = tmp_path / "attempts"
    fake_ffmpeg = make_fake_ffmpeg(f"""
        count_file="{marker}"
        count=0
        if [ -f "$count_file" ]; then count=$(cat "$count_file"); fi
        count=$((count + 1))
        echo "$count" > "$count_file"
        if [ "$count" -eq 1 ]; then
            exit 1
        fi
        trap 'exit 0' TERM
        while true; do sleep 0.05; done
        """)
    settings = make_settings(ffmpeg_path=str(fake_ffmpeg))
    streamer = FFmpegStreamer(settings)

    await streamer.start(_LOCAL_SOURCE)
    try:
        # Espera especificamente pelo reinício (não só por "RUNNING", que já é
        # verdade logo no start() inicial, antes da falha simulada acontecer).
        await _wait_until(lambda: streamer.healthcheck().restart_count >= 1)
        await _wait_until(lambda: streamer.healthcheck().state is FFmpegProcessState.RUNNING)
        health = streamer.healthcheck()
        assert health.restart_count >= 1
    finally:
        await streamer.stop()


async def test_permanent_failure_after_exhausting_retries(
    make_settings: Callable[..., Settings], make_fake_ffmpeg: Callable[[str], Path]
) -> None:
    fake_ffmpeg = make_fake_ffmpeg("exit 1")
    settings = make_settings(
        ffmpeg_path=str(fake_ffmpeg),
        retry_base_delay_seconds=0.001,
        retry_max_delay_seconds=0.001,
        retry_max_attempts=2,
        retry_jitter_seconds=0.0,
    )
    streamer = FFmpegStreamer(settings)
    permanent_failure = asyncio.Event()
    streamer.set_permanent_failure_callback(_make_setter(permanent_failure))

    await streamer.start(_LOCAL_SOURCE)
    await asyncio.wait_for(permanent_failure.wait(), timeout=2.0)
    await _wait_until(lambda: streamer.healthcheck().state is FFmpegProcessState.FAILED)
    health = streamer.healthcheck()
    assert health.last_error is not None


async def test_permanent_failure_only_spawns_one_supervisor_no_task_explosion(
    make_settings: Callable[..., Settings], make_fake_ffmpeg: Callable[[str], Path]
) -> None:
    """Regressão: cada tentativa de reinício não pode criar uma nova task de supervisão.

    Antes da correção, um FFmpeg que falha repetidamente em sequência rápida
    disparava uma task de supervisão nova a cada tentativa, multiplicando as
    reinicializações concorrentes. `restart_count` deve refletir exatamente
    `retry_max_attempts` tentativas — nem mais, nem menos.
    """
    fake_ffmpeg = make_fake_ffmpeg("exit 1")
    settings = make_settings(
        ffmpeg_path=str(fake_ffmpeg),
        retry_base_delay_seconds=0.001,
        retry_max_delay_seconds=0.001,
        retry_max_attempts=3,
        retry_jitter_seconds=0.0,
    )
    streamer = FFmpegStreamer(settings)
    permanent_failure = asyncio.Event()
    streamer.set_permanent_failure_callback(_make_setter(permanent_failure))

    await streamer.start(_LOCAL_SOURCE)
    await asyncio.wait_for(permanent_failure.wait(), timeout=2.0)
    await _wait_until(lambda: streamer.healthcheck().state is FFmpegProcessState.FAILED)

    assert streamer.healthcheck().restart_count == 3


def _make_setter(event: asyncio.Event) -> Callable[[], asyncio.Future[None]]:
    async def _setter() -> None:
        event.set()

    return _setter


async def test_stop_kills_process_if_it_ignores_sigterm(
    make_settings: Callable[..., Settings], make_fake_ffmpeg: Callable[[str], Path]
) -> None:
    fake_ffmpeg = make_fake_ffmpeg("trap '' TERM; while true; do sleep 0.05; done")
    settings = make_settings(ffmpeg_path=str(fake_ffmpeg), ffmpeg_terminate_timeout_seconds=0.1)
    streamer = FFmpegStreamer(settings)

    await streamer.start(_LOCAL_SOURCE)
    await streamer.stop()  # deve recorrer a SIGKILL após o timeout, não travar o teste

    assert streamer.healthcheck().state is FFmpegProcessState.STOPPED
    assert streamer.healthcheck().pid is None


async def test_launch_oserror_during_retry_triggers_permanent_failure(
    make_settings: Callable[..., Settings], make_fake_ffmpeg: Callable[[str], Path]
) -> None:
    # Primeira execução falha e remove a própria permissão de execução antes de
    # sair — a tentativa de reinício automático não consegue nem spawnar o
    # processo (PermissionError/OSError), o que deve ser tratado como falha
    # permanente imediata, sem entrar em loop.
    fake_ffmpeg = make_fake_ffmpeg('chmod 000 "$0"; exit 1')
    settings = make_settings(
        ffmpeg_path=str(fake_ffmpeg),
        retry_base_delay_seconds=0.001,
        retry_max_delay_seconds=0.001,
        retry_max_attempts=5,
        retry_jitter_seconds=0.0,
    )
    streamer = FFmpegStreamer(settings)
    permanent_failure = asyncio.Event()
    streamer.set_permanent_failure_callback(_make_setter(permanent_failure))

    await streamer.start(_LOCAL_SOURCE)
    await asyncio.wait_for(permanent_failure.wait(), timeout=2.0)

    assert streamer.healthcheck().state is FFmpegProcessState.FAILED
    assert streamer.healthcheck().last_error is not None


async def test_pump_stream_logs_ffmpeg_output(
    make_settings: Callable[..., Settings], make_fake_ffmpeg: Callable[[str], Path]
) -> None:
    from app.utils.logging import reset_logging_state, setup_logging

    fake_ffmpeg = make_fake_ffmpeg(
        "echo 'linha-stdout-marcador'; echo 'linha-stderr-marcador' >&2; "
        "trap 'exit 0' TERM; while true; do sleep 0.05; done"
    )
    settings = make_settings(ffmpeg_path=str(fake_ffmpeg))
    reset_logging_state()
    setup_logging(settings)
    try:
        streamer = FFmpegStreamer(settings)
        await streamer.start(_LOCAL_SOURCE)
        try:
            await _wait_until(
                lambda: (settings.log_dir / "ffmpeg.log").exists()
                and "linha-stderr-marcador" in (settings.log_dir / "ffmpeg.log").read_text()
            )
        finally:
            await streamer.stop()
        content = (settings.log_dir / "ffmpeg.log").read_text()
        assert "linha-stdout-marcador" in content
        assert "linha-stderr-marcador" in content
    finally:
        reset_logging_state()
