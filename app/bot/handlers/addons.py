"""Comandos de addons: `/addons`, `/addon <ação> <nome>`, `/find`, `/pick`.

Autorização: exigida (gerencia extensões de terceiros e revela fontes de mídia).

Erros: addon inexistente ou falha ao carregar/recarregar respondem com o
motivo (`AddonError`, que cobre `AddonNotFoundError`/`AddonLoadError`/
`AddonManifestError`). `/pick` com posição fora da última busca responde com
o motivo (`InvalidSearchIndexError`); sem streams disponíveis, idem
(`NoStreamsAvailableError`); fonte resolvida mas inválida ou fila cheia
reutilizam os mesmos erros de `/play` (`InvalidSourceError`/`QueueFullError`).

Resposta: sempre uma única mensagem de texto confirmando a ação, listando
resultados, ou explicando o erro.
"""

from __future__ import annotations

from pyrogram import Client, filters
from pyrogram.types import Message

from app.addon_system.exceptions import AddonError
from app.bot.formatting import format_addon_info, format_addons_list, format_search_results
from app.player.exceptions import QueueFullError
from app.services.addon_service import AddonService
from app.services.exceptions import InvalidSearchIndexError, NoStreamsAvailableError
from app.utils.sanitize import InvalidSourceError

_ADDON_ACTIONS = {"info", "enable", "disable", "reload", "uninstall"}


def register(app: Client, service: AddonService, authorized: filters.Filter) -> None:
    """Registra os comandos de addons em `app`, restritos por `authorized`."""

    @app.on_message(filters.command("addons") & authorized)  # type: ignore[misc]
    async def _addons(_: Client, message: Message) -> None:
        await message.reply_text(format_addons_list(service.list_addons()))

    @app.on_message(filters.command("addon") & authorized)  # type: ignore[misc]
    async def _addon(_: Client, message: Message) -> None:
        if message.command is None or len(message.command) < 2:
            await message.reply_text("Uso: `/addon <info|enable|disable|reload|uninstall> <nome>`")
            return
        action = message.command[1].lower()
        args = message.command[2:]

        if action not in _ADDON_ACTIONS:
            await message.reply_text(
                "Ação desconhecida. Use: `info`, `enable`, `disable`, `reload` ou `uninstall`."
            )
            return
        if not args:
            await message.reply_text(f"Uso: `/addon {action} <nome>`")
            return
        name = args[0]

        try:
            await _run_addon_action(service, action, name, message)
        except AddonError as exc:
            await message.reply_text(str(exc))

    @app.on_message(filters.command("find") & authorized)  # type: ignore[misc]
    async def _find(_: Client, message: Message) -> None:
        if message.command is None or message.text is None or len(message.command) < 2:
            await message.reply_text("Uso: `/find <busca>`")
            return
        query = message.text.split(maxsplit=1)[1].strip()
        results = await service.find(query)
        await message.reply_text(format_search_results(results))

    @app.on_message(filters.command("pick") & authorized)  # type: ignore[misc]
    async def _pick(_: Client, message: Message) -> None:
        if message.command is None or len(message.command) < 2:
            await message.reply_text("Uso: `/pick <número>` (depois de um /find)")
            return
        try:
            index = int(message.command[1])
        except ValueError:
            await message.reply_text("Posição inválida: precisa ser um número.")
            return
        user_id = message.from_user.id if message.from_user else 0
        try:
            position = await service.pick(index, user_id)
        except (InvalidSearchIndexError, NoStreamsAvailableError) as exc:
            await message.reply_text(str(exc))
        except InvalidSourceError as exc:
            await message.reply_text(f"Fonte inválida: {exc}")
        except QueueFullError as exc:
            await message.reply_text(f"Não foi possível adicionar: {exc}")
        else:
            await message.reply_text(f"Adicionado à fila na posição {position}.")


async def _run_addon_action(
    service: AddonService, action: str, name: str, message: Message
) -> None:
    if action == "info":
        info = service.addon_info(name)
        health = await service.addon_health(name)
        await message.reply_text(format_addon_info(info, health))
    elif action == "enable":
        await service.enable(name)
        await message.reply_text(f"Addon habilitado: `{name}`.")
    elif action == "disable":
        await service.disable(name)
        await message.reply_text(f"Addon desabilitado: `{name}`.")
    elif action == "reload":
        await service.reload(name)
        await message.reply_text(f"Addon recarregado: `{name}`.")
    elif action == "uninstall":
        await service.uninstall(name)
        await message.reply_text(f"Addon removido: `{name}`.")
