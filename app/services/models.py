"""Modelos agregados expostos por `services/` ao `bot/`."""

from __future__ import annotations

from dataclasses import dataclass

from app.player.models import LoopMode
from app.streaming.models import HealthStatus
from app.telegram.models import CallHealth


@dataclass(frozen=True, slots=True)
class ServiceStatus:
    """Snapshot combinado de streaming + chamada + fila, usado por `/status`."""

    streaming: HealthStatus
    call: CallHealth
    queue_length: int
    loop_mode: LoopMode
    degraded: bool
    degraded_reason: str | None
