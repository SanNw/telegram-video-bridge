"""Exceções da camada de orquestração."""

from __future__ import annotations


class ServiceError(Exception):
    """Erro genérico da camada de serviço."""


class NothingPlayingError(ServiceError):
    """Comando de controle de reprodução (pause/resume/skip/stop) sem reprodução ativa."""


class InvalidVolumeError(ServiceError):
    """Volume fora do intervalo permitido (0-200)."""


class InvalidSearchIndexError(ServiceError):
    """`/pick <índice>` fora dos limites da última busca de addons (ou nenhuma busca feita)."""


class NoStreamsAvailableError(ServiceError):
    """O addon não devolveu nenhuma fonte reproduzível para o resultado escolhido."""
