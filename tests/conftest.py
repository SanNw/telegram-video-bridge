"""Fixtures compartilhadas entre os testes."""

from __future__ import annotations

import stat
from collections.abc import Callable
from pathlib import Path

import pytest

from app.config.settings import Settings


@pytest.fixture
def make_settings(tmp_path: Path) -> Callable[..., Settings]:
    """Fábrica de `Settings` de teste, isolada em `tmp_path`. Retries rápidos por padrão."""

    def _make(**overrides: object) -> Settings:
        media_path = tmp_path / "media"
        media_path.mkdir(parents=True, exist_ok=True)
        defaults: dict[str, object] = {
            "api_id": 12345,
            "api_hash": "test-api-hash-secret",
            "session_string": "test-session-string-secret",
            "chat_id": -1001234567890,
            "authorized_user_ids": [111, 222],
            "log_dir": tmp_path / "logs",
            "media_path": media_path,
            "queue_data_path": tmp_path / "data" / "queue.json",
            "ffmpeg_path": "ffmpeg",
            "ffmpeg_terminate_timeout_seconds": 0.2,
            "queue_max_items": 5,
            "retry_base_delay_seconds": 0.01,
            "retry_max_delay_seconds": 0.02,
            "retry_max_attempts": 3,
            "retry_jitter_seconds": 0.0,
            "ffmpeg_healthcheck_interval_seconds": 3600.0,
        }
        defaults.update(overrides)
        return Settings(**defaults)  # type: ignore[arg-type]

    return _make


@pytest.fixture
def settings(make_settings: Callable[..., Settings]) -> Settings:
    return make_settings()


@pytest.fixture
def make_fake_ffmpeg(tmp_path: Path) -> Callable[[str], Path]:
    """Fábrica de um binário FFmpeg falso: um script shell com o corpo dado.

    Os testes de `FFmpegStreamer` não usam FFmpeg de verdade — só precisam de um
    processo real (para exercitar `asyncio.create_subprocess_exec`, sinais,
    códigos de saída) que ignore os argumentos fabricados e nunca tente escrever
    nos pipes nomeados (que não têm leitor nos testes e bloqueariam para sempre).
    """
    counter = {"n": 0}

    def _make(script_body: str) -> Path:
        counter["n"] += 1
        path = tmp_path / f"fake_ffmpeg_{counter['n']}.sh"
        path.write_text(f"#!/bin/sh\n{script_body}\n")
        path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        return path

    return _make
