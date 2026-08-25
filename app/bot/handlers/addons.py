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

Dois registradores, dois clients possíveis:

- `register_management`: `/addons`, `/addon` — sempre no client de sessão
  (`SESSION_STRING`), nunca precisou de botão.
- `register_search`: `/find`, `/pick`, e o callback `play:` — chamado com o
  client escolhido por `app/bot/client.py` (o de bot, `BOT_TOKEN`, se
  configurado; senão o de sessão, mesmo comportamento de sempre). Nunca
  registrado nos dois ao mesmo tempo: duplicaria o processamento de cada
  `/pick`/clique de botão (a conta de sessão recebe toda mensagem do grupo;
  um bot recebe comandos mesmo com privacy mode padrão).

Resposta de `/find`: quando o TMDB está configurado (`TMDBService.enabled`,
verificado dentro de `AddonService.find`) e acha metadados, uma Rich Message
(`send_rich_message`, pôster/sinopse/nota) é enviada ANTES da lista, como
destaque visual. Só quando `register_search` roda no client de bot
(`buttons_enabled=True`) ela carrega `reply_markup` com um botão por addon
resolvido (via `resolve_top_candidates`/`format_stream_buttons`) — clicar
enfileira direto, sem precisar de `/pick`. O Telegram descarta
silenciosamente `reply_markup` em mensagens enviadas por uma conta de usuário
(`SESSION_STRING`), então no client de sessão a resposta sai sem botão.
Em ambos os casos a lista de texto numerada + instrução de `/pick <número>`
sempre é enviada; no client de bot ela recebe os botões mesmo sem TMDB. Os botões são a via primária de escolha, `/pick`
continua disponível como fallback secundário (útil se os botões expirarem,
sumirem da tela, ou o TMDB não confirmar o filme).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InputRichMessage, Message

from app.addon_system.exceptions import AddonError
from app.bot.formatting import (
    format_addon_info,
    format_addons_list,
    format_catalog_buttons,
    format_catalog_page,
    format_movie_card,
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
from app.utils.logging import get_logger
from app.utils.sanitize import InvalidSourceError

_ADDON_ACTIONS = {"info", "enable", "disable", "reload", "uninstall"}
_OWNER_ONLY_ACTIONS = {"enable", "disable", "reload", "uninstall"}

_PLAY_CALLBACK_PREFIX = "play:"
_MOVIE_CALLBACK_PREFIX = "movie:"
_CATALOG_CALLBACK_PREFIX = "catalog:"
_logger = get_logger("bot")


def _stop(message: Message) -> None:
    cast(Callable[[], None], message.stop_propagation)()


def _is_play_callback(_flt: Any, _client: Any, callback_query: Any) -> bool:
    """Filtro duck-typed (sem `isinstance` contra `CallbackQuery`) — testável com dublês."""
    data = getattr(callback_query, "data", None)
    return isinstance(data, str) and data.startswith(_PLAY_CALLBACK_PREFIX)


play_callback_filter = filters.create(_is_play_callback, "PlayCallbackFilter")


def _is_catalog_callback(_flt: Any, _client: Any, callback_query: Any) -> bool:
    data = getattr(callback_query, "data", None)
    return isinstance(data, str) and data.startswith(
        (_MOVIE_CALLBACK_PREFIX, _CATALOG_CALLBACK_PREFIX)
    )


catalog_callback_filter = filters.create(_is_catalog_callback, "CatalogCallbackFilter")


def register_management(
    app: Client,
    service: AddonService,
    authorized: filters.Filter,
    owner: filters.Filter,
) -> None:
    """Registra `/addons` e `/addon` (gerenciamento) — sempre no client de sessão."""

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


def register_search(
    app: Client,
    service: AddonService,
    authorized: filters.Filter,
    buttons_enabled: bool,
) -> None:
    """Registra `/find`, `/pick` e o callback `play:` em `app`.

    `buttons_enabled` indica se `app` é o client de bot (`BOT_TOKEN`) — só
    nesse caso a resposta comum de `/find` carrega botões inline (ver
    docstring do módulo).
    """

    @app.on_message(filters.command("find") & authorized)  # type: ignore[misc]
    async def _find(client: Client, message: Message) -> None:
        if message.command is None or message.text is None or len(message.command) < 2:
            await message.reply_text("Uso: `/find <busca>`")
            return
        query = message.text.split(maxsplit=1)[1].strip()
        if not hasattr(service, "search_catalog"):
            results = await service.find(query)
            metadata = service.last_metadata()
            reply_markup = None
            if buttons_enabled:
                candidates = await service.resolve_top_candidates()
                reply_markup = format_stream_buttons(candidates) if candidates else None
            if (
                results
                and metadata is not None
                and message.chat is not None
                and message.chat.id is not None
            ):
                try:
                    await client.send_rich_message(
                        message.chat.id,
                        InputRichMessage(html=format_tmdb_rich_message(results[0], metadata)),
                    )
                except Exception as exc:  # noqa: BLE001
                    _logger.warning("Falha ao enviar destaque do TMDB: {error}", error=exc)
            await message.reply_text(format_search_results(results), reply_markup=reply_markup)
            _stop(message)
            return
        if not buttons_enabled:
            await message.reply_text("O catálogo interativo exige BOT_TOKEN configurado.")
            _stop(message)
            return
        movies = await service.search_catalog(query)
        if not movies:
            await message.reply_text(
                "O TMDB não encontrou filmes ou está indisponível. Tente novamente."
            )
            _stop(message)
            return
        caption = format_catalog_page(movies, 0)
        markup = format_catalog_buttons(movies, 0)
        if movies[0].poster_url:
            await message.reply_photo(movies[0].poster_url, caption=caption, reply_markup=markup)
        else:
            await message.reply_text(caption, reply_markup=markup)
        # O _unauthorized (group=1) também casa filters.command("find"); sem
        # stop_propagation ele dispara após este handler e responde "Você não
        # tem permissão" mesmo quando o usuário está autorizado.
        _stop(message)

    @app.on_callback_query(catalog_callback_filter & authorized)  # type: ignore[misc]
    async def _catalog(client: Client, callback_query: CallbackQuery) -> None:
        data = callback_query.data if isinstance(callback_query.data, str) else ""
        if data.startswith(_CATALOG_CALLBACK_PREFIX):
            page = int(data.removeprefix(_CATALOG_CALLBACK_PREFIX))
            movies = service.catalog()
            await callback_query.edit_message_caption(
                format_catalog_page(movies, page),
                reply_markup=format_catalog_buttons(movies, page),
            )
            await callback_query.answer()
            return

        index = int(data.removeprefix(_MOVIE_CALLBACK_PREFIX))
        await callback_query.answer("Procurando as melhores fontes...")
        callback_message = callback_query.message
        chat_id = (
            callback_message.chat.id
            if callback_message is not None and callback_message.chat is not None
            else None
        )
        try:
            movie, metadata, _ = await service.select_catalog_movie(index)
            candidates = await service.resolve_top_candidates()
        except (InvalidSearchIndexError, NoStreamsAvailableError) as exc:
            if chat_id is not None:
                await client.send_message(chat_id, str(exc))
            return
        if not candidates:
            if chat_id is not None:
                await client.send_message(
                    chat_id,
                    "Nenhuma fonte disponível para esse filme.",
                )
            return
        chat = callback_message.chat if callback_message else None
        if chat is None or chat.id is None:
            return
        caption = format_movie_card(movie, metadata)
        markup = format_stream_buttons(candidates)
        if metadata.poster_url:
            await client.send_photo(
                chat.id, metadata.poster_url, caption=caption, reply_markup=markup
            )
        else:
            await client.send_message(chat.id, caption, reply_markup=markup)
        await callback_query.edit_message_reply_markup(cast(Any, None))

    @app.on_callback_query(play_callback_filter & authorized)  # type: ignore[misc]
    async def _play_candidate(client: Client, callback_query: CallbackQuery) -> None:
        data = callback_query.data
        token = data.removeprefix(_PLAY_CALLBACK_PREFIX) if isinstance(data, str) else ""
        user_id = callback_query.from_user.id if callback_query.from_user else 0
        callback_message = getattr(callback_query, "message", None)
        chat_id = callback_message.chat.id if callback_message else None
        if chat_id is not None:
            await callback_query.answer("Preparando reprodução...")
        try:
            addon_name, position = await service.pick_candidate(token, user_id)
        except InvalidSearchIndexError as exc:
            if chat_id is not None:
                await client.send_message(chat_id, str(exc))
            else:
                await callback_query.answer(str(exc), show_alert=True)
            return
        except InvalidSourceError as exc:
            if chat_id is not None:
                await client.send_message(chat_id, f"Fonte inválida: {exc}")
            else:
                await callback_query.answer(f"Fonte inválida: {exc}", show_alert=True)
            return
        except QueueFullError as exc:
            if chat_id is not None:
                await client.send_message(chat_id, f"Não foi possível adicionar: {exc}")
            else:
                await callback_query.answer(f"Não foi possível adicionar: {exc}", show_alert=True)
            return
        except (TorrentTimeoutError, TorrentResolutionError) as exc:
            if chat_id is not None:
                await client.send_message(chat_id, f"Falha ao baixar torrent: {exc}")
            else:
                await callback_query.answer(f"Falha ao baixar torrent: {exc}", show_alert=True)
            return
        if chat_id is not None:
            await client.send_message(
                chat_id, f"Adicionado à fila ({addon_name}), posição {position}."
            )
        else:
            await callback_query.answer(f"Adicionado à fila ({addon_name}), posição {position}.")
        await callback_query.edit_message_reply_markup(cast(Any, None))

    @app.on_message(filters.command("pick") & authorized)  # type: ignore[misc]
    async def _pick(_: Client, message: Message) -> None:
        if message.command is None or len(message.command) < 2:
            await message.reply_text("Uso: `/pick <número>` (depois de um /find)")
            _stop(message)
            return
        try:
            index = int(message.command[1])
        except ValueError:
            await message.reply_text("Posição inválida: precisa ser um número.")
            _stop(message)
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
        # Mesmo motivo do stop_propagation em /find: sem isso o _unauthorized
        # (group=1) dispara depois e responde "sem permissão" mesmo quando o
        # /pick já foi processado com sucesso (ou falhou por um motivo legítimo).
        _stop(message)


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
