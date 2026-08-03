"""Comandos informativos: `/start`, `/help`, `/ping`, `/version`.

Autorização: públicos (sem efeito colateral, não expõem dados operacionais).
Erros: nenhum caminho de erro — respostas são texto estático ou latência medida.
Resposta: texto simples/Markdown, sempre uma única mensagem de resposta direta.
"""

from __future__ import annotations

import time

from pyrogram import Client, filters
from pyrogram.types import Message

from app import __version__

_START_TEXT = (
    "*Telegram Video Bridge*\n"
    "Transmite vídeos autorizados para a chamada de vídeo configurada.\n\n"
    "Use /help para ver os comandos disponíveis."
)

_HELP_TEXT = (
    "*Comandos*\n"
    "/play <fonte> — adiciona uma fonte à fila (inicia a reprodução se ociosa)\n"
    "/pause — pausa a chamada\n"
    "/resume — retoma a chamada pausada\n"
    "/stop — para a reprodução atual e sai da chamada\n"
    "/skip — pula para o próximo item da fila\n"
    "/queue — lista a fila atual\n"
    "/clear — esvazia a fila pendente\n"
    "/status — mostra o status de streaming/chamada/fila\n"
    "/ping — latência do bot\n"
    "/version — versão em execução\n\n"
    "_Comandos de controle exigem autorização (whitelist de user_id)._"
)


def register(app: Client) -> None:
    """Registra `/start`, `/help`, `/ping` e `/version` em `app`."""

    @app.on_message(filters.command("start"))  # type: ignore[misc]
    async def _start(_: Client, message: Message) -> None:
        await message.reply_text(_START_TEXT)

    @app.on_message(filters.command("help"))  # type: ignore[misc]
    async def _help(_: Client, message: Message) -> None:
        await message.reply_text(_HELP_TEXT)

    @app.on_message(filters.command("ping"))  # type: ignore[misc]
    async def _ping(_: Client, message: Message) -> None:
        started = time.monotonic()
        sent = await message.reply_text("Pong!")
        elapsed_ms = (time.monotonic() - started) * 1000
        await sent.edit_text(f"Pong! `{elapsed_ms:.0f}ms`")

    @app.on_message(filters.command("version"))  # type: ignore[misc]
    async def _version(_: Client, message: Message) -> None:
        await message.reply_text(f"telegram-video-bridge v{__version__}")
