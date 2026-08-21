"""Boas-vindas na primeira conversa privada com o BotFather."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from pyrogram import Client, filters
from pyrogram.types import Message

from app.bot.auth import is_stream_admin
from app.bot.handlers.info import HELP_TEXT
from app.config.settings import Settings

_ADMIN_TEXT = (
    "*Bem-vindo ao Telerion*\n\n"
    "Você é administrador do canal e pode controlar as sessões por este chat.\n\n"
    f"{HELP_TEXT}\n\n"
    "Use /find para buscar na internet ou /canal para reproduzir um filme já enviado "
    "ao canal. O Telerion prioriza RTMP, usa a chamada normal como alternativa e apaga "
    "os arquivos temporários depois da reprodução."
)
_DENIED_TEXT = (
    "Você não é administrador do canal configurado. Por isso, nenhum comando de "
    "controle enviado aqui será executado."
)


def _load(path: Path) -> dict[str, set[int]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {key: {int(value) for value in data.get(key, [])} for key in ("admins", "denied")}
    except (OSError, ValueError, TypeError):
        return {"admins": set(), "denied": set()}


def _save(path: Path, seen: dict[str, set[int]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({key: sorted(values) for key, values in seen.items()}), encoding="utf-8"
    )
    temporary.replace(path)


def register(
    app: Client, settings: Settings, path: Path = Path("/app/data/onboarding.json")
) -> None:
    """Envia uma única apresentação por usuário e por estado de autorização."""
    seen = _load(path)
    lock = asyncio.Lock()

    @app.on_message(filters.private, group=-1)  # type: ignore[misc]
    async def _onboarding(client: Client, message: Message) -> None:
        user = message.from_user
        if user is None:
            return
        async with lock:
            authorized = await is_stream_admin(settings, client, user.id)
            bucket = "admins" if authorized else "denied"
            if user.id in seen[bucket]:
                return
            await message.reply_text(_ADMIN_TEXT if authorized else _DENIED_TEXT)
            updated = {
                key: values | ({user.id} if key == bucket else set())
                for key, values in seen.items()
            }
            _save(path, updated)
            seen[bucket].add(user.id)
            if not authorized:
                message.stop_propagation()  # type: ignore[no-untyped-call]
