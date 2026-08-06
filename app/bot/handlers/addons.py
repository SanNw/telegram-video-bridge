"""Comandos de addons: `/addons`, `/addon <ação> <nome>`, `/find`, `/pick`.

Autorização: exigida (revela fontes de mídia). As ações de gerenciamento de
`/addon` (`enable`, `disable`, `reload`, `uninstall`) exigem adicionalmente
`owner_user_id` (`OWNER_USER_ID` em `.env`) — elas carregam/descarregam
código de terceiro no mesmo processo que tem a `SESSION_STRING`, um risco
maior do que apenas listar/buscar mídia. `info` (somente leitura) segue
disponível a qualquer usuário autorizado.

Erros: addon inexistente ou falha ao carregar/recarregar respondem com o
motivo (`AddonError`, que cobre `AddonNotFoundError`/`AddonLoadError`/
`AddonManifestError`). `/pick` com posição fora da última busca responde com
o motivo (`InvalidSearchIndexError`); sem streams disponíveis, idem
(`NoStreamsAvailableError`, que também cobre todos os candidatos torrent
expirando em `/pick`); fonte resolvida mas inválida ou fila cheia reutilizam
os mesmos erros de `/play` (`InvalidSourceError`/`QueueFullError`). O botão
de `/find` (`pick_candidate`, um único candidato por addon, sem fallback)
responde com o motivo também para `TorrentTimeoutError`/
`TorrentResolutionError` — falhas específicas de resolver um torrent via
qBittorrent.

Resposta: `/find` manda uma única mensagem. Quando o TMDB está configurado
(`TMDBService.enabled`) e acha metadados para o primeiro resultado, a
resposta é uma Rich Message (`send_rich_message`) com pôster/sinopse/nota —
substituindo a lista de texto puro, não somando a ela. Essa Rich Message vem
com um botão inline por addon que resolveu um stream reproduzível dentro do
limite de 4GB do Telegram para o resultado
(`AddonService.resolve_top_candidates`); clicar enfileira direto via
`pick_candidate`, sem precisar de `/pick <número>`. Quando o TMDB está
desabilitado ou não acha o filme, cai no fallback de sempre: lista de texto
numerada + instrução de `/pick <número>`. Comandos sem resultado nenhum
(`find` sem match) e demais handlers (`/addons`, `/addon`, `/pick`) sempre
respondem com uma única mensagem de texto confirmando a ação, listando
resultados, ou explicando o erro.
"""

from __future__ import annotations

from typing import Any

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InputRichMessage, Message

from app.addon_system.exceptions import AddonError
from app.bot.formatting import (
    format_addon_info,
    format_addons_list,
    format_search_results,
    format_stream_buttons,
    format_tmdb_rich_message,
)
from app.player.exceptions import QueueFullError
from app.services.addon_service import AddonService
from app.services.exceptions import (
    InvalidSearchIndexError,
    NoStreamsAvailableError,
    TorrentResolutionError,
    TorrentTimeoutError,
)
from app.services.tmdb_service import TMDBService
from app.utils.sanitize import InvalidSourceError

_ADDON_ACTIONS = {"info", "enable", "disable", "reload", "uninstall"}
_OWNER_ONLY_ACTIONS = {"enable", "disable", "reload", "uninstall"}

_PLAY_CALLBACK_PREFIX = "play:"


def _is_play_callback(_flt: Any, _client: Any, callback_query: Any) -> bool:
    """Filtro duck-typed (sem `isinstance` contra `CallbackQuery`) — testável com dublês."""
    data = getattr(callback_query, "data", None)
    return isinstance(data, str) and data.startswith(_PLAY_CALLBACK_PREFIX)


play_callback_filter = filters.create(_is_play_callback, "PlayCallbackFilter")


def register(
    app: Client,
    service: AddonService,
    authorized: filters.Filter,
    owner: filters.Filter,
    tmdb_service: TMDBService,
) -> None:
    """Registra os comandos de addons em `app`.

    `authorized` controla `/addons`, `/find`, `/pick` e `/addon info`;
    `owner` controla as ações de gerenciamento de `/addon` (ver docstring do
    módulo). `tmdb_service` enriquece `/find` com uma Rich Message quando
    habilitado (ver docstring do módulo).
    """

    @app.on_message(filters.command("addons") & authorized)  # type: ignore[misc]
    async def _addons(_: Client, message: Message) -> None:
        await message.reply_text(format_addons_list(service.list_addons()))

    @app.on_message(filters.command("addon") & authorized)  # type: ignore[misc]
    async def _addon(client: Client, message: Message) -> None:
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
        if action in _OWNER_ONLY_ACTIONS and not await owner(client, message):
            await message.reply_text(
                "Só o operador do bot pode gerenciar addons (enable/disable/reload/uninstall)."
            )
            return
        name = args[0]

        try:
            await _run_addon_action(service, action, name, message)
        except AddonError as exc:
            await message.reply_text(str(exc))

    @app.on_message(filters.command("find") & authorized)  # type: ignore[misc]
    async def _find(client: Client, message: Message) -> None:
        if message.command is None or message.text is None or len(message.command) < 2:
            await message.reply_text("Uso: `/find <busca>`")
            return
        query = message.text.split(maxsplit=1)[1].strip()
        results = await service.find(query)
        if not results:
            await message.reply_text(format_search_results(results))
            message.stop_propagation()  # type: ignore[no-untyped-call]
            return

        chat = message.chat
        metadata = None
        first = results[0]
        if tmdb_service.enabled and chat is not None and chat.id is not None:
            # `query` (termo digitado pelo usuário) é um termo de busca muito
            # mais confiável pro TMDB que `first.title` (título cru retornado
            # por um addon — ex.: "POEIRA NA POMBA - Conheça os personagens"
            # para a busca "homem de ferro").
            metadata = await tmdb_service.enrich(query, first.year)

        if metadata is not None and chat is not None and chat.id is not None:
            # TMDB confirmou o filme: a Rich Message (sinopse/pôster/nota) +
            # os botões dos melhores candidatos substituem a lista de texto
            # puro — não faz sentido mostrar as duas.
            candidates = await service.resolve_top_candidates()
            reply_markup = format_stream_buttons(candidates) if candidates else None
            await client.send_rich_message(
                chat.id,
                InputRichMessage(html=format_tmdb_rich_message(first, metadata)),
                reply_markup=reply_markup,
            )
        else:
            # TMDB desabilitado ou não achou o filme: cai no fallback de
            # sempre — lista de texto puro + `/pick <número>`.
            await message.reply_text(format_search_results(results))
        # O _unauthorized (group=1) também casa filters.command("find"); sem
        # stop_propagation ele dispara após este handler e responde "Você não
        # tem permissão" mesmo quando o usuário está autorizado.
        message.stop_propagation()  # type: ignore[no-untyped-call]

    @app.on_callback_query(play_callback_filter & authorized)  # type: ignore[misc]
    async def _play_candidate(_: Client, callback_query: CallbackQuery) -> None:
        data = callback_query.data
        token = data.removeprefix(_PLAY_CALLBACK_PREFIX) if isinstance(data, str) else ""
        user_id = callback_query.from_user.id if callback_query.from_user else 0
        try:
            addon_name, position = await service.pick_candidate(token, user_id)
        except InvalidSearchIndexError as exc:
            await callback_query.answer(str(exc), show_alert=True)
            return
        except InvalidSourceError as exc:
            await callback_query.answer(f"Fonte inválida: {exc}", show_alert=True)
            return
        except QueueFullError as exc:
            await callback_query.answer(f"Não foi possível adicionar: {exc}", show_alert=True)
            return
        except (TorrentTimeoutError, TorrentResolutionError) as exc:
            await callback_query.answer(
                f"Falha ao baixar torrent: {exc}. Use /find de novo.", show_alert=True
            )
            return
        await callback_query.answer(f"Adicionado à fila ({addon_name}), posição {position}.")
        await callback_query.edit_message_reply_markup(None)  # type: ignore[arg-type]

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
        except TorrentResolutionError as exc:
            await message.reply_text(f"Falha ao baixar torrent: {exc}")
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
