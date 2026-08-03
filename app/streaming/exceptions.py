"""Exceções da camada de streaming."""

from __future__ import annotations


class FFmpegStreamerError(Exception):
    """Erro genérico da camada de streaming (ex.: binário FFmpeg ausente, pipes indisponíveis)."""


class FFmpegPermanentFailureError(FFmpegStreamerError):
    """FFmpeg falhou e esgotou todas as tentativas de reinício automático."""
