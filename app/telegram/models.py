"""Modelos de estado da camada de gerenciamento de chamada."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CallState(str, Enum):
    """Estado da chamada gerenciada por `TelegramCallManager`."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class CallHealth:
    """Snapshot de saúde da chamada, consultável pelo resto do sistema (ex.: `/status`)."""

    state: CallState
    chat_id: int
    reconnect_count: int
    last_error: str | None
