"""Testes de `app/utils/logging.py`."""

from __future__ import annotations

import time
from collections.abc import Callable

from app.config.settings import Settings
from app.utils import logging as logging_module
from app.utils.logging import get_logger, reset_logging_state, setup_logging


def _wait_for_content(path: object, timeout: float = 2.0) -> str:
    """Loguru grava de forma assíncrona o suficiente para exigir um pequeno poll em disco."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and path.read_text():  # type: ignore[attr-defined]
            return path.read_text()  # type: ignore[attr-defined]
        time.sleep(0.02)
    return path.read_text() if path.exists() else ""  # type: ignore[attr-defined]


def test_setup_logging_creates_all_four_log_files(make_settings: Callable[..., Settings]) -> None:
    reset_logging_state()
    settings = make_settings()
    setup_logging(settings)
    try:
        for name in ("bot.log", "stream.log", "ffmpeg.log", "errors.log"):
            assert (settings.log_dir / name).exists()
    finally:
        reset_logging_state()


def test_setup_logging_is_idempotent(make_settings: Callable[..., Settings]) -> None:
    reset_logging_state()
    settings = make_settings()
    setup_logging(settings)
    setup_logging(settings)  # não deve levantar nem duplicar sinks
    try:
        get_logger("bot").info("mensagem única")
        content = _wait_for_content(settings.log_dir / "bot.log")
        assert content.count("mensagem única") == 1
    finally:
        reset_logging_state()


def test_bot_component_routes_only_to_bot_log(make_settings: Callable[..., Settings]) -> None:
    reset_logging_state()
    settings = make_settings()
    setup_logging(settings)
    try:
        get_logger("bot").info("marcador-bot-log-xyz")
        bot_content = _wait_for_content(settings.log_dir / "bot.log")
        stream_content = (settings.log_dir / "stream.log").read_text()
        assert "marcador-bot-log-xyz" in bot_content
        assert "marcador-bot-log-xyz" not in stream_content
    finally:
        reset_logging_state()


def test_streaming_and_telegram_and_player_route_to_stream_log(
    make_settings: Callable[..., Settings],
) -> None:
    reset_logging_state()
    settings = make_settings()
    setup_logging(settings)
    try:
        get_logger("streaming").info("marcador-streaming")
        get_logger("telegram").info("marcador-telegram")
        get_logger("player").info("marcador-player")
        content = _wait_for_content(settings.log_dir / "stream.log")
        assert "marcador-streaming" in content
        assert "marcador-telegram" in content
        assert "marcador-player" in content
    finally:
        reset_logging_state()


def test_ffmpeg_component_routes_to_ffmpeg_log(make_settings: Callable[..., Settings]) -> None:
    reset_logging_state()
    settings = make_settings()
    setup_logging(settings)
    try:
        get_logger("ffmpeg").debug("marcador-ffmpeg-linha")
        content = _wait_for_content(settings.log_dir / "ffmpeg.log")
        assert "marcador-ffmpeg-linha" in content
    finally:
        reset_logging_state()


def test_error_level_is_aggregated_in_errors_log_regardless_of_component(
    make_settings: Callable[..., Settings],
) -> None:
    reset_logging_state()
    settings = make_settings()
    setup_logging(settings)
    try:
        get_logger("bot").error("marcador-erro-bot")
        get_logger("streaming").error("marcador-erro-streaming")
        content = _wait_for_content(settings.log_dir / "errors.log")
        assert "marcador-erro-bot" in content
        assert "marcador-erro-streaming" in content
    finally:
        reset_logging_state()


def test_secrets_are_redacted_from_log_output(make_settings: Callable[..., Settings]) -> None:
    reset_logging_state()
    settings = make_settings(
        api_hash="leak-me-api-hash-12345", session_string="leak-me-session-string-67890"
    )
    setup_logging(settings)
    try:
        get_logger("bot").info(
            "credenciais: {hash} / {session}",
            hash=settings.api_hash.get_secret_value(),
            session=settings.session_string.get_secret_value(),
        )
        content = _wait_for_content(settings.log_dir / "bot.log")
        assert "leak-me-api-hash-12345" not in content
        assert "leak-me-session-string-67890" not in content
        assert "***MASKED***" in content
    finally:
        reset_logging_state()


def test_reset_logging_state_allows_reconfiguration_with_new_dir(
    make_settings: Callable[..., Settings],
) -> None:
    reset_logging_state()
    first_settings = make_settings()
    setup_logging(first_settings)
    assert logging_module._configured is True  # noqa: SLF001
    reset_logging_state()
    assert logging_module._configured is False  # noqa: SLF001
    reset_logging_state()
