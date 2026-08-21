"""Lista a conta atual e os chats acessíveis para configurar IDs do Telegram."""

from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv
from pyrogram import Client


async def _main(owner: str | None = None) -> None:
    load_dotenv()
    api_id = os.getenv("API_ID")
    api_hash = os.getenv("API_HASH")
    session_string = os.getenv("SESSION_STRING")
    if not api_id or not api_hash or not session_string:
        raise SystemExit("Preencha API_ID, API_HASH e SESSION_STRING no .env.")

    async with Client(
        "list_chats",
        api_id=int(api_id),
        api_hash=api_hash,
        session_string=session_string,
        in_memory=True,
    ) as app:
        me = await app.get_me()
        print(f"SESSION_ACCOUNT_ID={me.id} ({me.first_name or 'conta atual'})")
        if owner:
            owner_user = await app.get_users(owner)
            print(
                f"OWNER_USER_ID={owner_user.id} "
                f"({owner_user.first_name or owner_user.username or owner})"
            )
        else:
            print("Para obter OWNER_USER_ID, execute novamente informando @username.")
        print("\nChats acessíveis:")
        async for dialog in app.get_dialogs():
            chat = dialog.chat
            title = chat.title or chat.first_name or chat.username or "sem título"
            print(f"{chat.id}\t{chat.type.value}\t{title}")


if __name__ == "__main__":
    asyncio.run(_main(sys.argv[1] if len(sys.argv) > 1 else None))
