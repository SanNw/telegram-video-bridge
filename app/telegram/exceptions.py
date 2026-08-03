"""Exceções da camada de gerenciamento de chamada."""

from __future__ import annotations


class TelegramCallError(Exception):
    """Erro genérico da camada de gerenciamento de chamada."""


class CallPermanentFailureError(TelegramCallError):
    """A reconexão da chamada esgotou todas as tentativas configuradas."""
