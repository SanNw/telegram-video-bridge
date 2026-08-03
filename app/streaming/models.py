"""Modelos de estado da camada de streaming."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class FFmpegProcessState(StrEnum):
    """Estado do processo FFmpeg gerenciado por `FFmpegStreamer`."""

    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    RESTARTING = "restarting"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class HealthStatus:
    """Snapshot de saúde do pipeline FFmpeg, consultável pelo resto do sistema."""

    state: FFmpegProcessState
    pid: int | None
    current_source: str | None
    restart_count: int
    last_error: str | None
