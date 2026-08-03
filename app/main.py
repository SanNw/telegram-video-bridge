"""Ponto de entrada da aplicação.

Monta `PlaybackService` + `bot/`, inicia tudo e aguarda SIGINT/SIGTERM para um
shutdown gracioso. Qualquer exceção não capturada em uma task de background é
logada em `errors.log` (nunca derruba o processo) via `loop.set_exception_handler`.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys
from typing import Any

from app.bot.client import build_bot
from app.config.settings import get_settings
from app.services.playback_service import PlaybackService
from app.utils.logging import get_logger, setup_logging

_logger = get_logger("services")


def _handle_task_exception(_loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
    exception = context.get("exception")
    message = context.get("message", "Exceção não tratada em task de background.")
    if exception is not None:
        _logger.opt(exception=exception).error(message)
    else:
        _logger.error(message)


async def _run() -> None:
    settings = get_settings()
    setup_logging(settings)

    asyncio.get_running_loop().set_exception_handler(_handle_task_exception)

    service = PlaybackService(settings)
    build_bot(service, settings)

    await service.start()
    _logger.info("Telegram Video Bridge em execução.")

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # Plataforma sem suporte a add_signal_handler (ex.: Windows); Ctrl+C ainda funciona.
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop_event.set)

    try:
        await stop_event.wait()
    finally:
        _logger.info("Encerrando...")
        await service.shutdown()


def main() -> None:
    """Instala uvloop (Linux) e executa a aplicação até receber SIGINT/SIGTERM."""
    if sys.platform != "win32":
        import uvloop

        uvloop.install()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
