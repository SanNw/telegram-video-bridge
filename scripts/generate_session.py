"""Gera a `SESSION_STRING` usada pelo bot, autenticando uma conta de usuário via MTProto.

Rode uma única vez (localmente, nunca em CI/produção): pede `API_ID`/`API_HASH`
(os mesmos que vão para `.env`), depois número de telefone, código de login
enviado pelo Telegram e, se a conta tiver 2FA, a senha. Ao final, imprime a
`SESSION_STRING` pronta para colar em `.env` — nunca a salva em disco.

A conta autenticada aqui precisa poder participar da chamada de vídeo em
`CHAT_ID` (ver README, seção "Configuração"): normalmente uma conta de
usuário comum, não um bot do BotFather — chamadas de grupo (MTProto group
calls) exigem uma sessão de usuário.

Uso:
    uv run python scripts/generate_session.py
"""

from __future__ import annotations

import asyncio
import io
import os
import sys

from dotenv import load_dotenv
from pyrogram import Client

if sys.platform == "win32":
    if isinstance(sys.stdin, io.TextIOWrapper):
        sys.stdin.reconfigure(encoding="utf-8")
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


async def _main(api_id: int, api_hash: str) -> None:
    async with Client("generate_session", api_id=api_id, api_hash=api_hash, in_memory=True) as app:
        session_string = await app.export_session_string()

    print("\nSESSION_STRING gerada. Cole em .env (variável SESSION_STRING):\n")
    print(session_string)
    print(
        "\nNUNCA versione ou compartilhe este valor — ele dá acesso completo à conta "
        "autenticada, sem precisar de código de login novamente."
    )


def _prompt_credentials() -> tuple[int, str]:
    load_dotenv()
    print("Gerador de SESSION_STRING — Telegram Video Bridge")
    print("API_ID e API_HASH serão lidos do .env quando disponíveis.\n")

    api_id = os.getenv("API_ID") or input("API_ID: ").strip()
    api_hash = os.getenv("API_HASH") or input("API_HASH: ").strip()
    if not api_id or not api_hash:
        raise SystemExit("API_ID e API_HASH são obrigatórios.")
    return int(api_id), api_hash


if __name__ == "__main__":
    asyncio.run(_main(*_prompt_credentials()))
