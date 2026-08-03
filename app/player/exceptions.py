"""Exceções da camada de player (fila de reprodução)."""

from __future__ import annotations


class PlayerError(Exception):
    """Erro genérico da camada de player."""


class QueueFullError(PlayerError):
    """A fila atingiu `Settings.queue_max_items`."""


class InvalidQueueIndexError(PlayerError):
    """Índice fora dos limites da fila (ex.: `/queue` remover posição inexistente)."""
