"""Modelos da fila de reprodução: itens, estado e modos de loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from app.utils.sanitize import MediaSource, SourceType


class LoopMode(StrEnum):
    """Modo de repetição da fila."""

    OFF = "off"
    ITEM = "item"
    QUEUE = "queue"


@dataclass(slots=True)
class QueueItem:
    """Um item da fila: fonte já validada + metadados de auditoria."""

    source: MediaSource
    requested_by: int
    added_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_raw": self.source.raw,
            "source_type": self.source.type.value,
            "requested_by": self.requested_by,
            "added_at": self.added_at.isoformat(),
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> QueueItem:
        return QueueItem(
            source=MediaSource(raw=str(data["source_raw"]), type=SourceType(data["source_type"])),
            requested_by=int(data["requested_by"]),
            added_at=datetime.fromisoformat(str(data["added_at"])),
        )


@dataclass(slots=True)
class PlaybackState:
    """Estado completo persistido em disco: fila, item atual e modo de loop."""

    items: list[QueueItem] = field(default_factory=list)
    current: QueueItem | None = None
    loop_mode: LoopMode = LoopMode.OFF

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [item.to_dict() for item in self.items],
            "current": self.current.to_dict() if self.current is not None else None,
            "loop_mode": self.loop_mode.value,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> PlaybackState:
        items_raw = data.get("items")
        if not isinstance(items_raw, list):
            raise ValueError("Campo 'items' ausente ou inválido.")
        current_raw = data.get("current")
        return PlaybackState(
            items=[QueueItem.from_dict(item) for item in items_raw],
            current=QueueItem.from_dict(current_raw) if current_raw else None,
            loop_mode=LoopMode(data.get("loop_mode", LoopMode.OFF.value)),
        )
