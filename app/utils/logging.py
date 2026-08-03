"""Configuração de logging com Loguru: um sink por componente, rotacionado e mascarado.

Arquivos gerados em `settings.log_dir`:
    - bot.log: comandos e camada de apresentação do Telegram.
    - stream.log: player, streaming, telegram (chamada) e services.
    - ffmpeg.log: saída bruta do processo FFmpeg.
    - errors.log: todo registro de nível ERROR+ agregado, qualquer componente.

Rotação e retenção vêm de `Settings` (nunca hardcoded). `SESSION_STRING` e `API_HASH`
são redigidos de qualquer mensagem antes de chegar a um sink.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from loguru import logger

from app.config.settings import Settings

if TYPE_CHECKING:
    from loguru import Record

_configured = False

_CONSOLE_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | "
    "<cyan>{extra[component]}</cyan> | {message}"
)


def setup_logging(settings: Settings) -> None:
    """Configura os sinks do Loguru. Idempotente: chamadas repetidas são ignoradas."""
    global _configured
    if _configured:
        return

    settings.log_dir.mkdir(parents=True, exist_ok=True)

    secrets = {
        value
        for value in (
            settings.api_hash.get_secret_value(),
            settings.session_string.get_secret_value(),
        )
        if value
    }

    def _redact_patcher(record: "Record") -> None:
        message = record["message"]
        for secret in secrets:
            if secret in message:
                message = message.replace(secret, "***MASKED***")
        record["message"] = message

    logger.remove()
    logger.configure(extra={"component": "app"}, patcher=_redact_patcher)

    logger.add(
        sys.stderr,
        level=settings.log_level,
        colorize=True,
        format=_CONSOLE_FORMAT,
    )

    def _component_is(*components: str) -> "type":
        def _filter(record: "Record") -> bool:
            return record["extra"].get("component") in components

        return _filter  # type: ignore[return-value]

    logger.add(
        settings.log_dir / "bot.log",
        level=settings.log_level,
        rotation=settings.log_rotation,
        retention=settings.log_retention,
        filter=_component_is("bot"),
        backtrace=False,
        diagnose=False,
    )
    logger.add(
        settings.log_dir / "stream.log",
        level=settings.log_level,
        rotation=settings.log_rotation,
        retention=settings.log_retention,
        filter=_component_is("streaming", "telegram", "player", "services"),
        backtrace=False,
        diagnose=False,
    )
    logger.add(
        settings.log_dir / "ffmpeg.log",
        level="DEBUG",
        rotation=settings.log_rotation,
        retention=settings.log_retention,
        filter=_component_is("ffmpeg"),
        backtrace=False,
        diagnose=False,
    )
    logger.add(
        settings.log_dir / "errors.log",
        level="ERROR",
        rotation=settings.log_rotation,
        retention=settings.log_retention,
        backtrace=True,
        diagnose=False,
    )

    _configured = True


def get_logger(component: str) -> "logger.__class__":  # type: ignore[name-defined]
    """Retorna um logger vinculado a `component`, roteado para o sink correspondente."""
    return logger.bind(component=component)


def reset_logging_state() -> None:
    """Permite reconfigurar o logging (uso exclusivo de testes)."""
    global _configured
    _configured = False
